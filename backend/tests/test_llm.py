from langchain_anthropic import ChatAnthropic

from app.core.config import Settings
from app.core.llm import get_chat_model


def test_anthropic_model_uses_api_key_from_settings(monkeypatch) -> None:
    # pydantic-settings reads .env into Settings but does not export it to os.environ:
    # the key must be passed explicitly, not picked up from the process environment.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    settings = Settings(_env_file=None, anthropic_api_key="sk-ant-test-key")

    model = get_chat_model(settings)

    assert isinstance(model, ChatAnthropic)
    assert model.anthropic_api_key.get_secret_value() == "sk-ant-test-key"
