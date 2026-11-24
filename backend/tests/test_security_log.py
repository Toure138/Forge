"""Platform security log (`forge.security`) and access-log redaction (FR-39, FR-41, AC-51)."""

import inspect
import logging
import uuid
from dataclasses import replace
from typing import get_args

import pytest
from fastapi.testclient import TestClient

from app.core import security_log
from app.core.security_log import (
    LoginFailureReason,
    RedactQueryFilter,
    SecurityEventName,
    SessionRejectionReason,
    security_event,
)
from app.main import create_app
from tests.conftest import (
    TEST_BASE_URL,
    client_for,
    create_test_app,
    fake_model,
    login,
    settings_for_tests,
    start_login,
)
from tests.fakes.clock import FakeClock
from tests.fakes.google import GMAIL_ACCOUNT, FakeGoogle


def test_security_event_has_closed_field_list(caplog: pytest.LogCaptureFixture) -> None:
    """Only `name`, `user_id`, `organization_id` and `reason` can be logged: a closed signature,
    so that no token can be passed by mistake. Names and reasons come from the plan's lists."""
    parameters = inspect.signature(security_event).parameters
    assert list(parameters) == ["name", "user_id", "organization_id", "reason"]
    assert all(
        p.kind is inspect.Parameter.KEYWORD_ONLY for n, p in parameters.items() if n != "name"
    )

    assert set(get_args(SecurityEventName)) == {
        "login_succeeded",
        "login_failed",
        "logout",
        "session_rejected",
        "organization_access_denied",
    }
    assert set(get_args(LoginFailureReason)) == {
        "email_not_verified",
        "state_mismatch",
        "missing_login_cookie",
        "replayed",
        "invalid_id_token",
        "provider_error",
        "cancelled",
    }
    assert set(get_args(SessionRejectionReason)) == {
        "unknown",
        "revoked",
        "idle_expired",
        "absolute_expired",
    }

    user_id, org_id = uuid.uuid4(), uuid.uuid4()
    with caplog.at_level(logging.INFO, logger="forge.security"):
        security_event("login_failed", reason="state_mismatch")
        security_event("organization_access_denied", user_id=user_id, organization_id=org_id)
    first, second = (r for r in caplog.records if r.name == "forge.security")
    assert first.security_event == {  # type: ignore[attr-defined]
        "name": "login_failed",
        "user_id": None,
        "organization_id": None,
        "reason": "state_mismatch",
    }
    assert second.security_event == {  # type: ignore[attr-defined]
        "name": "organization_access_denied",
        "user_id": str(user_id),
        "organization_id": str(org_id),
        "reason": None,
    }
    assert "login_failed" in first.getMessage() and "state_mismatch" in first.getMessage()
    assert str(org_id) in second.getMessage()


@pytest.mark.parametrize(
    ("name", "reason"),
    [
        ("token_leaked", None),  # unknown event
        ("login_failed", "because"),  # unknown reason
        ("login_failed", None),  # a failure always says why
        ("session_rejected", "state_mismatch"),  # reason of another event
        ("logout", "unknown"),  # this event has no reason
    ],
)
def test_security_event_refuses_values_outside_closed_lists(name: str, reason: str | None) -> None:
    with pytest.raises(ValueError):
        security_event(name, reason=reason)  # type: ignore[arg-type]


def access_record(path: str) -> logging.LogRecord:
    """A record as uvicorn's access logger emits it."""
    return logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        __file__,
        0,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:50000", "GET", path, "1.1", 303),
        None,
    )


def test_redact_filter_masks_auth_query_strings() -> None:
    record = access_record("/api/auth/callback?code=4%2F0Secret-code&state=the-state-value")
    assert RedactQueryFilter().filter(record) is True  # the line is kept, only redacted
    message = record.getMessage()
    assert "/api/auth/callback?[redacted]" in message
    assert "Secret-code" not in message and "the-state-value" not in message

    for path in ("/api/agents?x=1", "/api/health", "/api/auth/session", "/api/authx?code=1"):
        untouched = access_record(path)
        RedactQueryFilter().filter(untouched)
        assert f"GET {path} HTTP" in untouched.getMessage()


def test_security_logger_name() -> None:
    assert security_log.logger.name == "forge.security"


# --- T024: end to end ---


def _is_test_client_record(record: logging.LogRecord) -> bool:
    """`TestClient` logs its own requests (the browser side, logger `httpx` or `httpx2`), not the
    server; the application's calls to Google stay checked."""
    return record.name.startswith("httpx") and "https://testserver/" in record.getMessage()


