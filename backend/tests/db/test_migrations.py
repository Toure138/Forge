"""Alembic migrations against `forge_test`: tables, constraints, indexes, downgrade.

Constraints are checked by behaviour, on TEMP copies of the tables (`LIKE … INCLUDING ALL`
copies CHECK, NOT NULL, unique and partial indexes, but neither foreign keys nor RLS), so these
tests keep working once the organization tables are under FORCE ROW LEVEL SECURITY.
"""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest
from alembic import command as alembic_command
from alembic.autogenerate import compare_metadata
from alembic.config import Config as AlembicConfig
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from psycopg import errors, sql
from psycopg.types.json import Jsonb
from sqlalchemy import create_engine, pool

from app.db.tables import metadata
from tests.conftest import database_url_from_env

EXPECTED_TABLES = {
    "users",
    "sessions",
    "login_requests",
    "organizations",
    "memberships",
    "invitations",
    "conversations",
    "audit_events",
}
NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)


def public_tables(conn: psycopg.Connection) -> set[str]:
    rows = conn.execute("SELECT tablename FROM pg_tables WHERE schemaname = 'public'").fetchall()
    return {r[0] for r in rows}


def base_row(table: str) -> dict[str, Any]:
    """A valid row for `table` (foreign keys are not copied to the TEMP table)."""
    org, user = uuid.uuid4(), uuid.uuid4()
    rows: dict[str, dict[str, Any]] = {
        "users": {
            "id": uuid.uuid4(),
            "google_sub": "sub-1",
            "email": "Alice@Example.com",
            "email_normalized": "alice@example.com",
            "name": "Alice",
            "picture_url": None,
            "created_at": NOW,
            "last_login_at": NOW,
        },
        "organizations": {"id": org, "name": "Acme", "created_at": NOW},
        "memberships": {
            "organization_id": org,
            "user_id": user,
            "role": "member",
            "created_at": NOW,
            "updated_at": NOW,
        },
        "invitations": {
            "id": uuid.uuid4(),
            "organization_id": org,
            "email_normalized": "bob@example.com",
            "role": "member",
            "invited_by": user,
            "created_at": NOW,
            "expires_at": NOW + timedelta(days=7),
            "status": "pending",
            "decided_at": None,
            "decided_by": None,
        },
        "audit_events": {
            "organization_id": org,
            "occurred_at": NOW,
            "actor_user_id": user,
            "action": "organization.created",
            "target_type": "organization",
            "target_id": str(org),
            "outcome": "success",
            "details": Jsonb({}),
        },
    }
    return rows[table]


class TempTable:
    """TEMP copy of a real table, with its constraints and indexes, for behavioural checks."""

    def __init__(self, conn: psycopg.Connection, table: str) -> None:
        self.conn, self.table, self.name = conn, table, f"tmp_{table}"
        conn.execute(
            sql.SQL("CREATE TEMP TABLE {} (LIKE {} INCLUDING ALL)").format(
                sql.Identifier(self.name), sql.Identifier(table)
            )
        )

    def insert(self, **overrides: Any) -> None:
        row = base_row(self.table) | overrides
        query = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
            sql.Identifier(self.name),
            sql.SQL(", ").join(map(sql.Identifier, row)),
            sql.SQL(", ").join(sql.Placeholder() * len(row)),
        )
        self.conn.execute(query, list(row.values()))

    def rejects(self, error: type[Exception], *rows: dict[str, Any]) -> bool:
        """True if inserting `rows` (in one rolled-back savepoint) raises `error`."""
        try:
            with self.conn.transaction(force_rollback=True):
                for row in rows:
                    self.insert(**row)
        except error:
            return True
        return False


