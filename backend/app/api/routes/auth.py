"""Sign-in with Google (BFF: the server talks to Google, the browser never sees a Google token).

GET /auth/login     -> 302 to Google + cookie `forge_login` (exempted route `auth_login`)
GET /auth/callback  -> 303 to the app + cookie `forge_session` (exempted route `auth_callback`)
GET /auth/session   -> the signed-in user and the CSRF token (`session:read`)
POST /auth/logout   -> 204, session revoked server-side, cookie cleared (`session:logout`)

The names `auth_login` and `auth_callback` are listed in `authz.PUBLIC_ROUTES`.
"""

import hmac
import logging
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse

from app.auth.dependencies import Principal
from app.auth.provider import (
    IdentityError,
    IdentityProvider,
    IdentityProviderUnavailable,
    code_challenge_s256,
)
from app.auth.sessions import (
    LOGIN_COOKIE,
    SESSION_COOKIE,
    consume_login_request,
    create_login_request,
    create_session,
    hash_token,
    revoke,
)
from app.auth.users import upsert_user
from app.core.authz import require_authenticated
from app.core.clock import Clock
from app.core.config import Settings
from app.core.security_log import LoginFailureReason, security_event
from app.db.context import RequestDbDep
from app.schemas.auth import SessionOut, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])

LOGIN_COOKIE_PATH = "/api/auth"  # sent to the callback only

logger = logging.getLogger(__name__)


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def _clock(request: Request) -> Clock:
    clock: Clock = request.app.state.clock
    return clock


def _identity_provider(request: Request) -> IdentityProvider:
    provider: IdentityProvider = request.app.state.identity_provider
    return provider


SettingsDep = Annotated[Settings, Depends(_settings)]
ClockDep = Annotated[Clock, Depends(_clock)]
ProviderDep = Annotated[IdentityProvider, Depends(_identity_provider)]


def _app_redirect(settings: Settings, query: str = "", status_code: int = 303) -> RedirectResponse:
    """Redirect to the application's home page (`APP_BASE_URL/`), the login cookie cleared."""
    base = (settings.app_base_url or "").rstrip("/")
    response = RedirectResponse(f"{base}/{query}", status_code=status_code)
    response.delete_cookie(
        LOGIN_COOKIE,
        path=LOGIN_COOKIE_PATH,
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )
    return response


def _login_failed(settings: Settings, reason: LoginFailureReason) -> RedirectResponse:
    """Refused sign-in: logged with its reason only (FR-39, FR-41); no session is created."""
    security_event("login_failed", reason=reason)
    outcome = "cancelled" if reason == "cancelled" else "failed"
    return _app_redirect(settings, f"?login_error={outcome}")


def _same_secret(received: str, expected: str) -> bool:
    return hmac.compare_digest(received.encode("utf-8"), expected.encode("utf-8"))


@router.get("/login", name="auth_login", response_class=RedirectResponse, status_code=302)
async def auth_login(
    db: RequestDbDep, settings: SettingsDep, clock: ClockDep, provider: ProviderDep
) -> RedirectResponse:
    """Start the sign-in: store a login request (state, nonce, PKCE verifier) bound to this
    browser by the `forge_login` cookie, and send the browser to Google."""
    ttl = settings.login_request_ttl_minutes
    login = await create_login_request(db, now=clock.now(), ttl=timedelta(minutes=ttl))
    try:
        url = await provider.authorization_url(
            state=login.state,
            nonce=login.nonce,
            code_challenge=code_challenge_s256(login.code_verifier),
        )
    except IdentityProviderUnavailable:
        logger.warning("Google sign-in unavailable (not configured, or discovery unreachable)")
        return _app_redirect(settings, "?login_error=unavailable", status_code=302)
    response = RedirectResponse(url, status_code=302)
    response.set_cookie(
        LOGIN_COOKIE,
        login.token,
        max_age=ttl * 60,
        path=LOGIN_COOKIE_PATH,
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )
    return response


@router.get("/callback", name="auth_callback", response_class=RedirectResponse, status_code=303)
async def auth_callback(
    request: Request,
    db: RequestDbDep,
    settings: SettingsDep,
    clock: ClockDep,
    provider: ProviderDep,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
) -> RedirectResponse:
    """Google's answer. Refused unless it comes back to the browser that started the sign-in
    (cookie `forge_login`), with the same `state`, a single time, before expiry (FR-5)."""
    now = clock.now()
    login_token = request.cookies.get(LOGIN_COOKIE)
    # Consumed first, whatever happens next: a login request never serves twice.
    pending = await consume_login_request(db, login_token, now=now) if login_token else None
    if error is not None:
        return _login_failed(
            settings, "cancelled" if error == "access_denied" else "provider_error"
        )
    if login_token is None:
        return _login_failed(settings, "missing_login_cookie")
    if pending is None:  # unknown, already used or expired
        return _login_failed(settings, "replayed")
    if state is None or not _same_secret(state, pending.state):
        return _login_failed(settings, "state_mismatch")
    if not code:
        return _login_failed(settings, "provider_error")
    try:
        identity = await provider.exchange(
            code=code, code_verifier=pending.code_verifier, nonce=pending.nonce
        )
    except IdentityError as exc:
        return _login_failed(settings, exc.reason)

    user = await upsert_user(db, identity, now=now)
    # Session fixation (FR-10): always a new session; the one the browser presented is revoked.
    presented = request.cookies.get(SESSION_COOKIE)
    if presented:
        await revoke(db, hash_token(presented), now=now)
    session = await create_session(db, user.id, now=now)
    security_event("login_succeeded", user_id=user.id)

    response = _app_redirect(settings)
    response.set_cookie(
        SESSION_COOKIE,
        session.token,
        max_age=settings.session_max_age_days * 24 * 3600,
        path="/",
        secure=settings.cookie_secure,
        httponly=True,
        samesite="lax",
    )
    return response


@router.get("/session", name="auth_session", response_model=SessionOut)
async def auth_session(
    principal: Annotated[Principal, require_authenticated("session:read")],
) -> SessionOut:
    user = UserOut(
        id=principal.user_id,
        email=principal.email,
        name=principal.name,
        picture_url=principal.picture_url,
    )
    return SessionOut(user=user, csrf_token=principal.csrf_token)


@router.post("/logout", name="auth_logout", status_code=204, response_class=Response)
async def auth_logout(
    principal: Annotated[Principal, require_authenticated("session:logout")],
    db: RequestDbDep,
    settings: SettingsDep,
    clock: ClockDep,
) -> Response:
    """Sign-out: the session is revoked server-side at once (FR-9), a copied cookie is refused."""
    await revoke(db, principal.session_token_hash, now=clock.now())
    security_event("logout", user_id=principal.user_id)
    response = Response(status_code=204)
    response.delete_cookie(
        SESSION_COOKIE, path="/", secure=settings.cookie_secure, httponly=True, samesite="lax"
    )
    return response
