"""Shared fixtures. Tests NEVER call a real LLM: use `FakeToolModel`.

Database tests run against the `forge_test` database of the Docker `db` service. They never
skip: if Postgres is unreachable they fail and tell you to start it.

Windows: psycopg's async API refuses the default Proactor loop. Async tests run on a selector loop
(`pytest_asyncio_loop_factories` below) and `client_for` gives `TestClient` a selector loop.
"""

import asyncio
import socket
import sys
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import psycopg
import pytest
from alembic import command as alembic_command
from alembic.config import Config as AlembicConfig
from fastapi import FastAPI
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage
from psycopg import sql
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.aio import selector_loop_factory
from app.core.config import Settings
from app.db import tables
from app.db.context import RequestDb, request_transaction
from app.db.engine import create_engine
from app.main import create_app
from tests.fakes.clock import FakeClock
from tests.fakes.google import CLIENT_ID, CLIENT_SECRET, GMAIL_ACCOUNT, FakeGoogle, GoogleAccount

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent

if sys.platform == "win32":
    # pytest-asyncio 1.5 hook (spec 001 T001): every async test and fixture runs on a selector
    # loop. Defined on Windows only, because the hook must never return None.
    def pytest_asyncio_loop_factories(
        config: pytest.Config, item: pytest.Item
    ) -> dict[str, Callable[[], asyncio.AbstractEventLoop]]:
        return {"selector": selector_loop_factory}


class NetworkBlockedError(RuntimeError):
    """Raised when a test tries to open a connection outside the loopback (AC-58)."""


LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def _refuse_non_loopback(address: Any) -> None:
    # AF_INET (host, port) and AF_INET6 (host, port, flowinfo, scope_id). Other families (e.g.
    # AF_UNIX paths) are local by nature.
    if isinstance(address, tuple) and address and isinstance(address[0], str):
        host = address[0]
        if host not in LOOPBACK_HOSTS:
            raise NetworkBlockedError(
                f"Outbound network access is blocked in tests: connection to {host!r} refused "
                f"(allowed: {', '.join(sorted(LOOPBACK_HOSTS))}). Fake Google with "
                "httpx.MockTransport and the LLM with fake_model()."
            )