def test_upgrade_creates_expected_tables(
    migrated_db: AlembicConfig, pg_owner: psycopg.Connection
) -> None:
    assert public_tables(pg_owner) == EXPECTED_TABLES | {"alembic_version"}

    # users: google_sub unique; email_normalized indexed but NOT unique (FR-33: emails change).
    users = TempTable(pg_owner, "users")
    assert users.rejects(errors.UniqueViolation, {}, {"id": uuid.uuid4()})
    assert not users.rejects(Exception, {}, {"id": uuid.uuid4(), "google_sub": "sub-2"}), (
        "two accounts may share an email_normalized"
    )
    email_indexes = pg_owner.execute(
        """
        SELECT i.indisunique FROM pg_index i
        JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY (i.indkey)
        WHERE i.indrelid = 'users'::regclass AND a.attname = 'email_normalized'
        """
    ).fetchall()
    assert email_indexes == [(False,)]

    # invitations: one pending invitation per (organization, email); others are history.
    inv = TempTable(pg_owner, "invitations")
    org = uuid.uuid4()
    pending = {"organization_id": org, "status": "pending"}
    assert inv.rejects(errors.UniqueViolation, pending, pending | {"id": uuid.uuid4()})
    assert not inv.rejects(
        Exception, pending, {"organization_id": org, "status": "revoked", "id": uuid.uuid4()}
    )
    assert not inv.rejects(
        Exception, pending, pending | {"id": uuid.uuid4(), "organization_id": uuid.uuid4()}
    )

    # CHECK constraints: roles, statuses, outcome, target type, organization name length.
    members = TempTable(pg_owner, "memberships")
    for role in ("owner", "admin", "member", "guest"):
        assert not members.rejects(Exception, {"role": role})
        assert not inv.rejects(Exception, {"role": role})
    assert members.rejects(errors.CheckViolation, {"role": "superadmin"})
    assert members.rejects(errors.CheckViolation, {"role": "Owner"})
    assert inv.rejects(errors.CheckViolation, {"role": "root"})
    for status in ("pending", "accepted", "declined", "revoked", "expired"):
        assert not inv.rejects(Exception, {"status": status})
    assert inv.rejects(errors.CheckViolation, {"status": "done"})

    orgs = TempTable(pg_owner, "organizations")
    assert not orgs.rejects(Exception, {"name": "x"})
    assert not orgs.rejects(Exception, {"name": "x" * 100})
    for bad in ("", "   ", "x" * 101):
        assert orgs.rejects(errors.CheckViolation, {"name": bad}), repr(bad)

    audit = TempTable(pg_owner, "audit_events")
    assert not audit.rejects(Exception, {"outcome": "denied", "target_type": None})
    assert audit.rejects(errors.CheckViolation, {"outcome": "maybe"})
    assert audit.rejects(errors.CheckViolation, {"target_type": "user"})


def current_revision(conn: psycopg.Connection) -> str | None:
    row = conn.execute("SELECT version_num FROM alembic_version").fetchone()
    return row[0] if row else None


def test_upgrade_downgrade_roundtrip(
    migrated_db: AlembicConfig, pg_owner: psycopg.Connection
) -> None:
    head = ScriptDirectory.from_config(migrated_db).get_current_head()
    assert head is not None
    pg_owner.autocommit = True

    alembic_command.downgrade(migrated_db, "base")
    assert public_tables(pg_owner) == {"alembic_version"}
    assert current_revision(pg_owner) is None

    alembic_command.upgrade(migrated_db, "head")
    assert public_tables(pg_owner) == EXPECTED_TABLES | {"alembic_version"}
    assert current_revision(pg_owner) == head


def test_migrations_match_table_definitions(migrated_db: AlembicConfig) -> None:
    """`app/db/tables.py` (used by the code) and the migrations (the real schema) agree."""
    engine = create_engine(
        database_url_from_env("TEST_DATABASE_OWNER_URL"), poolclass=pool.NullPool
    )
    try:
        with engine.connect() as conn:
            diff = compare_metadata(MigrationContext.configure(conn), metadata)
    finally:
        engine.dispose()
    assert diff == []


@pytest.mark.parametrize("table", sorted(EXPECTED_TABLES))
def test_timestamps_have_no_server_default(
    migrated_db: AlembicConfig, pg_owner: psycopg.Connection, table: str
) -> None:
    """Dates come from the injected clock (plan T14), never from a `DEFAULT now()`."""
    defaults = pg_owner.execute(
        """
        SELECT column_name, column_default FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s AND column_default IS NOT NULL
          AND data_type LIKE 'timestamp%%'
        """,
        (table,),
    ).fetchall()
    assert defaults == []
