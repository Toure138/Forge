from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langchain_core.language_models import BaseChatModel

from app.agents.registry import AgentRegistry
from app.api.routes import agents, health
from app.core.config import get_settings
from app.core.llm import get_chat_model


def create_app(model: BaseChatModel | None = None) -> FastAPI:
    """App factory. Tests pass a fake `model`; prod builds it from settings."""
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.registry = AgentRegistry(model or get_chat_model(settings))
        yield

    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router, prefix="/api")
    app.include_router(agents.router, prefix="/api")
    return app


app = create_app()
