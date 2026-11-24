"""Current session through the API: `GET /api/auth/session`, idle and absolute expiry, rejected
sessions logged, no call to Google once signed in (AC-8, AC-9, AC-13, FR-39)."""

import logging
import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient

from tests.conftest import login
from tests.fakes.clock import FakeClock
from tests.fakes.google import GMAIL_ACCOUNT, FakeGoogle

NOT_AUTHENTICATED = {"code": "not_authenticated", "message": "Authentication required."}


def security_events(caplog: pytest.LogCaptureFixture) -> list[dict[str, object]]:
    return [
        r.security_event  # type: ignore[attr-defined]
        for r in caplog.records
        if r.name == "forge.security"
    ]


@pytest.fixture(autouse=True)
def _capture_security_log(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="forge.security")


def test_session_endpoint_returns_user_and_csrf_token(
    client: TestClient, fake_google: FakeGoogle, pg_app: psycopg.Connection
) -> None:
    login(client, fake_google, GMAIL_ACCOUNT)
    response = client.get("/api/auth/session")

    assert response.status_code == 200
    row = pg_app.execute(
        "SELECT u.id, s.csrf_token FROM sessions s JOIN users u ON u.id = s.user_id"
    ).fetchone()
    assert row is not None
    user_id, csrf_token = row
    assert response.json() == {
        "user": {
            "id": str(user_id),
            "email": GMAIL_ACCOUNT.email,
            "name": GMAIL_ACCOUNT.name,
            "picture_url": GMAIL_ACCOUNT.picture,
        },
        "csrf_token": csrf_token,
    }


def test_anonymous_session_request_is_401_and_not_logged(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    response = client.get("/api/auth/session")
    assert response.status_code == 401
    assert response.json() == NOT_AUTHENTICATED
    assert security_events(caplog) == []  # a visitor without a cookie is not an incident


@pytest.mark.parametrize(
    ("idle", "status"), [({"hours": 23, "minutes": 59}, 200), ({"hours": 24, "minutes": 1}, 401)]
)
def test_session_idle_timeout(
    client: TestClient,
    fake_google: FakeGoogle,
    clock: FakeClock,
    idle: dict[str, float],
    status: int,
) -> None:
    """AC-8 (FR-8): refused after 24 h without activity, accepted just before."""
    login(client, fake_google)
    clock.advance(**idle)
    response = client.get("/api/auth/session")
    assert response.status_code == status
    if status == 401:
        assert response.json() == NOT_AUTHENTICATED


def test_session_absolute_timeout_despite_activity(
    client: TestClient, fake_google: FakeGoogle, clock: FakeClock
) -> None:
    """AC-9 (FR-8): used every 12 hours, the session still ends 7 days after its creation."""
    login(client, fake_google)
    for _ in range(13):  # 6 days and 12 hours of continuous use
        clock.advance(hours=12)
        assert client.get("/api/auth/session").status_code == 200
    clock.advance(hours=12)  # exactly 7 days
    response = client.get("/api/auth/session")
    assert response.status_code == 401
    assert response.json() == NOT_AUTHENTICATED


def test_authorized_request_works_when_identity_provider_down(
    client: TestClient, fake_google: FakeGoogle
) -> None:
    """AC-13 (FR-12): once signed in, nothing calls Google."""
    login(client, fake_google)
    fake_google.unavailable = True
    calls_before = len(fake_google.requests)

    assert client.get("/api/auth/session").status_code == 200
    assert len(fake_google.requests) == calls_before


@pytest.mark.parametrize("reason", ["unknown", "revoked", "idle_expired", "absolute_expired"])
def test_rejected_session_is_logged(
    client: TestClient,
    fake_google: FakeGoogle,
    clock: FakeClock,
    pg_app: psycopg.Connection,
    caplog: pytest.LogCaptureFixture,
    reason: str,
) -> None:
    """FR-39: a request with an expired or revoked session is logged on the platform log, with
    the user's Forge id when known (never the cookie)."""
    login(client, fake_google)
    row = pg_app.execute("SELECT id FROM users").fetchone()
    assert row is not None
    user_id: uuid.UUID = row[0]
    if reason == "unknown":
        client.cookies.clear()
        client.cookies.set("forge_session", "no-such-session", domain="testserver.local", path="/")
    elif reason == "revoked":
        pg_app.execute("UPDATE sessions SET revoked_at = %s", (clock.now(),))
        pg_app.commit()  # visible to the API (pg_app is not in autocommit mode)
    elif reason == "idle_expired":
        clock.advance(hours=24)
    else:
        for _ in range(14):
            clock.advance(hours=12)
            client.get("/api/auth/session")
    caplog.clear()

    response = client.get("/api/auth/session")

    assert response.status_code == 401
    assert response.json() == NOT_AUTHENTICATED
    expected_user = None if reason == "unknown" else str(user_id)
    assert security_events(caplog) == [
        {
            "name": "session_rejected",
            "user_id": expected_user,
            "organization_id": None,
            "reason": reason,
        }
    ]


def test_session_activity_is_recorded(
    client: TestClient, fake_google: FakeGoogle, clock: FakeClock, pg_app: psycopg.Connection
) -> None:
    """Each valid request moves `last_seen_at` (the idle timeout restarts)."""
    login(client, fake_google)
    clock.advance(hours=5)
    assert client.get("/api/auth/session").status_code == 200
    assert pg_app.execute("SELECT last_seen_at FROM sessions").fetchone() == (clock.now(),)


def test_logout_revokes_server_side_session(
    client: TestClient,
    fake_google: FakeGoogle,
    pg_app: psycopg.Connection,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """AC-10 (FR-9): after sign-out, a copy of the cookie is refused (401)."""
    login(client, fake_google)
    copy = client.cookies["forge_session"]
    csrf_token = client.get("/api/auth/session").json()["csrf_token"]
    row = pg_app.execute("SELECT id FROM users").fetchone()
    pg_app.commit()
    assert row is not None
    caplog.clear()

    response = client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf_token})

    assert response.status_code == 204
    assert response.content == b""
    cleared = [h for h in response.headers.get_list("set-cookie") if h.startswith("forge_session=")]
    assert len(cleared) == 1 and "Max-Age=0" in cleared[0] and "Path=/" in cleared[0]
    assert "forge_session" not in client.cookies
    assert pg_app.execute("SELECT revoked_at IS NOT NULL FROM sessions").fetchone() == (True,)
    assert security_events(caplog) == [
        {"name": "logout", "user_id": str(row[0]), "organization_id": None, "reason": None}
    ]

    client.cookies.set("forge_session", copy, domain="testserver.local", path="/")
    replay = client.get("/api/auth/session")
    assert replay.status_code == 401
    assert replay.json() == NOT_AUTHENTICATED


def test_logout_without_session_is_401(client: TestClient) -> None:
    response = client.post("/api/auth/logout", headers={"X-CSRF-Token": "anything"})
    assert response.status_code == 401
    assert response.json() == NOT_AUTHENTICATED
