"""Shared fixtures. Tests NEVER call a real LLM: use `FakeToolModel`."""

from collections.abc import Iterator, Sequence
from typing import Any

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from app.main import create_app


class FakeToolModel(GenericFakeChatModel):
    """Scripted chat model that accepts `bind_tools` (needed by agents)."""

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> "FakeToolModel":
        return self


def fake_model(*replies: AIMessage | str) -> FakeToolModel:
    msgs = [r if isinstance(r, AIMessage) else AIMessage(content=r) for r in replies]
    return FakeToolModel(messages=iter(msgs))


@pytest.fixture
def make_client() -> Iterator[Any]:
    clients: list[TestClient] = []

    def _make(*replies: AIMessage | str) -> TestClient:
        client = TestClient(create_app(model=fake_model(*replies)))
        client.__enter__()  # runs the lifespan
        clients.append(client)
        return client

    yield _make
    for c in clients:
        c.__exit__(None, None, None)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    import os

    if os.getenv("RUN_LIVE") == "1":
        return
    skip = pytest.mark.skip(reason="live test: set RUN_LIVE=1 to call the real model")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)
