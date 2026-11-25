"""Cross-organization « my … » reads (ADR-0003 point 3; socle of AC-17, AC-21 to AC-24).

The three SECURITY DEFINER functions run as `forge_self_reader`, which does not bypass RLS:
its own read policies only show the current user's memberships, the invitations addressed to
the current user's email, and the matching organizations.
"""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import timedelta

import psycopg
import pytest

from tests.conftest import connect_test_database
from tests.db.conftest import NOW, TwoOrgs, insert, insert_user, org_row, set_context


@dataclass(frozen=True)
class World:
    """Alice: owner of Acme, member of Globex. Gina: owner of Globex. Bob: invited only."""

    orgs: TwoOrgs
    bob: uuid.UUID
    carol: uuid.UUID
    bob_acme_invitation: uuid.UUID  # pending, expires NOW + 7 days, invited by Alice
    bob_globex_invitation: uuid.UUID  # pending, expires NOW + 1 day, invited by Gina
    bob_declined_invitation: uuid.UUID  # declined (history), Acme


@pytest.fixture
def world(two_orgs: TwoOrgs) -> Iterator[World]:
    acme, globex, alice, gina = two_orgs.acme, two_orgs.globex, two_orgs.alice, two_orgs.gina
    ids = [uuid.uuid4() for _ in range(3)]
    with connect_test_database("TEST_DATABASE_URL") as conn:
        bob = insert_user(conn, "Bob", "Bob@Example.com")
        carol = insert_user(conn, "Carol")
        conn.commit()

        set_context(conn, org=acme, user=alice)
        for invitation_id, status in ((ids[0], "pending"), (ids[2], "declined")):
            insert(
                conn,
                "invitations",
                org_row(
                    "invitations",
                    acme,
                    alice,
                    id=invitation_id,
                    email_normalized="bob@example.com",
                    status=status,
                ),
            )
        conn.commit()

        set_context(conn, org=globex, user=gina)
        insert(conn, "memberships", org_row("memberships", globex, alice, role="member"))
        insert(
            conn,
            "invitations",
            org_row(
                "invitations",
                globex,
                gina,
                id=ids[1],
                email_normalized="bob@example.com",
                expires_at=NOW + timedelta(days=1),
            ),
        )
        conn.commit()
    yield World(two_orgs, bob, carol, *ids)


def call(conn: psycopg.Connection, query: str, *params: object) -> set[tuple[object, ...]]:
    return set(conn.execute(query, params).fetchall())


def test_my_memberships_lists_only_current_user_across_orgs(
    world: World, pg_app: psycopg.Connection
) -> None:
    o = world.orgs
    set_context(pg_app, user=o.alice, email="alice@example.com")
    alice_rows = {(o.acme, "Acme", "owner"), (o.globex, "Globex", "member")}
    assert call(pg_app, "SELECT * FROM app_my_memberships()") == alice_rows
    pg_app.rollback()

    # The active organization does not narrow it down: it is a cross-organization read.
    set_context(pg_app, org=o.acme, user=o.alice, email="alice@example.com")
    assert call(pg_app, "SELECT * FROM app_my_memberships()") == alice_rows
    pg_app.rollback()

    set_context(pg_app, user=o.gina, email="gina@example.com")
    assert call(pg_app, "SELECT * FROM app_my_memberships()") == {(o.globex, "Globex", "owner")}
    pg_app.rollback()

    set_context(pg_app, user=world.bob, email="bob@example.com")
    assert call(pg_app, "SELECT * FROM app_my_memberships()") == set()


def test_my_pending_invitations_matches_email_and_expiry(
    world: World, pg_app: psycopg.Connection
) -> None:
    o = world.orgs
    query = (
        "SELECT id, organization_id, organization_name, role, invited_by_name, expires_at "
        "FROM app_my_pending_invitations(%s)"
    )
    acme_row = (
        world.bob_acme_invitation,
        o.acme,
        "Acme",
        "member",
        "Alice",
        NOW + timedelta(days=7),
    )
    globex_row = (
        world.bob_globex_invitation,
        o.globex,
        "Globex",
        "member",
        "Gina",
        NOW + timedelta(days=1),
    )
    # Bob signed in as "Bob@Example.COM": matched through email_normalized, case-insensitively.
    set_context(pg_app, user=world.bob, email="Bob@Example.COM")
    assert call(pg_app, query, NOW) == {acme_row, globex_row}
    # p_now is respected: expired when now >= expires_at. Declined invitations never show.
    assert call(pg_app, query, NOW + timedelta(days=1)) == {acme_row}
    assert call(pg_app, query, NOW + timedelta(days=7)) == set()
    pg_app.rollback()

    set_context(pg_app, user=world.carol, email="carol@example.com")
    assert call(pg_app, query, NOW) == set()


