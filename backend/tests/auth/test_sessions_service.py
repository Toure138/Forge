"""Services of `app/auth/sessions.py`: login requests (single use, expiry, purge) and server-side
sessions (SHA-256 storage, idle and absolute expiry, revocation). Every date comes from `now`."""

import hashlib
from datetime import timedelta

import pytest
from sqlalchemy import func, select

from app.auth.provider import VerifiedIdentity
from app.auth.sessions import (
    PendingLogin,
    SessionRejection,
    ValidSession,
    consume_login_request,
    create_login_request,
    create_session,
    hash_token,
    load_valid_session,
    revoke,
    touch,
)
from app.auth.users import User, upsert_user
from app.db import tables
from app.db.context import RequestDb
from tests.conftest import RequestDbFactory
from tests.fakes.clock import FakeClock

TTL = timedelta(minutes=10)
IDLE = timedelta(hours=24)
MAX_AGE = timedelta(days=7)


async def count(db: RequestDb, table: tables.Table) -> int:
    result = await db.connection.execute(select(func.count()).select_from(table))
    return int(result.scalar_one())


async def a_user(db: RequestDb, clock: FakeClock) -> User:
    identity = VerifiedIdentity(
        sub="100000000000000000007",
        email="zoe@example.com",
        email_verified=True,
        name="Zoe",
        picture=None,
    )
    return await upsert_user(db, identity, now=clock.now())


async def test_login_request_is_single_use(request_db_factory: RequestDbFactory) -> None:
    clock = FakeClock()
    async with request_db_factory() as db:
        login = await create_login_request(db, now=clock.now(), ttl=TTL)
    assert len({login.token, login.state, login.nonce, login.code_verifier}) == 4

    async with request_db_factory() as db:
        pending = await consume_login_request(db, login.token, now=clock.now())
    assert pending == PendingLogin(
        state=login.state, nonce=login.nonce, code_verifier=login.code_verifier
    )
    async with request_db_factory() as db:  # replay
        assert await consume_login_request(db, login.token, now=clock.now()) is None
        assert await consume_login_request(db, "unknown-token", now=clock.now()) is None


async def test_login_request_stored_by_hash_only(request_db_factory: RequestDbFactory) -> None:
    clock = FakeClock()
    async with request_db_factory() as db:
        login = await create_login_request(db, now=clock.now(), ttl=TTL)
        row = (await db.connection.execute(select(tables.login_requests))).mappings().one()
    assert row["token_hash"] == hashlib.sha256(login.token.encode()).digest()
    assert login.token not in {str(value) for value in row.values()}
    assert row["expires_at"] == clock.now() + TTL


@pytest.mark.parametrize(("elapsed", "usable"), [(TTL - timedelta(seconds=1), True), (TTL, False)])
async def test_expired_login_request_is_refused(
    request_db_factory: RequestDbFactory, elapsed: timedelta, usable: bool
) -> None:
    clock = FakeClock()
    async with request_db_factory() as db:
        login = await create_login_request(db, now=clock.now(), ttl=TTL)
    clock.advance(seconds=elapsed.total_seconds())
    async with request_db_factory() as db:
        pending = await consume_login_request(db, login.token, now=clock.now())
    assert (pending is not None) is usable


async def test_expired_login_requests_are_purged(request_db_factory: RequestDbFactory) -> None:
    clock = FakeClock()
    async with request_db_factory() as db:
        await create_login_request(db, now=clock.now(), ttl=TTL)
        await create_login_request(db, now=clock.now(), ttl=TTL)
    clock.advance(minutes=5)
    async with request_db_factory() as db:
        await create_login_request(db, now=clock.now(), ttl=TTL)
        assert await count(db, tables.login_requests) == 3  # nothing expired yet
    clock.advance(minutes=5)
    async with request_db_factory() as db:
        latest = await create_login_request(db, now=clock.now(), ttl=TTL)
        rows = (await db.connection.execute(select(tables.login_requests.c.token_hash))).scalars()
        # The two first ones expired exactly now: purged; the third (5 min old) and the new remain.
        remaining = set(rows.all())
    assert len(remaining) == 2
    assert hash_token(latest.token) in remaining


