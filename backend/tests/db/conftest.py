"""Fixtures and helpers for SQL-level tests (synchronous psycopg, no event loop).

Under FORCE ROW LEVEL SECURITY, `forge_owner` has no policy on the organization tables: it can
neither read nor insert there (only TRUNCATE). Test data is therefore written as `forge_app`,
inside a transaction whose context is set with `set_config(..., true)`, exactly like the API.
"""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest
from psycopg import sql
from psycopg.types.json import Jsonb

from tests.conftest import connect_test_database

NOW = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)

# The organization tables of spec 001 and the column that carries their organization.
ORG_TABLES: dict[str, str] = {
    "organizations": "id",
    "memberships": "organization_id",
    "invitations": "organization_id",
    "conversations": "organization_id",
    "audit_events": "organization_id",
}


def set_context(
    conn: psycopg.Connection,
    *,
    org: uuid.UUID | None = None,
    user: uuid.UUID | None = None,
    email: str | None = None,
) -> None:
    """Transaction-local context, as the API does (`set_config(name, value, is_local => true)`)."""
    for name, value in (
        ("app.organization_id", org),
        ("app.user_id", user),
        ("app.user_email", email),
    ):
        if value is not None:
            conn.execute("SELECT set_config(%s, %s, true)", (name, str(value)))


def insert(conn: psycopg.Connection, table: str, row: dict[str, Any]) -> None:
    query = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
        sql.Identifier(table),
        sql.SQL(", ").join(map(sql.Identifier, row)),
        sql.SQL(", ").join(sql.Placeholder() * len(row)),
    )
    conn.execute(query, list(row.values()))


def insert_user(conn: psycopg.Connection, name: str, email: str | None = None) -> uuid.UUID:
    """`users` is global (no RLS)."""
    user_id = uuid.uuid4()
    email = email or f"{name.lower()}@example.com"
    insert(
        conn,
        "users",
        {
            "id": user_id,
            "google_sub": f"sub-{user_id}",
            "email": email,
            "email_normalized": email.lower(),
            "name": name,
            "created_at": NOW,
            "last_login_at": NOW,
        },
    )
    return user_id


def org_row(table: str, org: uuid.UUID, user: uuid.UUID, **overrides: Any) -> dict[str, Any]:
    """A valid row of organization table `table`, belonging to `org` (and `user`)."""
    rows: dict[str, dict[str, Any]] = {
        "organizations": {"id": org, "name": "Org", "created_at": NOW},
        "memberships": {
            "organization_id": org,
            "user_id": user,
            "role": "owner",
            "created_at": NOW,
            "updated_at": NOW,
        },
        "invitations": {
            "id": uuid.uuid4(),
            "organization_id": org,
            "email_normalized": f"invitee-{uuid.uuid4().hex[:8]}@example.com",
            "role": "member",
            "invited_by": user,
            "created_at": NOW,
            "expires_at": NOW + timedelta(days=7),
            "status": "pending",
        },
        "conversations": {
            "id": uuid.uuid4(),
            "organization_id": org,
            "user_id": user,
            "agent_name": "research",
            "created_at": NOW,
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
    return rows[table] | overrides


@dataclass(frozen=True)
class TwoOrgs:
    """Acme (owner Alice) and Globex (owner Gina): one row of every organization table each."""

    acme: uuid.UUID
    globex: uuid.UUID
    alice: uuid.UUID
    gina: uuid.UUID
    acme_invitation: uuid.UUID
    globex_invitation: uuid.UUID


@pytest.fixture
def two_orgs(clean_db: None) -> Iterator[TwoOrgs]:
    acme, globex = uuid.uuid4(), uuid.uuid4()
    invitations: dict[uuid.UUID, uuid.UUID] = {}
    with connect_test_database("TEST_DATABASE_URL") as conn:
        alice = insert_user(conn, "Alice")
        gina = insert_user(conn, "Gina")
        conn.commit()
        for org, owner, name in ((acme, alice, "Acme"), (globex, gina, "Globex")):
            set_context(conn, org=org, user=owner)
            for table in ORG_TABLES:
                row = org_row(table, org, owner)
                if table == "organizations":
                    row["name"] = name
                if table == "invitations":
                    invitations[org] = row["id"]
                insert(conn, table, row)
            conn.commit()  # ends the transaction: the context is gone
    yield TwoOrgs(acme, globex, alice, gina, invitations[acme], invitations[globex])
