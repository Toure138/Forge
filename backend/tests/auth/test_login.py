"""Sign-in with Google, BFF (`/api/auth/login`, `/api/auth/callback`): AC-1 to AC-6, AC-11."""

import logging
from dataclasses import replace
from http.cookies import Morsel, SimpleCookie

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient

from app.auth.sessions import hash_token
from tests.conftest import TEST_BASE_URL, client_for, create_test_app, login, start_login
from tests.fakes import google as fake_google_module
from tests.fakes.clock import FakeClock
from tests.fakes.google import GMAIL_ACCOUNT, WORKSPACE_ACCOUNT, FakeGoogle


def set_cookies(response: httpx.Response) -> dict[str, Morsel[str]]:
    """Cookies set by `response`, with their attributes."""
    cookies: dict[str, Morsel[str]] = {}
    for header in response.headers.get_list("set-cookie"):
        parsed: SimpleCookie = SimpleCookie()
        parsed.load(header)
        cookies.update(parsed)
    return cookies


def session_rows(pg_app: psycopg.Connection) -> list[tuple[object, ...]]:
    return pg_app.execute(
        "SELECT s.token_hash, s.revoked_at, u.google_sub, u.email"
        " FROM sessions s JOIN users u ON u.id = s.user_id ORDER BY s.created_at"
    ).fetchall()


@pytest.mark.parametrize("account", [WORKSPACE_ACCOUNT, GMAIL_ACCOUNT], ids=["workspace", "gmail"])
def test_login_succeeds_for_workspace_and_gmail_accounts(
    client: TestClient, fake_google: FakeGoogle, pg_app: psycopg.Connection, account
) -> None:
    """AC-1 (FR-1): any Google account with a verified email; the `hd` claim changes nothing."""
    response = login(client, fake_google, account)

    assert response.status_code == 303
    assert response.headers["location"] == f"{TEST_BASE_URL}/"
    session_token = set_cookies(response)["forge_session"].value
    rows = session_rows(pg_app)
    assert [(row[0], row[1], row[2], row[3]) for row in rows] == [
        (hash_token(session_token), None, account.sub, account.email)
    ]


def test_relogin_with_changed_email_keeps_same_account(
    client: TestClient, fake_google: FakeGoogle, pg_app: psycopg.Connection
) -> None:
    """AC-3 (FR-3): same Google `sub`, new email: same Forge user (hence same memberships, which
    are keyed by user id), profile refreshed."""
    login(client, fake_google, GMAIL_ACCOUNT)
    (first_id,) = pg_app.execute("SELECT id FROM users").fetchone() or (None,)

    renamed = replace(GMAIL_ACCOUNT, email="bob@new-domain.example", name="Bob D.")
    assert login(client, fake_google, renamed).status_code == 303

    users = pg_app.execute("SELECT id, email, email_normalized, name FROM users").fetchall()
    assert users == [(first_id, "bob@new-domain.example", "bob@new-domain.example", "Bob D.")]


def test_authorization_request_asks_only_basic_identity(client: TestClient) -> None:
    """AC-4 (FR-4) through the route; the `forge_login` cookie binds the answer to this browser."""
    response, params = start_login(client)

    location = response.headers["location"]
    assert location.startswith(fake_google_module.AUTHORIZATION_ENDPOINT + "?")
    assert set(params["scope"].split(" ")) == {"openid", "email", "profile"}
    assert "drive" not in location.lower()
    assert "access_type" not in params
    assert params["code_challenge_method"] == "S256"
    assert params["prompt"] == "select_account"
    assert params["state"] and params["nonce"] and params["code_challenge"]
    assert params["redirect_uri"] == f"{TEST_BASE_URL}/api/auth/callback"

    login_cookie = set_cookies(response)["forge_login"]
    assert login_cookie.value
    assert login_cookie["httponly"] is True
    assert login_cookie["secure"] is True
    assert login_cookie["samesite"].lower() == "lax"
    assert login_cookie["max-age"] == "600"
    assert login_cookie["path"] == "/api/auth"
    assert not login_cookie["domain"]
    # The state, nonce and verifier stay on the server: the cookie is an opaque random value.
    for secret in (params["state"], params["nonce"]):
        assert secret != login_cookie.value


