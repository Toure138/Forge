"""Organization audit log service (`app/services/audit.py`) and `RequestDb.record_denial`
(plan, « Refus journalisés sans être annulés »; FR-38, FR-41)."""

from typing import Any

import psycopg
import pytest
from sqlalchemy import select, update

from app.core.errors import ApiError
from app.db import tables
from app.services import audit
from tests.conftest import RequestDbFactory
from tests.db.conftest import NOW, TwoOrgs, set_context

DENIED = ApiError(403, "permission_denied", "Permission denied.")


def audit_rows(pg_app: psycopg.Connection, org: Any) -> list[tuple[Any, ...]]:
    """Audit entries of `org`, read as `forge_app` under its context (oldest first)."""
    set_context(pg_app, org=org)
    rows = pg_app.execute(
        "SELECT organization_id, actor_user_id, action, target_type, target_id, outcome,"
        " details, occurred_at FROM audit_events ORDER BY id"
    ).fetchall()
    pg_app.commit()
    return rows


async def test_record_writes_event_in_active_org(
    two_orgs: TwoOrgs, request_db_factory: RequestDbFactory, pg_app: psycopg.Connection
) -> None:
    before_acme, before_globex = (
        audit_rows(pg_app, two_orgs.acme),
        audit_rows(pg_app, two_orgs.globex),
    )
    async with request_db_factory() as db:
        await db.set_user(two_orgs.alice, "alice@example.com")
        await db.set_organization(two_orgs.acme)
        event_id = await audit.record(
            db,
            action="organization.renamed",
            outcome="success",
            target_type="organization",
            target_id=str(two_orgs.acme),
            details={"from_name": "Acme", "to_name": "Acme SA"},
            now=NOW,
        )
    assert isinstance(event_id, int)

    after_acme = audit_rows(pg_app, two_orgs.acme)
    assert after_acme[: len(before_acme)] == before_acme
    assert after_acme[len(before_acme) :] == [
        (
            two_orgs.acme,
            two_orgs.alice,  # the actor is the current user of the request
            "organization.renamed",
            "organization",
            str(two_orgs.acme),
            "success",
            {"from_name": "Acme", "to_name": "Acme SA"},
            NOW,
        )
    ]
    assert audit_rows(pg_app, two_orgs.globex) == before_globex


async def test_record_needs_an_active_organization(
    two_orgs: TwoOrgs, request_db_factory: RequestDbFactory
) -> None:
    async with request_db_factory() as db:
        await db.set_user(two_orgs.alice, "alice@example.com")
        with pytest.raises(RuntimeError, match="active organization"):
            await audit.record(db, action="member.left", outcome="success", now=NOW)


async def test_record_denial_survives_rollback(
    two_orgs: TwoOrgs, request_db_factory: RequestDbFactory, pg_app: psycopg.Connection
) -> None:
    """The business write of the request is rolled back; the `denied` entry is committed, in the
    right organization and with the right actor (context set again after the ROLLBACK); the
    final ROLLBACK of the request transaction is a no-op."""
    before = audit_rows(pg_app, two_orgs.acme)
    orgs = tables.organizations
    with pytest.raises(ApiError) as raised:
        async with request_db_factory() as db:
            await db.set_user(two_orgs.alice, "alice@example.com")
            await db.set_organization(two_orgs.acme)
            await db.connection.execute(
                update(orgs).where(orgs.c.id == two_orgs.acme).values(name="Hacked")
            )
            await db.record_denial(
                DENIED,
                action="permission.denied",
                target_type="organization",
                target_id=str(two_orgs.acme),
                details={"permission": "organization:rename"},
                now=NOW,
            )
    assert raised.value is DENIED

    after = audit_rows(pg_app, two_orgs.acme)
    assert after[len(before) :] == [
        (
            two_orgs.acme,
            two_orgs.alice,
            "permission.denied",
            "organization",
            str(two_orgs.acme),
            "denied",
            {"permission": "organization:rename"},
            NOW,
        )
    ]
    set_context(pg_app, org=two_orgs.acme)
    name = pg_app.execute("SELECT name FROM organizations").fetchone()
    pg_app.commit()
    assert name == ("Acme",)  # the business write did not survive

    # The connection went back to the pool clean: the next request sees no context.
    async with request_db_factory() as db:
        visible = await db.connection.execute(select(orgs.c.id))
        assert visible.all() == []


@pytest.mark.parametrize(
    "details",
    [
        {"nested": {"token": "x"}},
        {"items": ["a", "b"]},
        {"ratio": 0.5},
        {"raw": b"secret"},
        {1: "not a string key"},
    ],
    ids=["dict", "list", "float", "bytes", "non_str_key"],
)
async def test_details_accept_only_scalars(
    two_orgs: TwoOrgs, request_db_factory: RequestDbFactory, details: dict[Any, Any]
) -> None:
    """FR-41: details are flat (str, int, bool, None), so a structured secret (a token payload,
    a cookie jar...) cannot be dumped into the log by construction."""
    async with request_db_factory() as db:
        await db.set_user(two_orgs.alice, "alice@example.com")
        await db.set_organization(two_orgs.acme)
        with pytest.raises(TypeError, match="details"):
            await audit.record(
                db, action="member.role_changed", outcome="success", details=details, now=NOW
            )
        ok = await audit.record(
            db,
            action="member.role_changed",
            outcome="success",
            details={
                "from_role": "member",
                "to_role": "guest",
                "count": 1,
                "flag": True,
                "n": None,
            },
            now=NOW,
        )
        assert ok > 0


async def test_record_refuses_unknown_action(
    two_orgs: TwoOrgs, request_db_factory: RequestDbFactory
) -> None:
    async with request_db_factory() as db:
        await db.set_user(two_orgs.alice, "alice@example.com")
        await db.set_organization(two_orgs.acme)
        with pytest.raises(ValueError, match="audit action"):
            await audit.record(db, action="audit.erased", outcome="success", now=NOW)  # type: ignore[arg-type]
