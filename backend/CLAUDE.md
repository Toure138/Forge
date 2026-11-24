# Backend : conventions

Python 3.12 · FastAPI · LangGraph · deepagents · langchain-anthropic · uv. Toutes les commandes se lancent depuis `backend/`.

## Structure
```
app/
├── main.py              create_app(model, *, settings, http_transport, clock) : factory, lifespan (moteur), routers /api
├── core/config.py       Settings (pydantic-settings, lit ../.env)
├── core/llm.py          get_chat_model() : SEUL endroit où un modèle est construit
├── agents/registry.py   AGENTS {name: AgentSpec} + AgentRegistry (cache + checkpointer)
├── agents/studio.py     graphes exposés à LangGraph Studio
├── agents/<nom>/        graph.py (NAME, DESCRIPTION, build) · prompts.py · tools.py
├── api/routes/          health.py, agents.py (invoke + stream SSE)
└── schemas/             modèles Pydantic = contrat API
tests/
├── conftest.py          FakeToolModel, fake_model(), fixtures make_client(*replies) (anonyme), world, member_client(*replies)
├── test_*.py            modèle factice, aucun réseau
└── live/                @pytest.mark.live : vrai LLM, RUN_LIVE=1
```

## Tester un agent sans LLM
```python
from langchain_core.messages import AIMessage

def test_agent_calls_tool(member_client):
    call = AIMessage(content="", tool_calls=[{"name": "my_tool", "args": {"x": 1}, "id": "c1"}])
    client = member_client(call, "Réponse finale")  # réponses successives du modèle
    res = client.post("/api/agents/<nom>/invoke", json={"message": "..."})
    assert res.json()["output"] == "Réponse finale"
```
Le faux modèle renvoie les messages scriptés dans l'ordre. Prévois une réponse par tour de modèle, sous-agents compris.
Les routes d'agents exigent `agents:use` dans l'organisation active (spec 001) : `member_client(*replies)` est Carole, Membre d'Acme, connectée (fixture `world`) ; pour une autre personne, `world.client_as(person, org, replies=[...])`. `make_client(*replies)` reste anonyme (santé seulement).

## Windows, base de données et tests (spec 001)
- psycopg async refuse la boucle Proactor de Windows. Lancer l'API avec `uv run uvicorn app.main:app --reload --port 8000 --loop app.core.aio:selector_loop_factory` (c'est ce que fait `scripts/dev.ps1`). Sans `--loop`, l'application refuse de démarrer avec un message explicite.
- `pytest` exige la base Docker (`docker compose up -d db` depuis la racine) : les tests de base échouent, ils ne sont jamais sautés.
- Tests async : le hook `pytest_asyncio_loop_factories` de `tests/conftest.py` (défini sous `win32` seulement) les fait tourner sur une boucle selector. Ne pas surcharger `event_loop_policy` (déprécié).
- Client HTTP de test : `client_for(app)` de `tests/conftest.py` (https, boucle selector) ; jamais `TestClient(app)` nu pour une app qui touche la base. Réglages de test : `settings_for_tests(**overrides)`.
- Une fixture autouse bloque toute connexion réseau hors de la boucle locale (`NetworkBlockedError`), sauf pour les tests `live`.
- Tests d'API multi-utilisateurs : fixture `world` (`tests/factories.py`, Acme : Alice Propriétaire, Bob Admin, Carole Membre, Dave Invité ; Globex : Gina Propriétaire). `world.client_as(world.bob, world.acme)` = navigateur de Bob dans Acme (une application par appel). `world.audit(org)`, `world.role_of(org, person)`, `world.execute(org, sql, ...)` lisent et écrivent en `forge_app` sous contexte. Après un `clock.advance` au-delà des durées de session : `world.renew_sessions()` puis de nouveaux `client_as`.

## Règles
- `uv add` / `uv add --dev` uniquement. Le fichier `uv.lock` est versionné.
- Style : `ruff format` (100 colonnes) et `ruff check`, tous deux verts. `mypy` vert sur `app/`.
- Async de bout en bout dans les routes (`ainvoke` et `astream`, jamais `invoke` dans un handler).
- Les API de deepagents, langgraph et langchain évoluent vite. Vérifie une signature avec `uv run python -c "import inspect, deepagents; print(inspect.signature(deepagents.create_deep_agent))"`.
- Le checkpointer est `InMemorySaver`, partagé par l'`AgentRegistry`. Les graphes de `studio.py` n'en ont pas : le serveur LangGraph gère la persistance.
- Le traçage LangSmith s'active par `.env` (`LANGSMITH_TRACING=true`, `LANGSMITH_API_KEY`), sans modification de code.
