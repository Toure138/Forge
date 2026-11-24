"""Current session of a request (cookie `forge_session`), as a FastAPI dependency.

`current_session` runs inside the request's single transaction (`RequestDbDep`): it validates the
session with the injected clock, records the activity and sets the current user in the database
context (`app.user_id`, `app.user_email`). No call to Google is made (FR-12).

Routes never use it directly: they declare a permission with `authz.require_authenticated(...)`
(or `authz.require(...)` for an organization), which builds on it.
"""

import hmac
from dataclasses import dataclass
from datetime import timedelta
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Request

from app.auth.sessions import SESSION_COOKIE, SessionRejection, load_valid_session, touch
from app.core.clock import Clock
from app.core.config import Settings
from app.core.errors import ApiError
from app.core.security_log import security_event
from app.db.context import RequestDbDep


@dataclass(frozen=True)
class Principal:
    """The signed-in user of the request. Holds the session's hash, never its token."""

    user_id: UUID
    email: str
    email_normalized: str
    name: str
    picture_url: str | None
    csrf_token: str
    session_token_hash: bytes


UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
CSRF_HEADER = "X-CSRF-Token"


def not_authenticated() -> ApiError:
    return ApiError(401, "not_authenticated", "Authentication required.")


def _csrf_ok(sent: str | None, expected: str) -> bool:
    return sent is not None and hmac.compare_digest(sent.encode(), expected.encode())


async def current_session(request: Request, db: RequestDbDep) -> Principal:
    """401 `not_authenticated` unless the cookie designates a valid session (FR-8, FR-9); on an
    unsafe method, 403 `csrf_failed` unless `X-CSRF-Token` is the session's token (FR-11). Both
    checks come before any write (not even the activity is recorded)."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise not_authenticated()
    settings: Settings = request.app.state.settings
    clock: Clock = request.app.state.clock
    now = clock.now()
    session = await load_valid_session(
        db,
        token,
        now=now,
        idle_timeout=timedelta(hours=settings.session_idle_timeout_hours),
        max_age=timedelta(days=settings.session_max_age_days),
    )
    if isinstance(session, SessionRejection):
        security_event("session_rejected", user_id=session.user_id, reason=session.reason)
        raise not_authenticated()
    if request.method in UNSAFE_METHODS and not _csrf_ok(
        request.headers.get(CSRF_HEADER), session.csrf_token
    ):
        raise ApiError(403, "csrf_failed", "Invalid or missing CSRF token.")
    await touch(db, session.token_hash, now=now)
    await db.set_user(session.user_id, session.email_normalized)
    return Principal(
        user_id=session.user_id,
        email=session.email,
        email_normalized=session.email_normalized,
        name=session.name,
        picture_url=session.picture_url,
        csrf_token=session.csrf_token,
        session_token_hash=session.token_hash,
    )


CurrentSessionDep = Annotated[Principal, Depends(current_session)]
