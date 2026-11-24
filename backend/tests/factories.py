"""Test world for API tests (spec 001): organizations Acme and Globex, users, memberships and
sessions, and one authenticated client per user.

Default world (fixture `world` in `tests/conftest.py`):

| Organization | Owner | Admin | Member | Guest |
|---|---|---|---|---|
| Acme   | Alice | Bob | Carole | Dave |
| Globex | Gina  |     |        |      |

- Users and sessions are created by the application's own services (`upsert_user`,
  `create_session`), on the test database, with the fake clock's `now`.
- Organizations and memberships are inserted as `forge_app` under the organization's context, like
  the API: with FORCE ROW LEVEL SECURITY, nothing else can write there.
- `world.client_as(person, org)` is a browser signed in as `person`: session cookie,
  `X-CSRF-Token` and (optionally) `X-Organization-Id` are set on every request. Each call builds
  its own application (never two `TestClient` on the same app: each lifespan replaces the engine);
  all of them share the fake clock, the fake Google and the database.
- Reads and writes done here are committed at once (`pg_app` is not in autocommit): the API sees
  them, and no lock is left behind.
"""

import asyncio
import uuid
from collections.abc import Awaitable, Callable, Iterator, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

import psycopg
from fastapi.testclient import TestClient
from psycopg.rows import dict_row
from sqlalchemy.pool import NullPool

from app.auth.provider import VerifiedIdentity
from app.auth.sessions import SESSION_COOKIE, NewSession, create_session
from app.auth.users import User, normalize_email, upsert_user
from app.core.aio import selector_loop_factory
from app.db.context import RequestDb, request_transaction
from tests.db.conftest import insert, set_context
from tests.fakes.clock import FakeClock
from tests.fakes.google import FakeGoogle

ORG_HEADER = "X-Organization-Id"
CSRF_HEADER = "X-CSRF-Token"


@dataclass(frozen=True)
class Person:
    """A user of the test world, with a valid session (token and CSRF token)."""

    key: str
    id: uuid.UUID
    name: str
    email: str
    session_token: str
    csrf_token: str


DEFAULT_PEOPLE = (
    ("alice", "Alice Martin", "alice@acme.example"),
    ("bob", "Bob Durand", "bob@acme.example"),
    ("carole", "Carole Petit", "carole@acme.example"),
    ("dave", "Dave Moreau", "dave@gmail.com"),
    ("gina", "Gina Rossi", "gina@globex.example"),
)
DEFAULT_MEMBERSHIPS = {
    "Acme": {"alice": "owner", "bob": "admin", "carole": "member", "dave": "guest"},
    "Globex": {"gina": "owner"},
}


def _run_in_transaction[T](work: Callable[[RequestDb], Awaitable[T]]) -> T:
    """Run `work` in one request transaction on `forge_test`, from synchronous test code."""
    from tests.conftest import create_test_engine  # tests.conftest imports this module

    async def run() -> T:
        engine = create_test_engine(poolclass=NullPool)
        try:
            async with request_transaction(engine) as db:
                return await work(db)
        finally:
            await engine.dispose()

    return asyncio.run(run(), loop_factory=selector_loop_factory)


def _create_people(
    specs: Sequence[tuple[str, str, str]], now: datetime
) -> list[tuple[User, NewSession]]:
    async def work(db: RequestDb) -> list[tuple[User, NewSession]]:
        created = []
        for key, name, email in specs:
            identity = VerifiedIdentity(
                sub=f"sub-{key}-{uuid.uuid4().hex}",
                email=email,
                email_verified=True,
                name=name,
                picture=None,
            )
            user = await upsert_user(db, identity, now=now)
            created.append((user, await create_session(db, user.id, now=now)))
        return created

    return _run_in_transaction(work)


def _create_sessions(user_ids: Sequence[uuid.UUID], now: datetime) -> list[NewSession]:
    async def work(db: RequestDb) -> list[NewSession]:
        return [await create_session(db, user_id, now=now) for user_id in user_ids]

    return _run_in_transaction(work)


