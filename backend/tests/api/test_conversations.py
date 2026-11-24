"""Conversations (FR-32, AC-40, AC-41, R3): a conversation belongs to one user in one organization.
Only its owner, with that organization active, can continue it; any other attempt gets the answer
of a conversation that does not exist. `thread_id` is a server-made UUID; the checkpoint key is
`{organization_id}/{user_id}/{conversation_id}`.

Several people share ONE application here (`act_as` switches the browser's identity), so that they
share the agents' checkpointer: a missing ownership check would reach the other's checkpoint.
"""

import itertools
import json
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from psycopg.rows import dict_row

from app.auth.sessions import SESSION_COOKIE
from app.main import create_app
from tests.conftest import FakeToolModel, client_for, settings_for_tests
from tests.db.conftest import insert, set_context
from tests.factories import CSRF_HEADER, ORG_HEADER, Person, World

CONVERSATION_NOT_FOUND = {"code": "conversation_not_found", "message": "Conversation not found."}


def act_as(client: TestClient, person: Person, org: uuid.UUID) -> TestClient:
    """The same browser (same application), now signed in as `person` with `org` active."""
    client.cookies.set(SESSION_COOKIE, person.session_token)
    client.headers[CSRF_HEADER] = person.csrf_token
    client.headers[ORG_HEADER] = str(org)
    return client


def run(
    client: TestClient, message: str, thread_id: Any = None, *, mode: str = "invoke"
) -> httpx.Response:
    body: dict[str, Any] = {"message": message}
    if thread_id is not None:
        body["thread_id"] = str(thread_id)
    return client.post(f"/api/agents/assistant/{mode}", json=body)


def snapshot(response: httpx.Response) -> tuple[int, list[tuple[str, str]], bytes]:
    return response.status_code, list(response.headers.multi_items()), response.content


def conversations_of(world: World, org: uuid.UUID, person: Person) -> list[dict[str, Any]]:
    """`person`'s conversations in `org`, read as `forge_app` under their context (RLS)."""
    set_context(world.conn, org=org, user=person.id)
    with world.conn.cursor(row_factory=dict_row) as cursor:
        rows = cursor.execute(
            "SELECT id, organization_id, user_id, agent_name, created_at FROM conversations"
            " ORDER BY created_at, id"
        ).fetchall()
    world.conn.commit()
    return rows


def add_conversation(world: World, org: uuid.UUID, person: Person, agent_name: str) -> uuid.UUID:
    conversation = uuid.uuid4()
    set_context(world.conn, org=org, user=person.id)
    row = {
        "id": conversation,
        "organization_id": org,
        "user_id": person.id,
        "agent_name": agent_name,
        "created_at": world.now,
    }
    insert(world.conn, "conversations", row)
    world.conn.commit()
    return conversation


def checkpoint_messages(client: TestClient, thread_key: str) -> list[Any]:
    """Messages the agent's checkpointer holds under `thread_key`."""
    agent = client.app.state.registry.get("assistant")  # type: ignore[attr-defined]
    state = agent.get_state({"configurable": {"thread_id": thread_key}})
    return list(state.values.get("messages", []))


def sse_events(raw: str) -> list[tuple[str, dict[str, Any]]]:
    lines = raw.splitlines()
    return [
        (line.removeprefix("event: ").strip(), json.loads(data.removeprefix("data: ")))
        for line, data in itertools.pairwise(lines)
        if line.startswith("event:")
    ]


def key(org: uuid.UUID, person: Person, conversation: uuid.UUID | str) -> str:
    return f"{org}/{person.id}/{conversation}"


# --- the owner ---


