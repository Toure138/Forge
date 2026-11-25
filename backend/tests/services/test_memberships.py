"""Memberships service under concurrency: « at least one Owner » (FR-28, AC-35)."""

import asyncio
import uuid
from collections.abc import Awaitable, Callable

import psycopg
import pytest

from app.core.authz import Role, permissions_for
from app.core.errors import ApiError
from app.services import memberships
from tests.conftest import RequestDbFactory
from tests.db.conftest import NOW, TwoOrgs, insert, insert_user, org_row, set_context

OWNER_PERMISSIONS = permissions_for(Role.OWNER)


def owners_of(pg_app: psycopg.Connection, org: uuid.UUID) -> set[uuid.UUID]:
    set_context(pg_app, org=org)
    rows = pg_app.execute(
        "SELECT user_id FROM memberships WHERE organization_id = %s AND role = 'owner'", (org,)
    ).fetchall()
    pg_app.commit()
    return {row[0] for row in rows}


@pytest.fixture
def two_owners(two_orgs: TwoOrgs, pg_app: psycopg.Connection) -> tuple[uuid.UUID, uuid.UUID]:
    """Acme with two Owners: Alice (from `two_orgs`) and Bob."""
    bob = insert_user(pg_app, "Bob")
    pg_app.commit()
    set_context(pg_app, org=two_orgs.acme)
    insert(pg_app, "memberships", org_row("memberships", two_orgs.acme, bob))
    pg_app.commit()
    assert owners_of(pg_app, two_orgs.acme) == {two_orgs.alice, bob}
    return two_orgs.alice, bob


@pytest.fixture
def widened_race(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pause each transaction right after it has read (and locked) the Owners, so that the other
    one reaches the same step before anything is written. Without the lock, both read two Owners
    and both demotions go through; with it, the second waits in Postgres for the first."""
    real: Callable[..., Awaitable[set[uuid.UUID]]] = memberships._lock_owners

    async def slow_lock_owners(db: object) -> set[uuid.UUID]:
        owners = await real(db)
        await asyncio.sleep(0.05)
        return owners

    monkeypatch.setattr(memberships, "_lock_owners", slow_lock_owners)


@pytest.mark.usefixtures("widened_race")
async def test_concurrent_owner_demotions_keep_one_owner(
    two_orgs: TwoOrgs,
    two_owners: tuple[uuid.UUID, uuid.UUID],
    request_db_factory: RequestDbFactory,
    pg_app: psycopg.Connection,
) -> None:
    """AC-35: Alice and Bob, both Owners, demote each other at the same time on two connections:
    one demotion succeeds, the other is refused with `last_owner`, exactly one Owner remains."""
    alice, bob = two_owners

    async def demote(actor: uuid.UUID, target: uuid.UUID) -> None:
        async with request_db_factory() as db:
            await db.set_user(actor, f"{actor}@example.com")
            await db.set_organization(two_orgs.acme)
            await memberships.change_role(
                db, permissions=OWNER_PERMISSIONS, user_id=target, role=Role.ADMIN, now=NOW
            )

    results = await asyncio.gather(demote(alice, bob), demote(bob, alice), return_exceptions=True)

    errors = [r for r in results if isinstance(r, BaseException)]
    assert len(errors) == 1, results
    assert isinstance(errors[0], ApiError)
    assert (errors[0].status_code, errors[0].code) == (409, "last_owner")
    remaining = owners_of(pg_app, two_orgs.acme)
    assert len(remaining) == 1
    assert remaining <= {alice, bob}
