"""Single entry point for building chat models.

Every agent MUST get its model from `get_chat_model()` so the provider,
model id and generation settings stay configurable from `.env` and
replaceable by a fake model in tests.
"""

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from app.core.config import Settings, get_settings


def get_chat_model(settings: Settings | None = None) -> BaseChatModel:
    settings = settings or get_settings()
    provider, _, model = settings.llm_model.partition(":")

    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        # Current Claude models: adaptive thinking, depth controlled by effort.
        # Do NOT set temperature / budget_tokens / thinking=disabled: they are
        # rejected (HTTP 400) by Opus 5.5.
        return ChatAnthropic(  # type: ignore[call-arg]  # pydantic aliases
            model=model,
            max_tokens=settings.llm_max_tokens,
            thinking={"type": "adaptive"},
            output_config={"effort": settings.llm_effort},
            streaming=True,
        )

    # Any other provider supported by langchain (requires its integration package).
    return init_chat_model(settings.llm_model)