def test_login_response_has_no_google_token_and_hardened_cookie(
    client: TestClient, fake_google: FakeGoogle
) -> None:
    """AC-6 (FR-5, FR-7)."""
    response = login(client, fake_google)

    assert response.status_code == 303
    assert response.headers["location"] == f"{TEST_BASE_URL}/"
    exposed = response.text + "\n".join(f"{k}: {v}" for k, v in response.headers.multi_items())
    google_tokens = fake_google.issued_id_tokens + fake_google.issued_access_tokens
    assert len(google_tokens) == 2
    for token in google_tokens:
        assert token not in exposed

    cookies = set_cookies(response)
    session = cookies["forge_session"]
    assert session["httponly"] is True
    assert session["secure"] is True
    assert session["samesite"].lower() == "lax"
    assert session["path"] == "/"
    assert not session["domain"]
    assert session["max-age"] == str(7 * 24 * 3600)
    # The login cookie is cleared, on the same path it was set on.
    assert cookies["forge_login"]["max-age"] == "0"
    assert cookies["forge_login"]["path"] == "/api/auth"
    assert "forge_login" not in client.cookies


# --- T020: refusals, replay, session fixation, platform events ---

FAILED = f"{TEST_BASE_URL}/?login_error=failed"


def security_events(caplog: pytest.LogCaptureFixture) -> list[dict[str, object]]:
    return [
        r.security_event  # type: ignore[attr-defined]
        for r in caplog.records
        if r.name == "forge.security"
    ]


def login_failed(reason: str) -> dict[str, object]:
    return {"name": "login_failed", "user_id": None, "organization_id": None, "reason": reason}


def session_count(pg_app: psycopg.Connection) -> int:
    row = pg_app.execute("SELECT count(*) FROM sessions").fetchone()
    assert row is not None
    return int(row[0])


def replant_cookie(client: TestClient, name: str, value: str, path: str) -> None:
    """Put a cookie back in the browser's jar (as a copy kept by the user or an attacker)."""
    client.cookies.set(name, value, domain="testserver.local", path=path)


@pytest.fixture(autouse=True)
def _capture_security_log(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="forge.security")


def test_successful_login_is_logged(
    client: TestClient, fake_google: FakeGoogle, pg_app: psycopg.Connection, caplog
) -> None:
    login(client, fake_google)
    (user_id,) = pg_app.execute("SELECT id FROM users").fetchone() or (None,)
    expected = {"name": "login_succeeded", "user_id": str(user_id)}
    assert security_events(caplog) == [expected | {"organization_id": None, "reason": None}]


def test_login_refused_when_email_not_verified(
    client: TestClient, fake_google: FakeGoogle, pg_app: psycopg.Connection, caplog
) -> None:
    """AC-2 (FR-2, FR-39)."""
    response = login(client, fake_google, replace(GMAIL_ACCOUNT, email_verified=False))

    assert response.status_code == 303
    assert response.headers["location"] == FAILED
    assert "forge_session" not in set_cookies(response)
    assert "forge_session" not in client.cookies
    assert session_count(pg_app) == 0
    assert pg_app.execute("SELECT count(*) FROM users").fetchone() == (0,)
    assert security_events(caplog) == [login_failed("email_not_verified")]


@pytest.mark.parametrize(
    ("case", "reason"),
    [
        ("missing_cookie", "missing_login_cookie"),
        ("state_mismatch", "state_mismatch"),
        ("replayed", "replayed"),
        ("nonce_mismatch", "invalid_id_token"),
        ("bad_audience", "invalid_id_token"),
        ("bad_issuer", "invalid_id_token"),
        ("bad_signature", "invalid_id_token"),
        ("expired_request", "replayed"),
    ],
)
def test_callback_rejected(
    client: TestClient,
    fake_google: FakeGoogle,
    clock: FakeClock,
    pg_app: psycopg.Connection,
    caplog: pytest.LogCaptureFixture,
    case: str,
    reason: str,
) -> None:
    """AC-5 (FR-5): an answer not started by this browser, or already used, creates no session.
    An expired login request is reported as "replayed" (unknown, used or expired: unusable)."""
    response, params = start_login(client)
    login_token = client.cookies["forge_login"]
    code = fake_google.authorize(response.headers["location"], GMAIL_ACCOUNT)
    callback = {"code": code, "state": params["state"]}
    sessions_before = 0
    if case == "replayed":
        first = client.get("/api/auth/callback", params=callback, follow_redirects=False)
        assert first.headers["location"] == f"{TEST_BASE_URL}/"
        sessions_before = 1
        client.cookies.clear()
        replant_cookie(client, "forge_login", login_token, "/api/auth")
        caplog.clear()
    elif case == "missing_cookie":
        client.cookies.clear()
    elif case == "state_mismatch":
        callback["state"] = "state-of-another-browser"
    elif case == "expired_request":
        clock.advance(minutes=10)
    fake_google.claim_overrides = {
        "nonce_mismatch": {"nonce": "nonce-of-another-login"},
        "bad_audience": {"aud": "another-client.apps.googleusercontent.com"},
        "bad_issuer": {"iss": "https://accounts.evil.example"},
    }.get(case, {})
    fake_google.sign_with_foreign_key = case == "bad_signature"

    rejected = client.get("/api/auth/callback", params=callback, follow_redirects=False)

    assert rejected.status_code == 303
    assert rejected.headers["location"] == FAILED
    assert "forge_session" not in set_cookies(rejected)
    assert session_count(pg_app) == sessions_before
    assert security_events(caplog) == [login_failed(reason)]


