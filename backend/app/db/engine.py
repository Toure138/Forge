"""Async engine of the API (role `forge_app`, subject to RLS).

Created in the application's lifespan and disposed on shutdown (`app/main.py`).
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.core.config import Settings


def create_engine(settings: Settings, **options: Any) -> AsyncEngine:
    """Engine on `DATABASE_URL` (clear error if missing). Connects lazily, on first use.

    `hide_parameters=True`: if SQL logging is switched on, statements are logged without their
    bound values, which include login `state` / `nonce` / PKCE verifier and CSRF tokens (FR-41).
    Error messages lose the values too."""
    return create_async_engine(
        settings.require_database_url(), **({"hide_parameters": True} | options)
    )