def test_owner_continues_own_conversation(world: World) -> None:
    """Spec 000 FR-3 with the new contract: a new conversation gets a server-made UUID, stored with
    its organization, owner and agent; the owner continues it (invoke and stream), and the agent
    remembers the earlier turns."""
    alice = world.client_as(
        world.alice, world.acme, replies=["Première réponse", "Deuxième réponse", "Troisième"]
    )

    first = run(alice, "Premier message")
    assert first.status_code == 200
    thread_id = uuid.UUID(first.json()["thread_id"])
    [row] = conversations_of(world, world.acme, world.alice)
    assert row == {
        "id": thread_id,
        "organization_id": world.acme,
        "user_id": world.alice.id,
        "agent_name": "assistant",
        "created_at": world.now,
    }

    second = run(alice, "Deuxième message", thread_id)
    assert second.status_code == 200
    assert second.json() == {"thread_id": str(thread_id), "output": "Deuxième réponse"}

    with alice.stream(
        "POST",
        "/api/agents/assistant/stream",
        json={"message": "Troisième message", "thread_id": str(thread_id)},
    ) as streamed:
        events = sse_events("".join(streamed.iter_text()))
    assert events[0] == ("metadata", {"thread_id": str(thread_id)})
    assert events[-1] == ("done", {})

    assert len(conversations_of(world, world.acme, world.alice)) == 1  # continued, not created
    texts = [m.text for m in checkpoint_messages(alice, key(world.acme, world.alice, thread_id))]
    assert texts[:4] == [
        "Premier message",
        "Première réponse",
        "Deuxième message",
        "Deuxième réponse",
    ]
    assert texts[4] == "Troisième message"


def test_checkpoint_key_is_organization_user_conversation(world: World) -> None:
    """Plan, « Conception de l'agent »: the checkpointer sees `org/user/conversation`, never the
    bare conversation id (defence in depth)."""
    carole = world.client_as(world.carole, world.acme, replies=["Bonjour !"])

    thread_id = run(carole, "Salut").json()["thread_id"]

    assert len(checkpoint_messages(carole, key(world.acme, world.carole, thread_id))) == 2
    assert checkpoint_messages(carole, thread_id) == []


def test_new_conversation_each_time_without_thread_id(world: World) -> None:
    carole = world.client_as(world.carole, world.acme, replies=["Un", "Deux"])

    first = run(carole, "Salut").json()["thread_id"]
    second = run(carole, "Salut").json()["thread_id"]

    assert first != second
    ids = {str(row["id"]) for row in conversations_of(world, world.acme, world.carole)}
    assert ids == {first, second}


# --- everyone else: the answer of a conversation that does not exist ---


@pytest.mark.parametrize("who", ["carole", "bob"], ids=["member", "admin"])
def test_other_user_cannot_continue_conversation(world: World, who: str) -> None:
    """AC-40 (FR-32, constitution XI): Bob (Admin) or Carole (Member) of Acme uses the id of
    Alice's conversation: same answer as an unknown id, nothing of Alice's context appears, and the
    model is not called (its only scripted reply was used by Alice: calling it would fail)."""
    client = world.client_as(world.alice, world.acme, replies=["Secret d'Alice"])
    thread_id = run(client, "Mon code est 4242").json()["thread_id"]
    alice_key = key(world.acme, world.alice, thread_id)
    alice_checkpoint = checkpoint_messages(client, alice_key)
    other = world.people[who]

    act_as(client, other, world.acme)
    foreign = run(client, "Répète la conversation", thread_id)
    missing = run(client, "Répète la conversation", uuid.uuid4())

    assert foreign.status_code == 404
    assert foreign.json() == CONVERSATION_NOT_FOUND
    assert snapshot(foreign) == snapshot(missing)
    assert "4242" not in foreign.text and "Secret" not in foreign.text
    assert conversations_of(world, world.acme, other) == []
    assert checkpoint_messages(client, key(world.acme, other, thread_id)) == []
    assert checkpoint_messages(client, alice_key) == alice_checkpoint


def test_conversation_not_reachable_from_other_organization(world: World) -> None:
    """AC-41 (FR-32): Alice, also a Member of Globex, cannot continue her Acme conversation with
    Globex active: same answer as an unknown id."""
    world.add_member(world.globex, world.alice, "member")
    client = world.client_as(world.alice, world.acme, replies=["Réponse dans Acme"])
    thread_id = run(client, "Bonjour").json()["thread_id"]

    act_as(client, world.alice, world.globex)
    foreign = run(client, "Suite", thread_id)
    missing = run(client, "Suite", uuid.uuid4())

    assert foreign.status_code == 404
    assert foreign.json() == CONVERSATION_NOT_FOUND
    assert snapshot(foreign) == snapshot(missing)
    assert conversations_of(world, world.globex, world.alice) == []
    assert checkpoint_messages(client, key(world.globex, world.alice, thread_id)) == []