def test_login_logout_events_logged_without_secrets(
    client: TestClient, fake_google: FakeGoogle, caplog: pytest.LogCaptureFixture
) -> None:
    """AC-51 (FR-39, FR-41): a successful sign-in, a refused one and a sign-out are logged, and no
    record of any logger carries a token, a cookie, the CSRF token, the code, the state or the
    PKCE verifier."""
    caplog.set_level(logging.INFO)  # every logger, at the level a server would run with
    secrets: list[str] = []

    # Successful sign-in.
    response, params = start_login(client)
    secrets += [client.cookies["forge_login"], params["state"], params["nonce"]]
    code = fake_google.authorize(response.headers["location"], GMAIL_ACCOUNT)
    callback = client.get(
        "/api/auth/callback",
        params={"code": code, "state": params["state"]},
        follow_redirects=False,
    )
    assert callback.headers["location"] == f"{TEST_BASE_URL}/"
    session_token = client.cookies["forge_session"]
    csrf_token = client.get("/api/auth/session").json()["csrf_token"]
    secrets += [session_token, csrf_token]

    # Sign-out.
    assert client.post("/api/auth/logout", headers={"X-CSRF-Token": csrf_token}).status_code == 204

    # Refused sign-in (unverified email).
    response, params = start_login(client)
    secrets += [client.cookies["forge_login"], params["state"], params["nonce"]]
    unverified = replace(GMAIL_ACCOUNT, email_verified=False)
    code = fake_google.authorize(response.headers["location"], unverified)
    refused = client.get(
        "/api/auth/callback",
        params={"code": code, "state": params["state"]},
        follow_redirects=False,
    )
    assert refused.headers["location"] == f"{TEST_BASE_URL}/?login_error=failed"

    secrets += fake_google.issued_codes + fake_google.received_code_verifiers
    secrets += fake_google.issued_id_tokens + fake_google.issued_access_tokens
    assert len(secrets) == 16 and all(len(secret) >= 16 for secret in secrets)

    events = [
        (r.security_event["name"], r.security_event["reason"])  # type: ignore[attr-defined]
        for r in caplog.records
        if r.name == "forge.security"
    ]
    assert events == [
        ("login_succeeded", None),
        ("logout", None),
        ("login_failed", "email_not_verified"),
    ]
    server_records = [r for r in caplog.records if not _is_test_client_record(r)]
    assert server_records
    for record in server_records:
        text = f"{record.getMessage()} {record.__dict__!r}"
        leaked = [secret for secret in secrets if secret in text]
        assert leaked == [], f"secret in log record {record.name}: {record.getMessage()}"
        assert GMAIL_ACCOUNT.email not in text  # minimization: Forge ids only


def test_app_installs_access_log_redaction() -> None:
    access_logger = logging.getLogger("uvicorn.access")
    for existing in [f for f in access_logger.filters if isinstance(f, RedactQueryFilter)]:
        access_logger.removeFilter(existing)

    app = create_app(fake_model(), settings=settings_for_tests())
    with client_for(app):
        installed = [f for f in access_logger.filters if isinstance(f, RedactQueryFilter)]
        assert len(installed) == 1
    with client_for(app):  # a second start does not stack filters
        installed = [f for f in access_logger.filters if isinstance(f, RedactQueryFilter)]
        assert len(installed) == 1


def test_sql_parameters_are_never_logged(
    clean_db: None, clock: FakeClock, fake_google: FakeGoogle, caplog: pytest.LogCaptureFixture
) -> None:
    """With SQL logging switched on (`sqlalchemy.engine` at INFO before startup, as a server's
    logging configuration could do), statements are logged without their parameters: the state,
    nonce, verifier and CSRF token written to the database never reach the log."""
    caplog.set_level(logging.INFO, logger="sqlalchemy.engine")
    with client_for(create_test_app(clock, fake_google)) as client:
        login(client, fake_google)
        csrf_token = client.get("/api/auth/session").json()["csrf_token"]
    sql_records = [r for r in caplog.records if r.name.startswith("sqlalchemy.engine")]
    assert any("INSERT INTO sessions" in r.getMessage() for r in sql_records)
    secrets = [csrf_token, *fake_google.received_code_verifiers]
    for record in sql_records:
        text = f"{record.getMessage()} {record.__dict__!r}"
        assert [s for s in secrets if s in text] == [], record.getMessage()