def test_my_invitation_hidden_for_other_email(world: World, pg_app: psycopg.Connection) -> None:
    query = "SELECT * FROM app_my_invitation(%s)"
    set_context(pg_app, user=world.carol, email="carol@example.com")
    assert call(pg_app, query, world.bob_acme_invitation) == set()
    pg_app.rollback()

    # Even the inviter (Alice, owner of Acme) gets nothing: it is not addressed to her.
    set_context(pg_app, org=world.orgs.acme, user=world.orgs.alice, email="alice@example.com")
    assert call(pg_app, query, world.bob_acme_invitation) == set()
    pg_app.rollback()

    set_context(pg_app, user=world.bob, email="bob@example.com")
    assert call(pg_app, query, world.bob_acme_invitation) == {
        (world.orgs.acme, "bob@example.com", "member", "pending", NOW + timedelta(days=7))
    }
    # Decided invitations are returned with their status (the API answers 404 if not pending).
    assert call(pg_app, query, world.bob_declined_invitation) == {
        (world.orgs.acme, "bob@example.com", "member", "declined", NOW + timedelta(days=7))
    }


def test_functions_return_nothing_without_user_context(
    world: World, pg_app: psycopg.Connection
) -> None:
    queries = [
        ("SELECT * FROM app_my_memberships()", ()),
        ("SELECT * FROM app_my_pending_invitations(%s)", (NOW,)),
        ("SELECT * FROM app_my_invitation(%s)", (world.bob_acme_invitation,)),
    ]
    for query, params in queries:  # fresh transaction, no context at all
        assert call(pg_app, query, *params) == set(), query
    pg_app.commit()

    # Reused connection (settings now read ''), with only an organization set.
    set_context(pg_app, org=world.orgs.acme)
    for query, params in queries:
        assert call(pg_app, query, *params) == set(), query


def test_unfiltered_query_as_self_reader_policies_only_shows_current_user(
    world: World, pg_owner: psycopg.Connection
) -> None:
    """A deliberately too broad SECURITY DEFINER function still only sees the current user.

    Created as forge_owner and handed to forge_self_reader like the real ones, inside a
    transaction that is rolled back: nothing survives the test.
    """
    o = world.orgs
    with pg_owner.transaction(force_rollback=True):
        pg_owner.execute("GRANT CREATE ON SCHEMA public TO forge_self_reader")
        for name, body, columns in (
            (
                "memberships",
                "SELECT organization_id, user_id FROM public.memberships",
                "a uuid, b uuid",
            ),
            (
                "invitations",
                "SELECT id, email_normalized FROM public.invitations",
                "a uuid, b text",
            ),
            ("organizations", "SELECT id, name FROM public.organizations", "a uuid, b text"),
        ):
            pg_owner.execute(
                f"CREATE FUNCTION too_broad_{name}() RETURNS TABLE ({columns}) "
                f"LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public "
                f"AS $$ {body} $$"
            )
            pg_owner.execute(f"ALTER FUNCTION too_broad_{name}() OWNER TO forge_self_reader")

        set_context(pg_owner, user=world.bob, email="bob@example.com")
        assert call(pg_owner, "SELECT * FROM too_broad_memberships()") == set()
        assert call(pg_owner, "SELECT * FROM too_broad_invitations()") == {
            (world.bob_acme_invitation, "bob@example.com"),
            (world.bob_globex_invitation, "bob@example.com"),
            (world.bob_declined_invitation, "bob@example.com"),
        }
        # Organizations with a pending invitation for Bob.
        assert call(pg_owner, "SELECT * FROM too_broad_organizations()") == {
            (o.acme, "Acme"),
            (o.globex, "Globex"),
        }

        set_context(pg_owner, user=o.gina, email="gina@example.com")
        assert call(pg_owner, "SELECT * FROM too_broad_memberships()") == {(o.globex, o.gina)}
        assert call(pg_owner, "SELECT * FROM too_broad_invitations()") == set()
        assert call(pg_owner, "SELECT * FROM too_broad_organizations()") == {(o.globex, "Globex")}

        set_context(pg_owner, user=world.carol, email="carol@example.com")
        for name in ("memberships", "invitations", "organizations"):
            assert call(pg_owner, f"SELECT * FROM too_broad_{name}()") == set(), name
