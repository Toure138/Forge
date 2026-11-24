"""Settings added by spec 001 (T007): cookies, durations, Google sign-in, database URL.

Every `Settings` here is built with `_env_file=None`: the developer's `.env` must not change the
outcome. Environment variables that would interfere are removed with `monkeypatch`.
"""

import pytest
from pydantic import ValidationError

from app.core.config import Settings

DURATION_VARS = (
    "SESSION_IDLE_TIMEOUT_HOURS",
    "SESSION_MAX_AGE_DAYS",
    "INVITATION_TTL_DAYS",
    "LOGIN_REQUEST_TTL_MINUTES",
)


@pytest.fixture(autouse=True)
def _no_settings_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        *DURATION_VARS,
        "ENVIRONMENT",
        "COOKIE_SECURE",
        "DATABASE_URL",
        "GOOGLE_CLIENT_ID",
        "GOOGLE_CLIENT_SECRET",
        "OIDC_CLOCK_SKEW_SECONDS",
    ):
        monkeypatch.delenv(var, raising=False)


def test_prod_refuses_insecure_cookies() -> None:
    with pytest.raises(ValidationError, match="COOKIE_SECURE"):
        Settings(_env_file=None, environment="prod", cookie_secure=False)

    # Secure cookies in prod, and insecure cookies outside prod, are both accepted.
    assert Settings(_env_file=None, environment="prod").cookie_secure is True
    assert Settings(_env_file=None, environment="dev", cookie_secure=False).cookie_secure is False


def test_cookie_secure_defaults_to_true() -> None:
    assert Settings(_env_file=None).cookie_secure is True


def test_durations_are_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    defaults = Settings(_env_file=None)
    assert defaults.session_idle_timeout_hours == 24
    assert defaults.session_max_age_days == 7
    assert defaults.invitation_ttl_days == 7
    assert defaults.login_request_ttl_minutes == 10

    for var, value in zip(DURATION_VARS, ("2", "3", "4", "5"), strict=True):
        monkeypatch.setenv(var, value)
    configured = Settings(_env_file=None)
    assert configured.session_idle_timeout_hours == 2
    assert configured.session_max_age_days == 3
    assert configured.invitation_ttl_days == 4
    assert configured.login_request_ttl_minutes == 5


@pytest.mark.parametrize(
    "field",
    [
        "session_idle_timeout_hours",
        "session_max_age_days",
        "invitation_ttl_days",
        "login_request_ttl_minutes",
    ],
)
def test_durations_must_be_positive(field: str) -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **{field: 0})


def test_oidc_clock_skew_is_configurable(monkeypatch: pytest.MonkeyPatch) -> None:
    """M1 (review): tolerated drift between Google's clock and ours on `iat` / `exp`."""
    assert Settings(_env_file=None).oidc_clock_skew_seconds == 60

    monkeypatch.setenv("OIDC_CLOCK_SKEW_SECONDS", "120")
    assert Settings(_env_file=None).oidc_clock_skew_seconds == 120

    for bound in (0, 300):
        settings = Settings(_env_file=None, oidc_clock_skew_seconds=bound)
        assert settings.oidc_clock_skew_seconds == bound
    for out_of_bounds in (-1, 301):
        with pytest.raises(ValidationError):
            Settings(_env_file=None, oidc_clock_skew_seconds=out_of_bounds)


def test_google_secret_is_not_repr() -> None:
    secret = "very-secret-value-123"
    settings = Settings(_env_file=None, google_client_id="client-id", google_client_secret=secret)
    assert secret not in repr(settings)
    assert secret not in str(settings)
    assert secret not in settings.model_dump_json()
    assert settings.google_client_secret is not None
    assert settings.google_client_secret.get_secret_value() == secret


def test_new_fields_are_optional_at_import() -> None:
    """`app = create_app()` runs at import: an old `.env` without these values must not break it."""
    settings = Settings(_env_file=None)
    assert settings.database_url is None
    assert settings.google_client_id is None
    assert settings.google_client_secret is None
    assert settings.oidc_redirect_uri is None
    assert settings.app_base_url is None


def test_missing_database_url_has_clear_message() -> None:
    with pytest.raises(RuntimeError, match=r"DATABASE_URL is not set.*\.env\.example"):
        Settings(_env_file=None).require_database_url()

    url = "postgresql+psycopg://forge_app:pw@127.0.0.1:5433/forge"
    assert Settings(_env_file=None, database_url=url).require_database_url() == url