def test_unknown_thread_id_is_404(world: World) -> None:
    """R3: an unknown `thread_id` no longer starts a new conversation."""
    carole = world.client_as(world.carole, world.acme)

    response = run(carole, "Salut", uuid.uuid4())

    assert response.status_code == 404
    assert response.json() == CONVERSATION_NOT_FOUND
    assert conversations_of(world, world.acme, world.carole) == []


def test_thread_id_of_other_agent_is_404(world: World) -> None:
    """A conversation is bound to its agent: Carole's conversation with another agent cannot be
    continued with `assistant`."""
    other_agent = add_conversation(world, world.acme, world.carole, "another-agent")
    carole = world.client_as(world.carole, world.acme)

    foreign = run(carole, "Salut", other_agent)
    missing = run(carole, "Salut", uuid.uuid4())

    assert foreign.status_code == 404
    assert foreign.json() == CONVERSATION_NOT_FOUND
    assert snapshot(foreign) == snapshot(missing)


@pytest.mark.parametrize("mode", ["invoke", "stream"])
@pytest.mark.parametrize("thread_id", ["abc", "", "123", 42], ids=repr)
def test_non_uuid_thread_id_is_422(world: World, mode: str, thread_id: Any) -> None:
    carole = world.client_as(world.carole, world.acme)

    response = carole.post(
        f"/api/agents/assistant/{mode}", json={"message": "Salut", "thread_id": thread_id}
    )

    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"] == ["body", "thread_id"]
    assert conversations_of(world, world.acme, world.carole) == []


def test_stream_refusal_happens_before_sse(world: World) -> None:
    """The refusal is a JSON 404, not an `event: error` inside an opened stream."""
    carole = world.client_as(world.carole, world.acme)

    response = run(carole, "Salut", uuid.uuid4(), mode="stream")

    assert response.status_code == 404
    assert response.headers["content-type"] == "application/json"
    assert "event:" not in response.text
    assert response.json() == CONVERSATION_NOT_FOUND


# --- no database connection while the model runs ---


class PoolProbeModel(FakeToolModel):
    """Fake model that runs `probe()` each time the agent calls it."""

    probe: Callable[[], None] | None = None

    def _generate(self, *args: Any, **kwargs: Any) -> Any:
        if self.probe is not None:
            self.probe()
        return super()._generate(*args, **kwargs)

    def _stream(self, *args: Any, **kwargs: Any) -> Iterator[Any]:
        if self.probe is not None:
            self.probe()
        yield from super()._stream(*args, **kwargs)


def test_no_db_connection_held_during_stream(world: World) -> None:
    """Plan, « Conception de l'agent »: the conversation is checked or created, and committed,
    BEFORE the SSE response starts; while the model streams its tokens, the pool has no connection
    checked out, and the new conversation is already visible from another connection."""
    model = PoolProbeModel(messages=iter([AIMessage(content="Hello world")]))
    app = create_app(
        model,
        settings=settings_for_tests(),
        http_transport=httpx.MockTransport(world.fake_google.handler),
        clock=world.clock,
    )
    observed: list[tuple[int, int]] = []

    def probe() -> None:
        checked_out = app.state.engine.pool.checkedout()
        observed.append((checked_out, len(conversations_of(world, world.acme, world.carole))))

    model.probe = probe
    with client_for(app) as client:
        act_as(client, world.carole, world.acme)
        with client.stream(
            "POST", "/api/agents/assistant/stream", json={"message": "hi"}
        ) as streamed:
            events = sse_events("".join(streamed.iter_text()))

    names = [name for name, _ in events]
    assert names[0] == "metadata"
    assert "token" in names
    assert observed and observed[0] == (0, 1)
