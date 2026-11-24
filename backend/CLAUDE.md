# Backend : conventions

Python 3.12 · FastAPI · LangGraph · deepagents · langchain-anthropic · uv. Toutes les commandes se lancent depuis `backend/`.

## Structure
```
app/
├── main.py                create_app(model, *, settings, http_transport, clock) : factory, lifespan (moteur, client httpx, fournisseur Google), routers /api
├── core/config.py         Settings (pydantic-settings, lit ../.env) ; refuse COOKIE_SECURE=false en prod
├── core/llm.py            get_chat_model() : SEUL endroit où un modèle est construit
├── core/authz.py          rôles → permissions (SEUL endroit où les rôles sont nommés), require(perm), require_authenticated(perm), PUBLIC_ROUTES
├── core/clock.py          Clock (Protocol) + SystemClock : toute décision temporelle passe par l'horloge injectée
├── core/errors.py         ApiError(status, code, message) → {code, message}
├── core/security_log.py   security_event() (journal plateforme `forge.security`, champs fermés) + RedactQueryFilter
├── core/aio.py            selector_loop_factory (psycopg async sous Windows)
├── auth/                  provider.py (IdentityProvider), google.py (OIDC : code + PKCE + state + nonce),
│                          sessions.py (login_requests, sessions hachées), users.py (upsert par google_sub),
│                          dependencies.py (CurrentSessionDep : cookie → session → CSRF → set_user)
├── db/                    engine.py, tables.py (Table Core), context.py (RequestDb : une transaction par requête,
│                          set_user / set_organization, record_denial, commit_and_raise,
│                          release : valider et rendre la connexion avant une longue attente ; alias RequestDbDep)
├── services/              organizations, memberships (invariant « au moins un Propriétaire », FOR UPDATE),
│                          invitations, audit, conversations : logique métier, testable sans HTTP
├── agents/registry.py     AGENTS {name: AgentSpec} + AgentRegistry (cache + checkpointer)
├── agents/studio.py       graphes exposés à LangGraph Studio
├── agents/<nom>/          graph.py (NAME, DESCRIPTION, build) · prompts.py · tools.py
├── api/routes/            health, auth, organizations, members, invitations, audit, agents (invoke + stream SSE)
└── schemas/               modèles Pydantic = contrat API (miroir : frontend/src/lib/api.ts)
migrations/                Alembic (env.py synchrone, rôle forge_owner) : 0001 tables, 0002 RLS + droits + SECURITY DEFINER
tests/
├── conftest.py            fixtures pg_owner / pg_app, migrated_db, clean_db, clock, fake_google, world, member_client(*replies),
│                          make_client(*replies) (anonyme) ; garde réseau autouse
├── factories.py           monde de test Acme / Globex (voir plus bas)
├── fakes/                 clock.py (FakeClock), google.py (faux Google : clé RSA, JWKS, endpoint de jeton, panne)
├── route_requests.py      inventaire des routes (iter_route_contexts) et corps minimaux : balayages 401 / CSRF / permission
├── db/                    isolation (fuite entre organisations), rôles, migrations, fonctions SECURITY DEFINER
├── auth/ · api/ · services/
├── infra/                 @pytest.mark.infra : Docker réel (projet compose forge-smoke), RUN_INFRA=1
└── live/                  @pytest.mark.live : vrai LLM, RUN_LIVE=1
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

## Base de données et sécurité (spec 001, ADR-0003)
- **Trois rôles Postgres.** `forge_owner` fait les migrations ; `forge_app` (l'API) ne possède rien et n'a pas `BYPASSRLS` ; `forge_self_reader` (NOLOGIN) possède les seules fonctions `SECURITY DEFINER`. Ils sont créés par `infra/postgres/initdb/`, pas par Alembic.
- **Une transaction par requête** (`RequestDbDep`, toujours le même alias). Le contexte se pose avec `set_config(…, true)` (`set_user`, `set_organization`), donc il meurt avec la transaction. Jamais de `SET` de session : le pool réutiliserait la connexion avec le contexte d'un autre utilisateur.
- **Toute table d'organisation**, dans la **même** migration : colonne `organization_id`, `ENABLE` + `FORCE ROW LEVEL SECURITY`, politique `TO forge_app` (USING + WITH CHECK), droits minimaux. Le test `test_every_table_is_global_or_org_protected` échoue sinon. Les tables globales sont en liste blanche (`users`, `sessions`, `login_requests`, `alembic_version`).
- **Filtre explicite en plus du RLS** : chaque requête d'organisation filtre aussi `organization_id` dans le code (constitution X). Une ressource d'une autre organisation répond comme une ressource inexistante (404 identique).
- **`SECURITY DEFINER`** : liste blanche fermée (`app_my_memberships`, `app_my_pending_invitations`, `app_my_invitation`), propriétaire `forge_self_reader`, `search_path` figé, `EXECUTE` révoqué de `PUBLIC` **avant** le changement de propriétaire (sinon le `REVOKE` est ignoré en silence). Un test d'introspection le vérifie.
- **Requêtes paramétrées uniquement.** Le RLS ne protège pas d'un SQL injecté (qui pourrait appeler `set_config`). `hide_parameters=True` sur le moteur : les paramètres SQL (state, CSRF…) n'apparaissent jamais dans les journaux.
- **Permissions, jamais des rôles** : une route déclare `require("perm")` (organisation active) ou `require_authenticated("perm")`. Aucun nom de rôle hors de `core/authz.py` (copies vérifiées par test dans `schemas/common.py` et `db/tables.py`, invariant « dernier Propriétaire » dans `services/memberships.py`). Une route sans permission ni exemption fait échouer `tests/api/test_route_protection.py`.
- **Refus journalisés** : `db.record_denial(...)` annule la transaction métier, écrit l'audit dans une nouvelle transaction, puis lève l'`ApiError`. Une tentative sur une organisation dont on n'est pas membre va au journal plateforme (`organization_access_denied`), pas à celui de l'organisation.
- **Horloge injectée** : aucune décision ne lit `now()` SQL ni `datetime.now()`. Les services reçoivent `now`. PyJWT vérifie `exp` avec l'heure système : `google.py` désactive ce contrôle et le refait avec l'horloge, avec une marge `OIDC_CLOCK_SKEW_SECONDS` (60 s par défaut, 0 à 300).
- **Secrets** : comparaisons en `hmac.compare_digest` ; aucun jeton, cookie, code, state, `code_verifier` ni CSRF dans les journaux (`security_event` a une liste de champs fermée).

### Migrations
```powershell
uv run alembic upgrade head                 # base forge (DATABASE_OWNER_URL)
uv run alembic revision -m "<slug>"         # nouvelle migration : écrire upgrade ET downgrade
uv run alembic downgrade -1
..\scripts\db-reset.ps1                     # détruit le volume local, recrée et migre la base (taper « forge » pour confirmer ; -Force)
..\scripts\dev.ps1 -DbOnly                  # base + migrations seulement
```
Les tests migrent eux-mêmes la base `forge_test` (fixture `migrated_db`).

### Tests d'infrastructure
`$env:RUN_INFRA=1; uv run pytest -m infra` : lance un projet compose séparé (`forge-smoke`, port 5434, volume neuf), vérifie le démarrage, les migrations, la persistance et la réinitialisation, puis le supprime. La base `forge` du développeur n'est pas touchée. `check.ps1` ne les lance pas (lents).

## Règles
- `uv add` / `uv add --dev` uniquement. Le fichier `uv.lock` est versionné.
- Style : `ruff format` (100 colonnes) et `ruff check`, tous deux verts. `mypy` vert sur `app/`.
- Async de bout en bout dans les routes (`ainvoke` et `astream`, jamais `invoke` dans un handler).
- Aucune connexion ni verrou pendant l'appel au modèle : `stream` finit son travail en base avant de renvoyer la réponse, `invoke` appelle `db.release()` avant `ainvoke`.
- Erreurs d'agent : `logger.exception` sur `forge.agents` (agent, mode, organisation, utilisateur ; jamais le message de l'utilisateur), message générique au client (SSE `error`, ou 500 `agent_error` pour `invoke`). Jamais `str(exc)` vers le client.
- Les API de deepagents, langgraph et langchain évoluent vite. Vérifie une signature avec `uv run python -c "import inspect, deepagents; print(inspect.signature(deepagents.create_deep_agent))"`.
- Le checkpointer est `InMemorySaver`, partagé par l'`AgentRegistry` (perdu au redémarrage ; persistance prévue en spec 003). La clé de checkpoint est `f"{organization_id}/{user_id}/{conversation_id}"`, et la table `conversations` (RLS organisation + propriétaire) décide qui peut reprendre une conversation. Les graphes de `studio.py` n'en ont pas : le serveur LangGraph gère la persistance.
- Le traçage LangSmith s'active par `.env` (`LANGSMITH_TRACING=true`, `LANGSMITH_API_KEY`), sans modification de code.
