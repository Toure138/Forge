"""Alembic environment: synchronous psycopg, connected as `forge_owner`.

URL resolution: `sqlalchemy.url` if the caller set it (`Config.set_main_option`, used by the
tests to migrate `forge_test`), otherwise `DATABASE_OWNER_URL` (environment, then repo-root `.env`).
The API's `DATABASE_URL` (forge_app) is never used here: forge_app owns nothing and cannot do DDL.
"""

from logging.config import fileConfig
from pathlib import Path

from alembic import context
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import create_engine, pool

from app.db.tables import metadata

REPO_ROOT = Path(__file__).resolve().parents[2]

config = context.config

if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

target_metadata = metadata


class MigrationSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    database_owner_url: str | None = None


def database_url() -> str:
    url = config.get_main_option("sqlalchemy.url") or MigrationSettings().database_owner_url
    if not url:
        raise RuntimeError(
            "DATABASE_OWNER_URL is not set: add it to the repo-root .env (see .env.example)."
        )
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