def test_state_mismatch_burns_the_login_request(
    client: TestClient, fake_google: FakeGoogle, pg_app: psycopg.Connection
) -> None:
    """A forged answer consumes the login request: the genuine one cannot be used after it."""
    response, params = start_login(client)
    login_token = client.cookies["forge_login"]
    code = fake_google.authorize(response.headers["location"], GMAIL_ACCOUNT)
    forged = {"code": "forged-code", "state": "forged-state"}
    first = client.get("/api/auth/callback", params=forged, follow_redirects=False)
    assert first.headers["location"] == FAILED

    replant_cookie(client, "forge_login", login_token, "/api/auth")
    genuine = {"code": code, "state": params["state"]}
    second = client.get("/api/auth/callback", params=genuine, follow_redirects=False)
    assert second.headers["location"] == FAILED
    assert session_count(pg_app) == 0


def test_callback_cancelled_by_user(
    client: TestClient, fake_google: FakeGoogle, pg_app: psycopg.Connection, caplog
) -> None:
    """The user refuses at Google: "error=access_denied" comes back instead of a code."""
    _, params = start_login(client)
    login_token = client.cookies["forge_login"]
    response = client.get(
        "/api/auth/callback",
        params={"error": "access_denied", "state": params["state"]},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert response.headers["location"] == f"{TEST_BASE_URL}/?login_error=cancelled"
    assert set_cookies(response)["forge_login"]["max-age"] == "0"
    assert session_count(pg_app) == 0
    assert security_events(caplog) == [login_failed("cancelled")]
    assert fake_google.count(fake_google_module.TOKEN_ENDPOINT) == 0
    used = pg_app.execute(
        "SELECT consumed_at IS NOT NULL FROM login_requests WHERE token_hash = %s",
        (hash_token(login_token),),
    ).fetchone()
    assert used == (True,)  # the login request cannot serve again


@pytest.mark.parametrize("cause", ["not_configured", "google_down"])
def test_login_unavailable_without_google_credentials(
    clean_db: None, clock: FakeClock, fake_google: FakeGoogle, cause: str
) -> None:
    """Missing Google credentials (or Google unreachable): a clear "login_error=unavailable"."""
    settings = {"google_client_id": None} if cause == "not_configured" else {}
    fake_google.unavailable = cause == "google_down"
    with client_for(create_test_app(clock, fake_google, **settings)) as client:
        response = client.get("/api/auth/login", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == f"{TEST_BASE_URL}/?login_error=unavailable"
    login_cookie = set_cookies(response).get("forge_login")
    assert login_cookie is None or login_cookie.value == ""  # no login request handed out
    if cause == "not_configured":
        assert fake_google.requests == []


def test_pre_login_session_id_never_authenticated(
    client: TestClient, fake_google: FakeGoogle, pg_app: psycopg.Connection
) -> None:
    """AC-11 (FR-10): no session exists before sign-in; a cookie planted beforehand never becomes
    one; each sign-in makes a new session and revokes the one the browser presented."""
    planted = "attacker-chosen-session-id"
    replant_cookie(client, "forge_session", planted, "/")
    first = set_cookies(login(client, fake_google))["forge_session"].value
    assert first != planted
    planted_rows = pg_app.execute(
        "SELECT count(*) FROM sessions WHERE token_hash = %s", (hash_token(planted),)
    ).fetchone()
    assert planted_rows == (0,)

    second = set_cookies(login(client, fake_google))["forge_session"].value
    assert second not in (first, planted)
    revoked = dict(
        pg_app.execute("SELECT token_hash, revoked_at IS NOT NULL FROM sessions").fetchall()
    )
    assert revoked == {hash_token(first): True, hash_token(second): False}
