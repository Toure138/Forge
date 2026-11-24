"""`create_app(model, *, settings, http_transport, clock)`: lifespan, docs, errors, event loop."""

import asyncio
import re
import sys

import pytest
from fastapi.routing import iter_route_contexts
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.core.config import Settings
from app.core.errors import ApiError
from app.main import create_app
from tests.conftest import client_for, fake_model, settings_for_tests
from tests.fakes.clock import FakeClock

DOC_PATHS = ("/docs", "/redoc", "/openapi.json")


def test_docs_and_openapi_not_served_outside_dev() -> None:
    """FR-25 / R9: outside dev, the docs are not functions exposed beside the exemption list."""
    for environment in ("test", "prod"):
        app = create_app(fake_model(), settings=settings_for_tests(environment=environment))
        with client_for(app) as client:
            for path in DOC_PATHS:
                assert client.get(path).status_code == 404, (environment, path)


def test_docs_served_in_dev() -> None:
    app = create_app(fake_model(), settings=settings_for_tests(environment="dev"))
    with client_for(app) as client:
        for path in DOC_PATHS:
            assert client.get(path).status_code == 200, path


def test_proactor_loop_is_refused_with_explicit_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """psycopg async cannot run on Windows' Proactor loop: refuse it at startup, with the fix."""
    if sys.platform == "win32":
        proactor = asyncio.ProactorEventLoop
    else:  # no Proactor loop outside Windows: stand one in, the guard only checks the type

        class proactor(asyncio.SelectorEventLoop):  # type: ignore[no-redef]
            pass

        monkeypatch.setattr(asyncio, "ProactorEventLoop", proactor, raising=False)

    app = create_app(fake_model(), settings=settings_for_tests())
    client = TestClient(app, backend_options={"loop_factory": proactor})
    with pytest.raises(RuntimeError, match=re.escape("--loop app.core.aio:selector_loop_factory")):
        client.__enter__()


def test_engine_disposed_on_shutdown() -> None:
    app = create_app(fake_model(), settings=settings_for_tests())
    with client_for(app) as client:
        engine: AsyncEngine = app.state.engine

        async def use_connection() -> int:
            async with engine.connect() as conn:
                return (await conn.execute(text("SELECT 1"))).scalar_one()

        assert client.portal.call(use_connection) == 1
        pool = engine.sync_engine.pool
        assert pool.checkedin() == 1  # the connection went back to the pool, still open

    assert pool.checkedin() == 0  # closed by dispose()
    assert engine.sync_engine.pool is not pool  # dispose() replaced the pool


def test_missing_database_url_fails_at_startup_with_clear_message() -> None:
    app = create_app(fake_model(), settings=Settings(_env_file=None, environment="test"))
    with pytest.raises(RuntimeError, match="DATABASE_URL is not set"), client_for(app):
        pass


def test_api_error_handler_registered() -> None:
    app = create_app(fake_model(), settings=settings_for_tests())

    async def boom() -> None:
        raise ApiError(409, "last_owner", "The organization must keep an owner.")

    app.add_api_route("/api/boom", boom)
    with client_for(app) as client:
        res = client.get("/api/boom")
    assert res.status_code == 409
    assert res.json() == {"code": "last_owner", "message": "The organization must keep an owner."}


def test_settings_and_clock_are_injected() -> None:
    settings, clock = settings_for_tests(), FakeClock()
    app = create_app(fake_model(), settings=settings, clock=clock)
    assert app.state.settings is settings
    assert app.state.clock is clock


def test_health_route_is_named_health() -> None:
    """`health` is the operation name listed in `authz.PUBLIC_ROUTES`."""
    app = create_app(fake_model(), settings=settings_for_tests())
    # FastAPI 0.165 includes routers lazily: `app.routes` holds `_IncludedRouter` objects, and
    # `iter_route_contexts` yields the effective routes.
    assert "health" in {route.name for route in iter_route_contexts(app.routes)}
    with client_for(app) as client:
        assert client.get("/api/health").json() == {"status": "ok"}
