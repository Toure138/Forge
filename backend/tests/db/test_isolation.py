"""Row-level security leak tests (constitution X; AC-42 to AC-45), as `forge_app`.

Every query here is written WITHOUT an organization filter, on purpose: only RLS stands
between Acme's context and Globex's rows.
"""

import uuid
from dataclasses import dataclass

import psycopg
import pytest
from psycopg import errors, sql

from tests.conftest import connect_test_database
from tests.db.conftest import (
    ORG_TABLES,
    TwoOrgs,
    insert,
    insert_user,
    org_row,
    set_context,
)


@dataclass(frozen=True)
class Writes:
    """A harmless unfiltered UPDATE, and which writes forge_app is granted on the table."""

    update_set: str
    can_update: bool
    can_delete: bool


WRITES = {
    "organizations": Writes("name = 'hacked'", can_update=True, can_delete=False),
    "memberships": Writes("role = 'guest'", can_update=True, can_delete=True),
    "invitations": Writes("status = 'revoked'", can_update=True, can_delete=False),
    "conversations": Writes("agent_name = 'hacked'", can_update=False, can_delete=False),
    "audit_events": Writes("action = 'hacked'", can_update=False, can_delete=False),
}
assert WRITES.keys() == ORG_TABLES.keys()

TABLES = sorted(ORG_TABLES)


def org_values(conn: psycopg.Connection, table: str) -> list[uuid.UUID]:
    """Organization of every row of `table` visible to `conn`, with no filter."""
    query = sql.SQL("SELECT {} FROM {}").format(
        sql.Identifier(ORG_TABLES[table]), sql.Identifier(table)
    )
    return [row[0] for row in conn.execute(query).fetchall()]


def fingerprint(conn: psycopg.Connection, table: str) -> list[tuple[object, ...]]:
    query = sql.SQL("SELECT * FROM {} ORDER BY 1, 2").format(sql.Identifier(table))
    return conn.execute(query).fetchall()


@pytest.mark.parametrize("table", TABLES)
def test_unfiltered_select_sees_only_active_org(
    two_orgs: TwoOrgs, pg_app: psycopg.Connection, table: str
) -> None:
    set_context(pg_app, org=two_orgs.acme, user=two_orgs.alice)
    assert org_values(pg_app, table) == [two_orgs.acme]
    pg_app.rollback()

    set_context(pg_app, org=two_orgs.globex, user=two_orgs.gina)
    assert org_values(pg_app, table) == [two_orgs.globex]


@pytest.mark.parametrize("table", TABLES)
def test_unfiltered_update_delete_cannot_touch_other_org(
    two_orgs: TwoOrgs, pg_app: psycopg.Connection, table: str
) -> None:
    writes = WRITES[table]
    set_context(pg_app, org=two_orgs.globex, user=two_orgs.gina)
    globex_before = fingerprint(pg_app, table)
    pg_app.rollback()

    update = sql.SQL("UPDATE {} SET " + writes.update_set).format(sql.Identifier(table))
    delete = sql.SQL("DELETE FROM {}").format(sql.Identifier(table))
    for statement, allowed in ((update, writes.can_update), (delete, writes.can_delete)):
        set_context(pg_app, org=two_orgs.acme, user=two_orgs.alice)
        if allowed:
            cursor = pg_app.execute(statement)
            assert cursor.rowcount == 1  # Acme's row only
            pg_app.commit()
        else:
            with pytest.raises(errors.InsufficientPrivilege, match="permission denied"):
                pg_app.execute(statement)
            pg_app.rollback()

    set_context(pg_app, org=two_orgs.globex, user=two_orgs.gina)
    assert fingerprint(pg_app, table) == globex_before


@pytest.mark.parametrize("table", TABLES)
def test_insert_or_move_row_into_other_org_is_rejected(
    two_orgs: TwoOrgs, pg_app: psycopg.Connection, table: str
) -> None:
    acme, globex = two_orgs.acme, two_orgs.globex
    org_column = ORG_TABLES[table]

    # Insert, from Acme's context, a row that belongs to Globex (or to a new organization).
    set_context(pg_app, org=acme, user=two_orgs.alice)
    target = uuid.uuid4() if table == "organizations" else globex
    with pytest.raises(errors.InsufficientPrivilege, match="row-level security"):
        insert(pg_app, table, org_row(table, target, two_orgs.alice))
    pg_app.rollback()

    # Move Acme's row to Globex.
    set_context(pg_app, org=acme, user=two_orgs.alice)
    move = sql.SQL("UPDATE {} SET {} = %s").format(
        sql.Identifier(table), sql.Identifier(org_column)
    )
    expected = "row-level security" if WRITES[table].can_update else "permission denied"
    with pytest.raises(errors.InsufficientPrivilege, match=expected):
        pg_app.execute(move, (globex,))
    pg_app.rollback()

    set_context(pg_app, org=globex, user=two_orgs.gina)
    assert org_values(pg_app, table) == [globex]


@pytest.mark.parametrize("table", TABLES)
def test_no_context_returns_no_rows(two_orgs: TwoOrgs, table: str) -> None:
    with connect_test_database("TEST_DATABASE_URL") as fresh:  # never had any context
        assert org_values(fresh, table) == []
        with pytest.raises(errors.InsufficientPrivilege, match="row-level security"):
            insert(fresh, table, org_row(table, two_orgs.acme, two_orgs.alice))
        fresh.rollback()


def test_reused_connection_after_transaction_returns_no_rows(
    two_orgs: TwoOrgs, pg_app: psycopg.Connection
) -> None:
    set_context(pg_app, org=two_orgs.acme, user=two_orgs.alice)
    assert org_values(pg_app, "organizations") == [two_orgs.acme]
    pg_app.commit()

    # Same connection, next transaction: the local settings now read '' (not NULL).
    settings = pg_app.execute(
        "SELECT current_setting('app.organization_id', true), current_setting('app.user_id', true)"
    ).fetchone()
    assert settings == ("", "")
    for table in TABLES:
        assert org_values(pg_app, table) == [], table


def test_conversation_visible_only_to_its_owner_user(
    two_orgs: TwoOrgs, pg_app: psycopg.Connection
) -> None:
    bob = insert_user(pg_app, "Bob")
    set_context(pg_app, org=two_orgs.acme, user=bob)
    insert(pg_app, "memberships", org_row("memberships", two_orgs.acme, bob, role="admin"))
    pg_app.commit()

    # Same organization, another user (even an Admin): Alice's conversation is invisible...
    set_context(pg_app, org=two_orgs.acme, user=bob)
    assert org_values(pg_app, "conversations") == []
    # ... and Bob cannot create a conversation in Alice's name.
    with pytest.raises(errors.InsufficientPrivilege, match="row-level security"):
        insert(pg_app, "conversations", org_row("conversations", two_orgs.acme, two_orgs.alice))
    pg_app.rollback()

    set_context(pg_app, org=two_orgs.acme, user=two_orgs.alice)
    assert org_values(pg_app, "conversations") == [two_orgs.acme]
