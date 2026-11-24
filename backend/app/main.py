import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.language_models import BaseChatModel

from app.agents.registry import AgentRegistry
from app.api.routes import agents, auth, health
from app.auth.google import GoogleOIDCProvider
from app.core.aio import ensure_selector_event_loop
from app.core.clock import Clock, SystemClock
from app.core.config import Settings, get_settings
from app.core.errors import add_error_handlers
from app.core.llm import get_chat_model
from app.core.security_log import RedactQueryFilter
from app.db.engine import create_engine

GOOGLE_TIMEOUT = httpx.Timeout(10.0)


def install_access_log_redaction() -> None:
    """uvicorn's access log would print `/api/auth/callback?code=…&state=…`: hide the query string
    of `/api/auth/*` (FR-41). Idempotent; done at startup, after uvicorn configured its loggers."""
    access_logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(f, RedactQueryFilter) for f in access_logger.filters):
        access_logger.addFilter(RedactQueryFilter())


def create_app(
    model: BaseChatModel | None = None,
    *,
    settings: Settings | None = None,
    http_transport: httpx.AsyncBaseTransport | None = None,
    clock: Clock | None = None,
) -> FastAPI:
    """App factory. Tests pass a fake `model`, test `settings`, a fake `clock` and an
    `http_transport` that simulates Google; production builds everything from the environment.

    `app.state` holds `settings`, `clock` and `http_transport` from creation, and `engine`,
    `identity_provider` (Google, through an `httpx.AsyncClient` on `http_transport`) and `registry`
    while the lifespan runs.
    """
    settings = settings or get_settings()
    docs = settings.environment == "dev"  # outside dev, no exposed function beyond the API (R9)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        ensure_selector_event_loop()
        install_access_log_redaction()
        app.state.engine = create_engine(settings)
        google_http = httpx.AsyncClient(transport=http_transport, timeout=GOOGLE_TIMEOUT)
        try:
            app.state.identity_provider = GoogleOIDCProvider(settings, google_http, app.state.clock)
            app.state.registry = AgentRegistry(model or get_chat_model(settings))
            yield
        finally:
            await google_http.aclose()
            await app.state.engine.dispose()

    app = FastAPI(
        title=settings.app_name,
        lifespan=lifespan,
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
    )
    app.state.settings = settings
    app.state.clock = clock or SystemClock()
    app.state.http_transport = http_transport  # None = httpx's default (real network)
    add_error_handlers(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router, prefix="/api")
    app.include_router(auth.router, prefix="/api")
    app.include_router(agents.router, prefix="/api")
    return app


app = create_app()