class World:
    """Acme / Globex world. People are attributes (`world.alice`); organizations too
    (`world.acme`, `world.globex`)."""

    alice: Person
    bob: Person
    carole: Person
    dave: Person
    gina: Person
    acme: uuid.UUID
    globex: uuid.UUID

    def __init__(self, clock: FakeClock, fake_google: FakeGoogle, conn: psycopg.Connection) -> None:
        self.clock = clock
        self.fake_google = fake_google
        self.conn = conn
        self._clients: list[TestClient] = []
        self.people: dict[str, Person] = {}

    # --- building ---

    def populate(self) -> "World":
        self.add_people(*DEFAULT_PEOPLE)
        for org_name, members in DEFAULT_MEMBERSHIPS.items():
            org = self.add_organization(org_name)
            setattr(self, org_name.lower(), org)
            for key, role in members.items():
                self.add_member(org, self.people[key], role)
        return self

    def add_people(self, *specs: tuple[str, str, str]) -> list[Person]:
        """Users created by the sign-in services, each with a fresh valid session."""
        people = []
        for (key, _, _), (user, session) in zip(
            specs, _create_people(specs, self.now), strict=True
        ):
            person = Person(key, user.id, user.name, user.email, session.token, session.csrf_token)
            self._set_person(person)
            people.append(person)
        return people

    def add_person(self, key: str, name: str, email: str) -> Person:
        return self.add_people((key, name, email))[0]

    def renew_sessions(self) -> None:
        """A new session for everyone, at the clock's current time: for tests that move the clock
        beyond the session timeouts (idle 24 h, absolute 7 days). Clients built before keep the old
        session; build new ones with `client_as`."""
        people = list(self.people.values())
        sessions = _create_sessions([p.id for p in people], self.now)
        for person, session in zip(people, sessions, strict=True):
            renewed = replace(person, session_token=session.token, csrf_token=session.csrf_token)
            self._set_person(renewed)

    def _set_person(self, person: Person) -> None:
        self.people[person.key] = person
        setattr(self, person.key, person)

    def add_organization(self, name: str) -> uuid.UUID:
        org = uuid.uuid4()
        self._write(org, "organizations", {"id": org, "name": name, "created_at": self.now})
        return org

    def add_member(self, org: uuid.UUID, person: Person, role: str) -> None:
        row = {
            "organization_id": org,
            "user_id": person.id,
            "role": role,
            "created_at": self.now,
            "updated_at": self.now,
        }
        self._write(org, "memberships", row)

    def add_invitation(
        self,
        org: uuid.UUID,
        email: str,
        role: str,
        invited_by: Person,
        *,
        status: str = "pending",
        ttl: timedelta = timedelta(days=7),
    ) -> uuid.UUID:
        invitation = uuid.uuid4()
        row = {
            "id": invitation,
            "organization_id": org,
            "email_normalized": normalize_email(email),
            "role": role,
            "invited_by": invited_by.id,
            "created_at": self.now,
            "expires_at": self.now + ttl,
            "status": status,
        }
        self._write(org, "invitations", row)
        return invitation

    def _write(self, org: uuid.UUID, table: str, row: dict[str, Any]) -> None:
        set_context(self.conn, org=org)
        insert(self.conn, table, row)
        self.conn.commit()

    # --- reading (as forge_app, under the organization's context) ---

    @property
    def now(self) -> datetime:
        return self.clock.now()

    def query(self, org: uuid.UUID, statement: str, *params: Any) -> list[dict[str, Any]]:
        set_context(self.conn, org=org)
        with self.conn.cursor(row_factory=dict_row) as cursor:
            rows = cursor.execute(statement, params).fetchall()  # type: ignore[arg-type]
        self.conn.commit()
        return rows

    def execute(self, org: uuid.UUID, statement: str, *params: Any) -> None:
        """A write as `forge_app` under `org`'s context (committed at once)."""
        set_context(self.conn, org=org)
        self.conn.execute(statement, params)  # type: ignore[arg-type]
        self.conn.commit()

    def role_of(self, org: uuid.UUID, person: Person) -> str | None:
        rows = self.query(
            org,
            "SELECT role FROM memberships WHERE organization_id = %s AND user_id = %s",
            org,
            person.id,
        )
        return rows[0]["role"] if rows else None

    def roles(self, org: uuid.UUID) -> dict[uuid.UUID, str]:
        rows = self.query(
            org, "SELECT user_id, role FROM memberships WHERE organization_id = %s", org
        )
        return {row["user_id"]: row["role"] for row in rows}

    def audit(self, org: uuid.UUID) -> list[dict[str, Any]]:
        """Audit entries of `org`, oldest first."""
        return self.query(
            org,
            "SELECT id, occurred_at, actor_user_id, action, target_type, target_id, outcome,"
            " details FROM audit_events WHERE organization_id = %s ORDER BY id",
            org,
        )

    def invitation(self, org: uuid.UUID, invitation: uuid.UUID) -> dict[str, Any] | None:
        rows = self.query(
            org,
            "SELECT * FROM invitations WHERE organization_id = %s AND id = %s",
            org,
            invitation,
        )
        return rows[0] if rows else None

    # --- clients ---

    def client_as(self, person: Person, org: uuid.UUID | None = None) -> TestClient:
        """A browser signed in as `person` (lifespan running), acting in `org` if given."""
        from tests.conftest import client_for, create_test_app  # avoid an import cycle

        client = client_for(create_test_app(self.clock, self.fake_google))
        client.__enter__()
        self._clients.append(client)
        client.cookies.set(SESSION_COOKIE, person.session_token)
        client.headers[CSRF_HEADER] = person.csrf_token
        if org is not None:
            client.headers[ORG_HEADER] = str(org)
        return client

    def close(self) -> None:
        for client in self._clients:
            client.__exit__(None, None, None)
        self._clients.clear()


def build_world(
    clock: FakeClock, fake_google: FakeGoogle, conn: psycopg.Connection
) -> Iterator[World]:
    world = World(clock, fake_google, conn).populate()
    try:
        yield world
    finally:
        world.close()
