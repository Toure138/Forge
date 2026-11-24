"""Who may use the built-in agents (FR-31, AC-39; AC-29 for the agent routes): a valid session,
an active organization and `agents:use`. Refusals are plain JSON errors, sent before any SSE
stream is opened, and the fake model is never called (it has no scripted reply)."""

from collections.abc import Callable

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.factories import World

PERMISSION_DENIED = {"code": "permission_denied", "message": "Permission denied."}
NOT_AUTHENTICATED = {"code": "not_authenticated", "message": "Authentication required."}
AGENT_NOT_FOUND = {"code": "agent_not_found", "message": "Agent not found."}

AgentCall = Callable[[TestClient], httpx.Response]
AGENT_CALLS: dict[str, AgentCall] = {
    "list": lambda c: c.get("/api/agents"),
    "invoke": lambda c: c.post("/api/agents/assistant/invoke", json={"message": "Salut"}),
    "stream": lambda c: c.post("/api/agents/assistant/stream", json={"message": "Salut"}),
}


def assert_json_error(response: httpx.Response, status: int, body: dict[str, str]) -> None:
    """A JSON error, not an `event: error` inside an opened stream."""
    assert response.status_code == status
    assert response.headers["content-type"] == "application/json"
    assert "event:" not in response.text
    assert response.json() == body


@pytest.mark.parametrize("call", list(AGENT_CALLS))
def test_guest_cannot_list_or_invoke_agents(world: World, call: str) -> None:
    """AC-39 (FR-31): Dave, Guest of Acme, is refused; the refusal is in Acme's audit log."""
    dave = world.client_as(world.dave, world.acme)

    response = AGENT_CALLS[call](dave)

    assert_json_error(response, 403, PERMISSION_DENIED)
    last = world.audit(world.acme)[-1]
    assert (last["actor_user_id"], last["action"], last["outcome"]) == (
        world.dave.id,
        "permission.denied",
        "denied",
    )
    assert last["details"] == {"permission": "agents:use"}
    assert world.query(world.acme, "SELECT * FROM conversations") == []


@pytest.mark.parametrize("call", list(AGENT_CALLS))
def test_anonymous_cannot_use_agents(client: TestClient, call: str) -> None:
    """FR-25, FR-31: without a session, the agent routes answer 401 (no model call)."""
    assert_json_error(AGENT_CALLS[call](client), 401, NOT_AUTHENTICATED)


@pytest.mark.parametrize("who", ["alice", "bob", "carole"], ids=["owner", "admin", "member"])
def test_owner_admin_and_member_can_list_agents(world: World, who: str) -> None:
    response = world.client_as(world.people[who], world.acme).get("/api/agents")

    assert response.status_code == 200
    assert "assistant" in [agent["name"] for agent in response.json()]


@pytest.mark.parametrize("call", ["invoke", "stream"])
def test_unknown_agent_is_agent_not_found(world: World, call: str) -> None:
    """Spec 000 AC-6, with the error code of the plan's contract (JSON, before any stream)."""
    carole = world.client_as(world.carole, world.acme)

    response = carole.post(f"/api/agents/nope/{call}", json={"message": "hi"})

    assert_json_error(response, 404, AGENT_NOT_FOUND)
