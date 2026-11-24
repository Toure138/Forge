"""Application settings, loaded from environment variables / `.env`."""

from functools import lru_cache
from typing import Literal

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


@lru_cache
def get_settings() -> Settings:
    return Settings()
