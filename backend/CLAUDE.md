# Backend : conventions

Python 3.12 · FastAPI · LangGraph · deepagents · langchain-anthropic · uv. Toutes les commandes se lancent depuis `backend/`.

## Structure
```
app/
├── main.py              create_app(model=None) : app factory, lifespan, CORS, routers /api
├── core/config.py       Settings (pydantic-settings, lit ../.env)
├── core/llm.py          get_chat_model() : SEUL endroit où un modèle est construit
├── agents/registry.py   AGENTS {name: AgentSpec} + AgentRegistry (cache + checkpointer)
├── agents/studio.py     graphes exposés à LangGraph Studio
├── agents/<nom>/        graph.py (NAME, DESCRIPTION, build) · prompts.py · tools.py
├── api/routes/          health.py, agents.py (invoke + stream SSE)
└── schemas/             modèles Pydantic = contrat API
tests/
├── conftest.py          FakeToolModel, fake_model(), fixture make_client(*replies)
├── test_*.py            modèle factice, aucun réseau
└── live/                @pytest.mark.live : vrai LLM, RUN_LIVE=1
```

## Tester un agent sans LLM
```python
from langchain_core.messages import AIMessage

def test_agent_calls_tool(make_client):
    call = AIMessage(content="", tool_calls=[{"name": "my_tool", "args": {"x": 1}, "id": "c1"}])
    client = make_client(call, "Réponse finale")  # réponses successives du modèle
    res = client.post("/api/agents/<nom>/invoke", json={"message": "..."})
    assert res.json()["output"] == "Réponse finale"
```
Le faux modèle renvoie les messages scriptés dans l'ordre. Prévois une réponse par tour de modèle, sous-agents compris.

## Règles
- `uv add` / `uv add --dev` uniquement. Le fichier `uv.lock` est versionné.
- Style : `ruff format` (100 colonnes) et `ruff check`, tous deux verts. `mypy` vert sur `app/`.
- Async de bout en bout dans les routes (`ainvoke` et `astream`, jamais `invoke` dans un handler).
- Les API de deepagents, langgraph et langchain évoluent vite. Vérifie une signature avec `uv run python -c "import inspect, deepagents; print(inspect.signature(deepagents.create_deep_agent))"`.
- Le checkpointer est `InMemorySaver`, partagé par l'`AgentRegistry`. Les graphes de `studio.py` n'en ont pas : le serveur LangGraph gère la persistance.
- Le traçage LangSmith s'active par `.env` (`LANGSMITH_TRACING=true`, `LANGSMITH_API_KEY`), sans modification de code.