@pytest.fixture(autouse=True)
def _block_outbound_network(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No test reaches Google, the LLM or anything else outside the machine (AC-58).

    libpq (psycopg) opens its sockets in C, and `TestClient` uses none: both stay unaffected. The
    selector loop's self-pipe uses a loopback socket pair on Windows: allowed. Only `live` tests
    (opt-in with RUN_LIVE=1, they call the real LLM on purpose) are left unguarded.
    """
    if request.node.get_closest_marker("live") is not None:
        return
    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex

    def connect(self: socket.socket, address: Any) -> None:
        _refuse_non_loopback(address)
        real_connect(self, address)

    def connect_ex(self: socket.socket, address: Any) -> int:
        _refuse_non_loopback(address)
        return real_connect_ex(self, address)

    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)


TEST_BASE_URL = "https://testserver"


def client_for(app: FastAPI) -> TestClient:
    """`TestClient` over https (`Secure` cookies are sent, as in production), on a selector loop
    under Windows. Use it as a context manager to run the lifespan."""
    options = {"loop_factory": asyncio.SelectorEventLoop} if sys.platform == "win32" else {}
    return TestClient(app, base_url=TEST_BASE_URL, backend_options=options)


class DatabaseTestSettings(BaseSettings):
    """`TEST_*` URLs, read from the environment or the repo-root `.env` (tests only)."""

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    test_database_url: str | None = None
    test_database_owner_url: str | None = None


def libpq_url(sqlalchemy_url: str) -> str:
    """`postgresql+psycopg://…` (SQLAlchemy) → `postgresql://…` (psycopg / libpq)."""
    url = make_url(sqlalchemy_url).set(drivername="postgresql")
    return url.render_as_string(hide_password=False)


def database_url_from_env(var: str) -> str:
    """Return the SQLAlchemy URL in `var`, or fail the test with an explicit message."""
    value = getattr(DatabaseTestSettings(), var.lower())
    if not value:
        pytest.fail(
            f"{var} is not set: add it to the repo-root .env (see .env.example).", pytrace=False
        )
    return str(value)


def connect_test_database(var: str) -> psycopg.Connection:
    """Synchronous psycopg connection to `forge_test`; fails (never skips) if unreachable."""
    url = database_url_from_env(var)
    try:
        return psycopg.connect(libpq_url(url), connect_timeout=5)
    except psycopg.OperationalError as exc:
        cause = str(exc).strip().splitlines()[0] if str(exc).strip() else type(exc).__name__
    pytest.fail(
        "Test database unreachable: lancez `docker compose up -d db` "
        f"(from the repo root, Docker Desktop running). Cause: {cause}",
        pytrace=False,
    )


@pytest.fixture
def pg_owner() -> Iterator[psycopg.Connection]:
    """Connection to `forge_test` as `forge_owner` (schema owner, used by migrations)."""
    conn = connect_test_database("TEST_DATABASE_OWNER_URL")
    try:
        yield conn
    finally:
        conn.close()


@pytest.fixture
def pg_app() -> Iterator[psycopg.Connection]:
    """Connection to `forge_test` as `forge_app` (the API role: subject to RLS)."""
    conn = connect_test_database("TEST_DATABASE_URL")
    try:
        yield conn
    finally:
        conn.close()


def alembic_config() -> AlembicConfig:
    """Alembic config that migrates `forge_test` as `forge_owner` (not the dev database)."""
    url = database_url_from_env("TEST_DATABASE_OWNER_URL")
    connect_test_database("TEST_DATABASE_OWNER_URL").close()  # explicit failure if unreachable
    cfg = AlembicConfig(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))  # configparser escaping
    cfg.attributes["configure_logger"] = False  # keep pytest's logging (caplog) untouched
    return cfg


@pytest.fixture(scope="session")
def migrated_db() -> AlembicConfig:
    """`forge_test` migrated to `head` once per session; returns the Alembic config."""
    cfg = alembic_config()
    alembic_command.upgrade(cfg, "head")
    return cfg


@pytest.fixture
def clean_db(migrated_db: AlembicConfig) -> Iterator[None]:
    """Empty every table before the test, as owner (RLS does not apply to TRUNCATE)."""
    names = sql.SQL(", ").join(sql.Identifier(t.name) for t in tables.metadata.sorted_tables)
    with connect_test_database("TEST_DATABASE_OWNER_URL") as conn:
        conn.execute(sql.SQL("TRUNCATE {} RESTART IDENTITY CASCADE").format(names))
    yield


def create_test_engine(**options: Any) -> AsyncEngine:
    """Async engine on `forge_test` as `forge_app` (the API role), built like the API's."""
    url = database_url_from_env("TEST_DATABASE_URL")
    return create_engine(Settings(_env_file=None, database_url=url), **options)


RequestDbFactory = Callable[[], AbstractAsyncContextManager[RequestDb]]


@pytest.fixture
async def request_db_factory(clean_db: None) -> AsyncIterator[RequestDbFactory]:
    """`async with request_db_factory() as db:` = one request transaction on an empty `forge_test`
    (commit at the end, rollback on exception), exactly like the `request_db` dependency."""
    engine = create_test_engine()
    try:
        yield lambda: request_transaction(engine)
    finally:
        await engine.dispose()


class FakeToolModel(GenericFakeChatModel):
    """Scripted chat model that accepts `bind_tools` (needed by agents)."""

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "FakeToolModel":
        return self


def fake_model(*replies: AIMessage | str) -> FakeToolModel:
    msgs = [r if isinstance(r, AIMessage) else AIMessage(content=r) for r in replies]
    return FakeToolModel(messages=iter(msgs))


def settings_for_tests(**overrides: Any) -> Settings:
    """Application settings for tests: never read from `.env`; `forge_test` database; `Secure`
    cookies as in production (the test client talks https)."""
    values: dict[str, Any] = {
        "environment": "test",
        "database_url": database_url_from_env("TEST_DATABASE_URL"),
        "cookie_secure": True,
        # Google sign-in against the fake Google (`tests/fakes/google.py`).
        "google_client_id": CLIENT_ID,
        "google_client_secret": CLIENT_SECRET,
        "oidc_redirect_uri": f"{TEST_BASE_URL}/api/auth/callback",
        "app_base_url": TEST_BASE_URL,
    }
    return Settings(_env_file=None, **(values | overrides))


@pytest.fixture
def make_client() -> Iterator[Any]:
    clients: list[TestClient] = []

    def _make(*replies: AIMessage | str) -> TestClient:
        client = client_for(create_app(model=fake_model(*replies), settings=settings_for_tests()))
        client.__enter__()  # runs the lifespan
        clients.append(client)
        return client

    yield _make
    for c in clients:
        c.__exit__(None, None, None)


# --- Sign-in against the fake Google ---


@pytest.fixture
def clock() -> FakeClock:
    """The application's clock in API tests (`clock.advance(...)` moves time forward)."""
    return FakeClock()


@pytest.fixture
def fake_google(clock: FakeClock) -> FakeGoogle:
    """The fake Google the application talks to (through `httpx.MockTransport`)."""
    return FakeGoogle(clock)


def create_test_app(
    clock: FakeClock, fake_google: FakeGoogle, *replies: AIMessage | str, **settings: Any
) -> FastAPI:
    """Application on `forge_test`, with the fake Google, the fake clock and a fake model."""
    return create_app(
        fake_model(*replies),
        settings=settings_for_tests(**settings),
        http_transport=httpx.MockTransport(fake_google.handler),
        clock=clock,
    )


@pytest.fixture
def client(clean_db: None, clock: FakeClock, fake_google: FakeGoogle) -> Iterator[TestClient]:
    """Test client (lifespan running) on an empty `forge_test`, with the fake Google and clock.

    One client = one browser (one cookie jar). Do not open a second `TestClient` on the same app:
    each one runs the lifespan, which replaces `app.state.engine`."""
    with client_for(create_test_app(clock, fake_google)) as test_client:
        yield test_client


@pytest.fixture
def world(clean_db: None, clock: FakeClock, fake_google: FakeGoogle) -> Iterator[Any]:
    """Acme / Globex world of `tests/factories.py` (people with valid sessions, memberships), on
    an empty `forge_test`. `world.client_as(world.bob, world.acme)` is Bob's browser in Acme."""
    from tests.factories import build_world  # imports tests.db.conftest, which imports this module

    with connect_test_database("TEST_DATABASE_URL") as conn:
        yield from build_world(clock, fake_google, conn)


@pytest.fixture
def member_client(world: Any) -> Callable[..., TestClient]:
    """`member_client(*replies)`: Carole, Member of Acme, signed in with Acme active, on an
    application whose fake model answers with `replies` (agent routes need `agents:use`)."""

    def _make(*replies: AIMessage | str) -> TestClient:
        client: TestClient = world.client_as(world.carole, world.acme, replies=replies)
        return client

    return _make


def start_login(client: TestClient) -> tuple[httpx.Response, dict[str, str]]:
    """`GET /api/auth/login` (no redirect followed); returns the response and the parameters of
    the authorization URL the browser is sent to."""
    response = client.get("/api/auth/login", follow_redirects=False)
    assert response.status_code == 302, response.text
    query = urlsplit(response.headers["location"]).query
    return response, {k: v[0] for k, v in parse_qs(query).items()}


def login(
    client: TestClient, fake_google: FakeGoogle, account: GoogleAccount = GMAIL_ACCOUNT
) -> httpx.Response:
    """The whole sign-in, as a browser does it: `/login`, consent at Google, `/callback`.
    Returns the callback response (redirect not followed); the client keeps the cookies."""
    response, params = start_login(client)
    code = fake_google.authorize(response.headers["location"], account)
    return client.get(
        "/api/auth/callback",
        params={"code": code, "state": params["state"]},
        follow_redirects=False,
    )


# Opt-in markers: skipped unless their variable is "1".
OPT_IN_MARKERS = {
    "live": ("RUN_LIVE", "live test: set RUN_LIVE=1 to call the real model"),
    "infra": (
        "RUN_INFRA",
        "infra test: set RUN_INFRA=1 to drive Docker and the scripts (forge-smoke project)",
    ),
}


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    import os

    for marker, (variable, reason) in OPT_IN_MARKERS.items():
        if os.getenv(variable) == "1":
            continue
        skip = pytest.mark.skip(reason=reason)
        for item in items:
            if marker in item.keywords:
                item.add_marker(skip)
