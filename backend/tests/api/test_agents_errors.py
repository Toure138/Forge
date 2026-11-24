"""Agent errors (constitution VIII, FR-41, FR-47; review m1): an exception raised while the agent
runs is logged on the server (logger `forge.agents`: the exception, the agent, the organization
and the user, never the user's message), and the client only gets a generic message: an SSE
`error` event for `stream`, a 500 `agent_error` for `invoke`. The exception's own text (an internal
detail) never reaches the client."""

import logging
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from tests.api.test_conversations import act_as, sse_events
from tests.conftest import FakeToolModel, client_for, settings_for_tests
from tests.factories import World

INTERNAL_DETAIL = "upstream said: key sk-internal-9f8e7d is over quota"
USER_MESSAGE = "Mon numéro de carte est 4970-1234"
GENERIC = "The agent encountered an error."


class FailingModel(FakeToolModel):
    """Fake model that fails on every call, with an internal detail in the exception."""

    def _generate(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError(INTERNAL_DETAIL)

    def _stream(self, *args: Any, **kwargs: Any) -> Iterator[Any]:
        raise RuntimeError(INTERNAL_DETAIL)


@pytest.fixture
def failing_client(world: World) -> Iterator[TestClient]:
    """Carole (Member of Acme) on an application whose model always fails."""
    app = create_app(
        FailingModel(messages=iter([])),
        settings=settings_for_tests(),
        http_transport=httpx.MockTransport(world.fake_google.handler),
        clock=world.clock,
    )
    with client_for(app) as client:
        yield act_as(client, world.carole, world.acme)


@pytest.fixture(autouse=True)
def _capture_agent_log(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger="forge.agents")


def assert_logged(caplog: pytest.LogCaptureFixture, world: World, mode: str) -> None:
    """One ERROR record on `forge.agents`, with the exception and who / where it happened; neither
    the user's message nor a session secret in it."""
    [record] = [r for r in caplog.records if r.name == "forge.agents"]
    assert record.levelno == logging.ERROR
    assert record.exc_info is not None
    assert isinstance(record.exc_info[1], RuntimeError)
    assert record.agent_error == {  # type: ignore[attr-defined]
        "agent": "assistant",
        "mode": mode,
        "organization_id": str(world.acme),
        "user_id": str(world.carole.id),
    }
    logged = logging.Formatter().format(record)
    assert INTERNAL_DETAIL in logged  # the traceback is on the server side
    for field in ("assistant", str(world.acme), str(world.carole.id)):
        assert field in record.getMessage()
    for forbidden in (USER_MESSAGE, "4970", world.carole.session_token, world.carole.csrf_token):
        assert forbidden not in logged


def test_stream_agent_error_is_logged_and_generic(
    world: World, failing_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with failing_client.stream(
        "POST", "/api/agents/assistant/stream", json={"message": USER_MESSAGE}
    ) as streamed:
        assert streamed.status_code == 200
        raw = "".join(streamed.iter_text())

    events = sse_events(raw)
    assert [name for name, _ in events] == ["metadata", "error", "done"]
    assert events[1] == ("error", {"message": GENERIC})
    assert "sk-internal" not in raw and "quota" not in raw and "RuntimeError" not in raw
    assert_logged(caplog, world, "stream")


def test_invoke_agent_error_is_logged_and_generic(
    world: World, failing_client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    response = failing_client.post("/api/agents/assistant/invoke", json={"message": USER_MESSAGE})

    assert response.status_code == 500
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {"code": "agent_error", "message": GENERIC}
    assert "sk-internal" not in response.text and "quota" not in response.text
    assert_logged(caplog, world, "invoke")
