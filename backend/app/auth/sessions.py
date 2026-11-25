"""Login requests and server-side sessions (plan, « Sessions, cookies et CSRF »; plan T9, T10).

- A login request ties Google's answer to the browser that started the sign-in (cookie
  `forge_login`): it holds `state`, `nonce` and the PKCE `code_verifier`, is single use and
  expires (FR-5).
- A session is the only thing the browser gets after sign-in (cookie `forge_session`). Tokens are
  32 random bytes; the database stores their SHA-256 only, so a database leak yields no usable
  cookie. Each session has its own CSRF token (synchronizer token).

Every date comes from the `now` given by the caller (injected clock), never from SQL `now()`.
Tables `login_requests` and `sessions` are global (no RLS): queries always target one token hash.
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import Row, delete, insert, select, update

from app.auth.provider import new_code_verifier
from app.core.security_log import SessionRejectionReason
from app.db import tables
from app.db.context import RequestDb

SESSION_COOKIE = "forge_session"  # HttpOnly; Secure; SameSite=Lax; Path=/
LOGIN_COOKIE = "forge_login"  # HttpOnly; Secure; SameSite=Lax; Path=/api/auth; LOGIN_REQUEST_TTL


def new_token() -> str:
    """32 random bytes, URL-safe (session, login request and CSRF tokens)."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> bytes:
    """Storage form of a token: SHA-256 (no slow hash needed for 256 random bits)."""
    return hashlib.sha256(token.encode("utf-8")).digest()


# --- login requests ---


@dataclass(frozen=True)
class LoginRequest:
    token: str  # the `forge_login` cookie value; stored hashed
    state: str
    nonce: str
    code_verifier: str


@dataclass(frozen=True)
class PendingLogin:
    state: str
    nonce: str
    code_verifier: str


async def create_login_request(db: RequestDb, *, now: datetime, ttl: timedelta) -> LoginRequest:
    """Purge the expired login requests, then store a new one valid for `ttl`."""
    lr = tables.login_requests
    await db.connection.execute(delete(lr).where(lr.c.expires_at <= now))
    login = LoginRequest(
        token=new_token(), state=new_token(), nonce=new_token(), code_verifier=new_code_verifier()
    )
    await db.connection.execute(
        insert(lr).values(
            token_hash=hash_token(login.token),
            state=login.state,
            nonce=login.nonce,
            code_verifier=login.code_verifier,
            created_at=now,
            expires_at=now + ttl,
        )
    )
    return login


async def consume_login_request(db: RequestDb, token: str, *, now: datetime) -> PendingLogin | None:
    """Use the login request of `token` once. None if unknown, already used or expired.

    A single conditional UPDATE: two concurrent callbacks cannot both consume it."""
    lr = tables.login_requests
    statement = (
        update(lr)
        .where(lr.c.token_hash == hash_token(token))
        .where(lr.c.consumed_at.is_(None))
        .where(lr.c.expires_at > now)
        .values(consumed_at=now)
        .returning(lr.c.state, lr.c.nonce, lr.c.code_verifier)
    )
    row = (await db.connection.execute(statement)).one_or_none()
    if row is None:
        return None
    return PendingLogin(state=row.state, nonce=row.nonce, code_verifier=row.code_verifier)


# --- sessions ---


@dataclass(frozen=True)
class NewSession:
    token: str  # the `forge_session` cookie value; stored hashed
    csrf_token: str


@dataclass(frozen=True)
class ValidSession:
    """A valid session and its user. Holds the token's hash, never the token itself."""

    token_hash: bytes
    csrf_token: str
    user_id: UUID
    email: str
    email_normalized: str
    name: str
    picture_url: str | None


@dataclass(frozen=True)
class SessionRejection:
    """Why a session cookie is refused, and whose session it was (None when unknown)."""

    reason: SessionRejectionReason
    user_id: UUID | None


async def create_session(db: RequestDb, user_id: UUID, *, now: datetime) -> NewSession:
    """New session for `user_id`. Called by the Google callback only (FR-6, AC-7)."""
    new = NewSession(token=new_token(), csrf_token=new_token())
    await db.connection.execute(
        insert(tables.sessions).values(
            token_hash=hash_token(new.token),
            user_id=user_id,
            csrf_token=new.csrf_token,
            created_at=now,
            last_seen_at=now,
        )
    )
    return new


async def load_valid_session(
    db: RequestDb,
    token: str,
    *,
    now: datetime,
    idle_timeout: timedelta,
    max_age: timedelta,
) -> ValidSession | SessionRejection:
    """The session of `token` if valid at `now`, otherwise why it is refused (FR-8, FR-9).

    Valid: not revoked, `now < last_seen_at + idle_timeout` and `now < created_at + max_age`. The
    durations are applied at read time, so a configuration change also applies to existing
    sessions."""
    s, u = tables.sessions, tables.users
    statement = (
        select(
            s.c.token_hash,
            s.c.csrf_token,
            s.c.created_at,
            s.c.last_seen_at,
            s.c.revoked_at,
            u.c.id.label("user_id"),
            u.c.email,
            u.c.email_normalized,
            u.c.name,
            u.c.picture_url,
        )
        .join(u, u.c.id == s.c.user_id)
        .where(s.c.token_hash == hash_token(token))
    )
    row = (await db.connection.execute(statement)).one_or_none()
    rejection = _rejection(row, now=now, idle_timeout=idle_timeout, max_age=max_age)
    if rejection is not None:
        return SessionRejection(rejection, row.user_id if row is not None else None)
    assert row is not None
    return ValidSession(
        token_hash=row.token_hash,
        csrf_token=row.csrf_token,
        user_id=row.user_id,
        email=row.email,
        email_normalized=row.email_normalized,
        name=row.name,
        picture_url=row.picture_url,
    )


def _rejection(
    row: "Row[Any, *tuple[Any, ...]] | None",
    *,
    now: datetime,
    idle_timeout: timedelta,
    max_age: timedelta,
) -> SessionRejectionReason | None:
    if row is None:
        return "unknown"
    if row.revoked_at is not None:
        return "revoked"
    if now >= row.created_at + max_age:
        return "absolute_expired"
    if now >= row.last_seen_at + idle_timeout:
        return "idle_expired"
    return None


async def touch(db: RequestDb, token_hash: bytes, *, now: datetime) -> None:
    """Record activity: the idle timeout restarts from `now`."""
    s = tables.sessions
    await db.connection.execute(
        update(s).where(s.c.token_hash == token_hash).values(last_seen_at=now)
    )


async def revoke(db: RequestDb, token_hash: bytes, *, now: datetime) -> UUID | None:
    """Revoke the session (sign-out, or replaced at sign-in). Returns its user if it was active."""
    s = tables.sessions
    statement = (
        update(s)
        .where(s.c.token_hash == token_hash)
        .where(s.c.revoked_at.is_(None))
        .values(revoked_at=now)
        .returning(s.c.user_id)
    )
    user_id: UUID | None = (await db.connection.execute(statement)).scalar_one_or_none()
    return user_id
