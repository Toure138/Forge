import json

from langchain_core.messages import AIMessage


def test_health(make_client):
    client = make_client()
    assert client.get("/api/health").json() == {"status": "ok"}


def test_list_agents(make_client):
    client = make_client()
    names = [a["name"] for a in client.get("/api/agents").json()]
    assert "assistant" in names


def test_unknown_agent_returns_404(make_client):
    client = make_client()
    res = client.post("/api/agents/nope/invoke", json={"message": "hi"})
    assert res.status_code == 404


def test_invoke_returns_answer_and_thread(make_client):
    client = make_client("Bonjour !")
    res = client.post("/api/agents/assistant/invoke", json={"message": "Salut"})
    assert res.status_code == 200
    body = res.json()
    assert body["output"] == "Bonjour !"
    assert body["thread_id"]


def test_invoke_runs_custom_tool(make_client):
    tool_call = AIMessage(
        content="",
        tool_calls=[{"name": "get_current_time", "args": {}, "id": "call_1"}],
    )
    client = make_client(tool_call, "Il est l'heure.")
    res = client.post("/api/agents/assistant/invoke", json={"message": "Quelle heure ?"})
    assert res.json()["output"] == "Il est l'heure."


def test_stream_emits_sse_events(make_client):
    client = make_client("Hello world")
    with client.stream("POST", "/api/agents/assistant/stream", json={"message": "hi"}) as res:
        raw = "".join(res.iter_text())
    events = [
        line.removeprefix("event: ").strip()
        for line in raw.splitlines()
        if line.startswith("event:")
    ]
    assert events[0] == "metadata"
    assert "token" in events
    assert events[-1] == "done"
    tokens = [
        json.loads(line.removeprefix("data: "))["text"]
        for prev, line in zip(raw.splitlines(), raw.splitlines()[1:], strict=False)
        if prev.strip() == "event: token"
    ]
    assert "".join(tokens).replace(" ", "") == "Helloworld"
