"""Per-request database access (`app/db/context.py`): one transaction, context set with
`set_config(..., true)`, nothing left on a pooled connection (AC-45, application side)."""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import func, insert, select, text
from sqlalchemy.exc import ResourceClosedError

from app.db import tables
from app.db.context import RequestDbDep, request_transaction
from tests.conftest import client_for, create_test_engine
from tests.db.conftest import NOW, TwoOrgs


def user_row(user_id: uuid.UUID) -> dict[str, object]:
    return {
        "id": user_id,
        "google_sub": f"sub-{user_id}",
        "email": "zoe@example.com",
        "email_normalized": "zoe@example.com",
        "name": "Zoe",
        "created_at": NOW,
        "last_login_at": NOW,
    }


async def test_set_organization_scopes_queries_to_transaction(
    two_orgs: TwoOrgs, request_db_factory
) -> None:
    async with request_db_factory() as db:
        await db.set_user(two_orgs.alice, "Alice@Example.com")
        await db.set_organization(two_orgs.acme)
        assert (db.user_id, db.organization_id) == (two_orgs.alice, two_orgs.acme)

        # An unfiltered query only sees the active organization (RLS).
        names = (await db.connection.execute(select(tables.organizations.c.name))).scalars()
        assert names.all() == ["Acme"]
        context = await db.connection.execute(
            text("SELECT app_current_org(), app_current_user(), app_current_user_email()")
        )
        assert context.one() == (two_orgs.acme, two_orgs.alice, "alice@example.com")
        # `set_user` also feeds the self-reader functions (they need the user id).
        mine = await db.connection.execute(text("SELECT organization_id FROM app_my_memberships()"))
        assert mine.scalars().all() == [two_orgs.acme]

    async with request_db_factory() as db:  # the next transaction starts without context
        count = await db.connection.execute(select(func.count()).select_from(tables.organizations))
        assert count.scalar_one() == 0


async def test_context_does_not_leak_to_next_transaction_on_pooled_connection(
    two_orgs: TwoOrgs,
) -> None:
    engine = create_test_engine(pool_size=1, max_overflow=0)
    try:
        async with request_transaction(engine) as db:
            await db.set_user(two_orgs.alice, "alice@example.com")
            await db.set_organization(two_orgs.acme)
            first_pid = (await db.connection.execute(text("SELECT pg_backend_pid()"))).scalar_one()
            visible = select(func.count()).select_from(tables.memberships)
            assert (await db.connection.execute(visible)).scalar_one() == 1

        async with request_transaction(engine) as db:
            pid = (await db.connection.execute(text("SELECT pg_backend_pid()"))).scalar_one()
            assert pid == first_pid  # same pooled connection
            settings = await db.connection.execute(
                text(
                    "SELECT current_setting('app.organization_id', true),"
                    " current_setting('app.user_id', true), app_current_org(), app_current_user()"
                )
            )
            org_setting, user_setting, current_org, current_user = settings.one()
            assert org_setting in ("", None)
            assert user_setting in ("", None)
            assert (current_org, current_user) == (None, None)
            for table in (tables.organizations, tables.memberships, tables.invitations):
                count = select(func.count()).select_from(table)
                assert (await db.connection.execute(count)).scalar_one() == 0
    finally:
        await engine.dispose()


async def test_exception_rolls_back(request_db_factory) -> None:
    rolled_back, committed = uuid.uuid4(), uuid.uuid4()
    with pytest.raises(RuntimeError, match="boom"):
        async with request_db_factory() as db:
            await db.connection.execute(insert(tables.users).values(user_row(rolled_back)))
            raise RuntimeError("boom")

    async with request_db_factory() as db:
        await db.connection.execute(insert(tables.users).values(user_row(committed)))

    async with request_db_factory() as db:
        ids = (await db.connection.execute(select(tables.users.c.id))).scalars().all()
        assert ids == [committed]


async def test_release_commits_and_returns_connection(clean_db: None) -> None:
    """`RequestDb.release()` (M2, `invoke`): committed, connection back in the pool, the request
    can no longer query; the end of `request_transaction` then has nothing left to do (its
    COMMIT / ROLLBACK on the closed connection are no-ops), and an exception raised after the
    release propagates unchanged."""
    engine = create_test_engine(pool_size=1, max_overflow=0)
    kept = uuid.uuid4()
    try:
        with pytest.raises(RuntimeError, match="after release"):
            async with request_transaction(engine) as db:
                await db.connection.execute(insert(tables.users).values(user_row(kept)))
                await db.release()
                assert engine.sync_engine.pool.checkedout() == 0
                # The single pooled connection is free: another transaction sees the committed row.
                async with request_transaction(engine) as other:
                    query = select(tables.users.c.id)
                    assert (await other.connection.execute(query)).scalars().all() == [kept]
                with pytest.raises(ResourceClosedError):
                    await db.connection.execute(select(func.count()).select_from(tables.users))
                raise RuntimeError("after release")
        async with request_transaction(engine) as db:  # nothing was rolled back
            ids = (await db.connection.execute(select(tables.users.c.id))).scalars().all()
            assert ids == [kept]
    finally:
        await engine.dispose()


def test_dependency_commits_before_response(clean_db: None) -> None:
    """`request_db` ends with the route function: committed and released before the body is sent
    (a streamed response must not hold a connection, nor see uncommitted work)."""
    user_id = uuid.uuid4()
    observed: dict[str, int] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.engine = create_test_engine()
        yield
        await app.state.engine.dispose()

    app = FastAPI(lifespan=lifespan)

    @app.post("/users")
    async def create_user(db: RequestDbDep, request: Request) -> StreamingResponse:
        await db.connection.execute(insert(tables.users).values(user_row(user_id)))
        engine = request.app.state.engine

        async def body() -> AsyncIterator[bytes]:
            observed["checked_out"] = engine.sync_engine.pool.checkedout()
            async with engine.connect() as other:
                query = select(func.count()).where(tables.users.c.id == user_id)
                observed["visible"] = (await other.execute(query)).scalar_one()
            yield b"ok"

        return StreamingResponse(body())

    @app.post("/fail")
    async def fail(db: RequestDbDep) -> None:
        await db.connection.execute(insert(tables.users).values(user_row(uuid.uuid4())))
        raise RuntimeError("boom")

    with client_for(app) as client:
        res = client.post("/users")
        assert (res.status_code, res.text) == (200, "ok")
        assert observed == {"checked_out": 0, "visible": 1}

        with pytest.raises(RuntimeError, match="boom"):
            client.post("/fail")

        async def count_users() -> int:
            async with app.state.engine.connect() as conn:
                return (
                    await conn.execute(select(func.count()).select_from(tables.users))
                ).scalar_one()

        assert client.portal.call(count_users) == 1  # the failed request was rolled back