async def test_session_token_stored_only_as_sha256(request_db_factory: RequestDbFactory) -> None:
    clock = FakeClock()
    async with request_db_factory() as db:
        user = await a_user(db, clock)
        new = await create_session(db, user.id, now=clock.now())
        row = (await db.connection.execute(select(tables.sessions))).mappings().one()
    assert len(new.token) >= 43 and len(new.csrf_token) >= 43  # 32 random bytes each
    assert new.token != new.csrf_token
    assert row["token_hash"] == hashlib.sha256(new.token.encode()).digest() == hash_token(new.token)
    assert new.token not in {str(value) for value in row.values()}
    assert row["csrf_token"] == new.csrf_token
    assert row["created_at"] == row["last_seen_at"] == clock.now()
    assert row["revoked_at"] is None


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("fresh", None),
        ("idle_23h59", None),
        ("idle_24h01", "idle_expired"),
        ("absolute_7d", "absolute_expired"),
        ("revoked", "revoked"),
        ("unknown", "unknown"),
    ],
)
async def test_session_validity(
    request_db_factory: RequestDbFactory, case: str, expected: str | None
) -> None:
    clock = FakeClock()
    async with request_db_factory() as db:
        user = await a_user(db, clock)
        new = await create_session(db, user.id, now=clock.now())
        token_hash = hash_token(new.token)
    token = new.token
    if case == "idle_23h59":
        clock.advance(hours=23, minutes=59)
    elif case == "idle_24h01":
        clock.advance(hours=24, minutes=1)
    elif case == "absolute_7d":
        for _ in range(7 * 2):  # used every 12 hours: never idle, but 7 days old in the end
            clock.advance(hours=12)
            async with request_db_factory() as db:
                await touch(db, token_hash, now=clock.now())
    elif case == "revoked":
        async with request_db_factory() as db:
            assert await revoke(db, token_hash, now=clock.now()) == user.id
    elif case == "unknown":
        token = "not-a-session-token"

    async with request_db_factory() as db:
        result = await load_valid_session(
            db, token, now=clock.now(), idle_timeout=IDLE, max_age=MAX_AGE
        )
    if expected is None:
        assert isinstance(result, ValidSession)
        assert (result.user_id, result.email_normalized, result.name) == (
            user.id,
            "zoe@example.com",
            "Zoe",
        )
        assert (result.token_hash, result.csrf_token) == (token_hash, new.csrf_token)
    else:
        expected_user = None if case == "unknown" else user.id
        assert result == SessionRejection(reason=expected, user_id=expected_user)  # type: ignore[arg-type]


async def test_touch_updates_last_seen(request_db_factory: RequestDbFactory) -> None:
    clock = FakeClock()
    async with request_db_factory() as db:
        user = await a_user(db, clock)
        new = await create_session(db, user.id, now=clock.now())
    clock.advance(hours=20)
    async with request_db_factory() as db:
        await touch(db, hash_token(new.token), now=clock.now())
    clock.advance(hours=20)  # 40 h after creation, 20 h after the last activity: still valid
    async with request_db_factory() as db:
        result = await load_valid_session(
            db, new.token, now=clock.now(), idle_timeout=IDLE, max_age=MAX_AGE
        )
        last_seen = await db.connection.execute(select(tables.sessions.c.last_seen_at))
    assert isinstance(result, ValidSession)
    assert last_seen.scalar_one() == clock.now() - timedelta(hours=20)


async def test_revoke_twice_keeps_first_date(request_db_factory: RequestDbFactory) -> None:
    clock = FakeClock()
    async with request_db_factory() as db:
        user = await a_user(db, clock)
        new = await create_session(db, user.id, now=clock.now())
        assert await revoke(db, hash_token(new.token), now=clock.now()) == user.id
    clock.advance(hours=1)
    async with request_db_factory() as db:
        assert await revoke(db, hash_token(new.token), now=clock.now()) is None
        revoked_at = await db.connection.execute(select(tables.sessions.c.revoked_at))
    assert revoked_at.scalar_one() == FakeClock().now()
