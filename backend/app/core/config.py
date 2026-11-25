"""Application settings, loaded from environment variables / `.env`.

Fields added by spec 001 are optional at import time: `app.main` builds `app = create_app()` at
module level, so an older `.env` must not break every import. A missing value is reported with a
clear message where it is needed (at startup for the database, at login for Google).
"""

from functools import lru_cache
from typing import Literal, Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Forge"
    environment: Literal["dev", "test", "prod"] = "dev"
    cors_origins: list[str] = ["http://localhost:5173"]

    # LLM — format "provider:model" (see langchain init_chat_model).
    llm_model: str = "anthropic:claude-opus-5-5"
    # Effort for Claude models: low | medium | high | xhigh | max.
    llm_effort: Literal["low", "medium", "high", "xhigh", "max"] = "high"
    llm_max_tokens: int = 64000

    anthropic_api_key: str | None = None

    # Database: the API connects as `forge_app` (subject to RLS). `DATABASE_OWNER_URL` is read by
    # Alembic only, never by the API.
    database_url: str | None = None

    # Google sign-in (OAuth client "Web application"). The redirect URI is read from here, never
    # derived from the `Host` header.
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    oidc_redirect_uri: str | None = None
    app_base_url: str | None = None
    # Tolerated drift between Google's clock and ours when checking the ID token's `iat` / `exp`.
    oidc_clock_skew_seconds: int = Field(default=60, ge=0, le=300)

    # Sessions & invitations. `Secure` cookies by default; only a local http setup turns it off.
    cookie_secure: bool = True
    session_idle_timeout_hours: int = Field(default=24, gt=0)
    session_max_age_days: int = Field(default=7, gt=0)
    invitation_ttl_days: int = Field(default=7, gt=0)
    login_request_ttl_minutes: int = Field(default=10, gt=0)

    @model_validator(mode="after")
    def _secure_cookies_in_prod(self) -> Self:
        if self.environment == "prod" and not self.cookie_secure:
            raise ValueError(
                "COOKIE_SECURE must be true when ENVIRONMENT=prod "
                "(session cookies must never travel over plain http)."
            )
        return self

    def require_database_url(self) -> str:
        """The API database URL, or a clear error if it is missing."""
        if not self.database_url:
            raise RuntimeError(
                "DATABASE_URL is not set: add it to the repo-root .env (see .env.example), "
                "e.g. postgresql+psycopg://forge_app:<password>@127.0.0.1:5433/forge"
            )
        return self.database_url


@lru_cache
def get_settings() -> Settings:
    return Settings()
