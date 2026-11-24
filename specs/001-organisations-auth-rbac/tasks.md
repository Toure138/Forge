# Tâches : spec 001 — Organisations, connexion Google, rôles d'organisation et isolation des données

Légende :

- Couche : `infra` | `backend` | `agent` | `frontend` | `docs`. Aucune tâche `agent` : aucun agent n'est créé ni modifié (plan, « Conception de l'agent »).
- `[P ∥ Txxx, …]` : parallélisable **avec les tâches citées uniquement**. Aucune dépendance mutuelle et aucun fichier en commun, vérifié dans la section « Vérification des [P] ». Une tâche sans `[P]` se fait seule, dans l'ordre.
- Chaque tâche suit le TDD (constitution II) : **1.** écrire les tests listés sous « Tests d'abord » ; **2.** les lancer et les voir échouer pour la bonne raison ; **3.** écrire le code ; **4.** tests verts, puis `ruff format`, `ruff check`, `mypy` (ou `pnpm lint`, `pnpm typecheck`, `pnpm test`) ; **5.** cocher. Quand un test est une garde (introspection, analyse de code), on le voit d'abord échouer sur un cas négatif synthétique.
- Sous-agent : `backend-engineer`, `frontend-engineer`, ou « orchestrateur » pour les tâches `docs` (cf. `/spec-implement`).
- Tous les chemins backend sont relatifs à `backend/`, tous les chemins frontend à `frontend/`, sauf mention contraire.
- Références : [spec.md](spec.md), [plan.md](plan.md), [ADR-0003](../../docs/adr/0003-persistance-et-contexte-de-securite.md).

Écarts au découpage indicatif du plan (justifiés dans « Remarques sur le plan ») : `scripts/dev.ps1` passe de la phase 7 à la phase 1 (T006) ; le contrôle des routes (AC-29) démarre en phase 2 avec une liste « pas encore protégées » qui se vide en phase 5 ; AC-18 (volet API), AC-27 et AC-31 passent en phase 5, car ils exigent que les routes d'agents soient protégées.

---

## Phase 1 : Infrastructure base de données

Objectif : Postgres 18 dans Docker, rôles, Alembic, tables, RLS forcé, fonctions `SECURITY DEFINER`, tests d'isolation et de rôles contre la vraie base. `check.ps1` démarre la base avant pytest.

- [ ] **T001** `infra` — **Pointe technique** : psycopg async sous Windows et image Postgres 18
  - Sous-agent : `backend-engineer` · Dépend de : — · Couvre : risques « Windows / Proactor » et « image Postgres 18 » du plan (aucun AC)
  - Tests d'abord : `tests/test_aio.py::test_selector_loop_factory_returns_selector_event_loop` (appel sans argument, renvoie une `asyncio.SelectorEventLoop`).
  - Code : `uv add "psycopg[binary]" "sqlalchemy[asyncio]"` ; `app/core/aio.py::selector_loop_factory` (callable **sans argument** : uvicorn 0.54 appelle directement l'objet importé par `--loop module:callable`, vérifié dans `uvicorn/config.py::get_loop_factory`).
  - Vérifications (scripts jetables dans le répertoire scratchpad de la session, **non versionnés**) :
    1. `docker pull pgvector/pgvector:0.8.7-pg18-trixie` réussit ; `docker image inspect` montre `PGDATA` et le volume déclaré, qui doit être sous **`/var/lib/postgresql`** ; `psql --version` dans l'image ≥ 15 (`\getenv`).
    2. Conteneur jetable (`docker run --rm -d -p 127.0.0.1:5439:5432 …`) : une `psycopg.AsyncConnection` sous la boucle par défaut de Windows lève l'`InterfaceError` attendue ; avec `asyncio.run(main(), loop_factory=selector_loop_factory)`, `SELECT 1` réussit, en psycopg direct **et** via `create_async_engine("postgresql+psycopg://…")` (greenlet installé).
    3. `uv run uvicorn <app jetable> --reload --loop app.core.aio:selector_loop_factory` : un endpoint qui fait une requête psycopg async répond 200 (le `--reload` lance un sous-processus : la fabrique doit y être réimportée sans erreur).
    4. `TestClient(app, backend_options={"loop_factory": asyncio.SelectorEventLoop})` (signature vérifiée dans Starlette 1.10) exécute le même endpoint.
    5. Un test `pytest-asyncio` (1.5) tourne sur une boucle selector sous `win32` (fixture `event_loop_policy` ou mécanisme équivalent de la version installée) et exécute une requête psycopg async.
  - Fichiers : `pyproject.toml`, `uv.lock`, `app/core/aio.py`, `tests/test_aio.py`, `specs/001-organisations-auth-rbac/tasks.md` (section « Journal » : versions, chemin du volume, mécanisme retenu pour le point 5).
  - Fin : les cinq vérifications passent et sont consignées dans le Journal ; conteneur jetable supprimé. **Si une vérification échoue : s'arrêter, annuler l'ajout des dépendances, et proposer une mise à jour du plan et de l'ADR-0003** (autre tag d'image, autre chemin de volume, ou repli sur `asyncpg` pour l'API). Ne pas commencer T002.

- [ ] **T002** `infra` — `compose.yaml`, script d'initialisation des rôles et des bases, étape `infra: database` de `check.ps1`
  - Sous-agent : `backend-engineer` · Dépend de : T001 · Couvre : AC-45 (attributs des rôles), AC-46 (aucun rôle `BYPASSRLS`), AC-58 (base démarrée par `check.ps1`)
  - Tests d'abord : `tests/db/test_db_roles.py::test_no_role_bypasses_rls` (aucun rôle autre que le superutilisateur de l'image n'a `rolbypassrls`) ; `::test_roles_and_databases_exist` (`forge_owner` LOGIN NOSUPERUSER ; `forge_app` LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE ; `forge_self_reader` NOLOGIN ; bases `forge` et `forge_test` possédées par `forge_owner`). Rouges d'abord : la base est injoignable, et la fixture doit échouer avec le message « lancez `docker compose up -d db` » (jamais de `skip`).
  - Code : `compose.yaml` (service `db`, image vérifiée en T001, port `127.0.0.1:${POSTGRES_PORT:-5433}:5432`, volume nommé `forge_pgdata` sur le chemin vérifié, `./infra/postgres/initdb` en lecture seule, `healthcheck: pg_isready`) ; `infra/postgres/initdb/10-roles-and-databases.sql` (mots de passe via `\getenv`, `GRANT forge_self_reader TO forge_owner WITH INHERIT FALSE, SET TRUE`) ; `.gitattributes` (`* text=auto`, `*.sh` et `*.sql` en `eol=lf`) ; `.env.example` (bloc « Database » du plan) ; `scripts/check.ps1` (étape `infra: database` = `docker compose up -d --wait db`, **avant** `backend: pytest`) ; fixtures `pg_owner` et `pg_app` (connexions psycopg **synchrones** à `forge_test`, URL lues dans `TEST_DATABASE_OWNER_URL` / `TEST_DATABASE_URL`, message explicite si la variable manque).
  - Fichiers : `compose.yaml`, `infra/postgres/initdb/10-roles-and-databases.sql`, `.gitattributes`, `.env.example`, `scripts/check.ps1`, `backend/tests/conftest.py`, `backend/tests/db/__init__.py`, `backend/tests/db/test_db_roles.py`.
  - Fin : `docker compose up -d --wait db` sain sur un volume neuf ; les deux tests verts ; `docker compose down` puis `up` garde les données (contrôle manuel, automatisé en T050) ; `check.ps1` exécute l'étape base avant pytest.

- [ ] **T003** `infra` — Alembic, `app/db/tables.py` et migration `0001_identity_and_organizations`
  - Sous-agent : `backend-engineer` · Dépend de : T002 · Couvre : socle de AC-42 à AC-46 (aucun AC seul)
  - Tests d'abord : `tests/db/test_migrations.py::test_upgrade_creates_expected_tables` (8 tables ; `users.google_sub` unique ; `users.email_normalized` indexé non unique ; index unique partiel `invitations (organization_id, email_normalized) WHERE status='pending'` ; CHECK des rôles, des statuts et de la longueur du nom) ; `::test_upgrade_downgrade_roundtrip` (`downgrade base` puis `upgrade head`, la base finit à `head`).
  - Code : `uv add alembic` ; `alembic.ini`, `migrations/env.py` **synchrone** (psycopg), connecté en `forge_owner` via `DATABASE_OWNER_URL`, avec une URL surchargeable par l'appelant (`Config.set_main_option`) pour que les tests migrent `forge_test` ; `migrations/script.py.mako` ; `app/db/tables.py` (objets `Table` Core des 8 tables du plan, horodatages sans `DEFAULT now()` décisionnel : les services passent `now`, cf. T14 du plan) ; `migrations/versions/0001_identity_and_organizations.py` avec son `downgrade`. Fixture de session `migrated_db` (`alembic upgrade head` sur `forge_test` en `forge_owner`) et fixture `clean_db` (`TRUNCATE … CASCADE` en propriétaire : le RLS ne s'applique pas à `TRUNCATE`).
  - Fichiers : `pyproject.toml`, `uv.lock`, `alembic.ini`, `migrations/env.py`, `migrations/script.py.mako`, `migrations/versions/0001_identity_and_organizations.py`, `app/db/__init__.py`, `app/db/tables.py`, `tests/conftest.py`, `tests/db/test_migrations.py`.
  - Fin : tests verts ; `uv run alembic upgrade head` fonctionne aussi sur la base `forge` ; `mypy` vert sur `app/db/`.

- [ ] **T004** `infra` — Migration `0002_row_level_security` (1/2) : RLS forcé et droits minimaux de `forge_app`
  - Sous-agent : `backend-engineer` · Dépend de : T003 · Couvre : AC-42, AC-43, AC-44, AC-45, AC-50 (volet base), AC-58 (volet base)
  - Tests d'abord :
    - `tests/db/test_isolation.py::test_unfiltered_select_sees_only_active_org[organizations-memberships-invitations-conversations-audit_events]` (AC-42)
    - `::test_unfiltered_update_delete_cannot_touch_other_org[table]` (AC-43 ; refus de privilège là où il n'y a ni `UPDATE` ni `DELETE`)
    - `::test_insert_or_move_row_into_other_org_is_rejected[table]` (AC-44)
    - `::test_no_context_returns_no_rows[table]` et `::test_reused_connection_after_transaction_returns_no_rows` (AC-45, cas `''`)
    - `::test_conversation_visible_only_to_its_owner_user` (même organisation, autre utilisateur : aucune ligne ; socle de AC-40)
    - `tests/db/test_db_roles.py::test_app_role_cannot_bypass_rls` (non superutilisateur, sans `BYPASSRLS`, propriétaire d'aucun objet) ; `::test_every_table_is_global_or_org_protected` (liste blanche `users`, `sessions`, `login_requests`, `alembic_version` ; toute autre table a `organization_id` ou est `organizations`, `relrowsecurity` et `relforcerowsecurity`, et une politique `TO forge_app`) ; `::test_audit_table_is_append_only_for_app` (AC-50) ; `::test_test_database_is_postgres_with_forced_rls` (AC-58)
  - Code : fonctions `app_current_org()`, `app_current_user()`, `app_current_user_email()` (avec `NULLIF`) ; `ENABLE` + `FORCE ROW LEVEL SECURITY` sur les 5 tables d'organisation ; politiques `TO forge_app` (USING + WITH CHECK) ; `GRANT` / `REVOKE` exactement comme la table « Droits `forge_app` » du plan ; `downgrade` complet. Fixture `two_orgs` (Acme et Globex, une ligne de chaque table dans chacune) qui insère **en `forge_app` avec `set_config`** : avec `FORCE`, `forge_owner` n'a aucune politique, donc ni lecture ni insertion.
  - Fichiers : `migrations/versions/0002_row_level_security.py`, `tests/db/conftest.py`, `tests/db/test_isolation.py`, `tests/db/test_db_roles.py`.
  - Fin : tous les tests listés verts ; le roundtrip de T003 reste vert avec 0002.

- [ ] **T005** `infra` — Migration `0002_row_level_security` (2/2) : politiques de `forge_self_reader` et fonctions `SECURITY DEFINER`
  - Sous-agent : `backend-engineer` · Dépend de : T004 · Couvre : AC-46 (liste blanche), socle de AC-17 et AC-21 à AC-24
  - Tests d'abord : `tests/db/test_self_reader_functions.py::test_my_memberships_lists_only_current_user_across_orgs` ; `::test_my_pending_invitations_matches_email_and_expiry` (insensible à la casse via `email_normalized`, `p_now` respecté, statut `pending` seulement) ; `::test_my_invitation_hidden_for_other_email` ; `::test_functions_return_nothing_without_user_context` ; `::test_unfiltered_query_as_self_reader_policies_only_shows_current_user` (une fonction volontairement trop large, créée dans le test, ne renvoie toujours que les lignes de l'utilisateur courant) ; `tests/db/test_db_roles.py::test_security_definer_functions_are_whitelisted` (`pg_proc.prosecdef` : exactement les trois fonctions, propriétaire `forge_self_reader`, `search_path` figé, `EXECUTE` révoqué de `PUBLIC`).
  - Code : politiques `TO forge_self_reader` en lecture (`memberships`, `invitations`, `organizations`) ; `app_my_memberships()`, `app_my_pending_invitations(p_now)`, `app_my_invitation(p_id)` (`LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, public`, filtre explicite sur l'utilisateur courant en plus du RLS) ; `ALTER FUNCTION … OWNER TO forge_self_reader` ; `REVOKE EXECUTE … FROM PUBLIC` ; `GRANT EXECUTE … TO forge_app` ; `downgrade`.
  - Fichiers : `migrations/versions/0002_row_level_security.py`, `tests/db/test_self_reader_functions.py`, `tests/db/test_db_roles.py`.
  - Fin : tests verts ; roundtrip vert.

- [ ] **T006** `infra` [P ∥ T004, T005] — `scripts/dev.ps1` : base, migrations et boucle selector
  - Sous-agent : `backend-engineer` · Dépend de : T001, T003 · Couvre : AC-56 (volet manuel ; test automatisé en T049)
  - Tests d'abord : pas de test automatisé dans cette tâche (script PowerShell ; le test `RUN_INFRA=1` arrive en T049, cf. R8). Liste de contrôle manuelle écrite d'abord dans la description du commit.
  - Code : vérifie que Docker répond et que `.env` existe (message clair sinon) → `docker compose up -d --wait db` → `uv run alembic upgrade head` (dans `backend/`) → backend avec `--reload --port 8000 --loop app.core.aio:selector_loop_factory` et frontend dans deux fenêtres, comme aujourd'hui.
  - Fichiers : `scripts/dev.ps1`.
  - Fin : `.\scripts\dev.ps1` sur une base vide : base saine, `alembic current` = `head`, `GET http://localhost:8000/api/health` = `{"status":"ok"}`, Vite répond sur :5173. Message clair si Docker est arrêté.

- [ ] **Fin de phase 1** : `.\scripts\check.ps1` vert (Docker Desktop démarré).

---

## Phase 2 : Socle applicatif

Objectif : réglages, horloge, erreurs, accès à la base par requête, autorisation pure, fabrique d'application, garde réseau et contrôle automatique des routes.

- [ ] **T007** `backend` [P ∥ T008, T009, T010] — `Settings` : nouveaux réglages et validateur de production
  - Sous-agent : `backend-engineer` · Dépend de : T002 · Couvre : configurabilité de FR-8 et FR-20 (socle de AC-8, AC-9, AC-24)
  - Tests d'abord : `tests/test_config.py::test_prod_refuses_insecure_cookies` (`ENVIRONMENT=prod` + `COOKIE_SECURE=false` → erreur de validation) ; `::test_durations_are_configurable` (`SESSION_IDLE_TIMEOUT_HOURS`, `SESSION_MAX_AGE_DAYS`, `INVITATION_TTL_DAYS`, `LOGIN_REQUEST_TTL_MINUTES`, valeurs par défaut 24 / 7 / 7 / 10) ; `::test_google_secret_is_not_repr` (`SecretStr`).
  - Code : champs `database_url`, `cookie_secure` (défaut `true`), durées, `google_client_id`, `google_client_secret`, `oidc_redirect_uri`, `app_base_url`. Les champs restent optionnels à l'import (`app = create_app()` au niveau module ne doit pas casser sans `.env` complet) ; leur absence est signalée au démarrage ou à l'usage, avec un message clair.
  - Fichiers : `app/core/config.py`, `.env.example` (blocs « Google sign-in » et « Sessions & invitations »), `tests/test_config.py`.
  - Fin : tests verts ; tous les champs ajoutés figurent dans `.env.example`.

- [ ] **T008** `backend` [P ∥ T007, T009, T010] — Horloge injectable
  - Sous-agent : `backend-engineer` · Dépend de : — · Couvre : socle de AC-8, AC-9, AC-24
  - Tests d'abord : `tests/test_clock.py::test_system_clock_is_timezone_aware_utc` ; `::test_fake_clock_advances`.
  - Code : `app/core/clock.py` (`Clock` Protocol, `SystemClock`) ; `tests/fakes/clock.py` (`FakeClock` avec `advance(**timedelta)`), réservé aux tests.
  - Fichiers : `app/core/clock.py`, `tests/fakes/__init__.py`, `tests/fakes/clock.py`, `tests/test_clock.py`.
  - Fin : tests verts, `mypy` vert.

- [ ] **T009** `backend` [P ∥ T007, T008, T010] — Format d'erreur unique `{code, message}`
  - Sous-agent : `backend-engineer` · Dépend de : — · Couvre : FR-37, FR-47 (socle), aucun AC seul
  - Tests d'abord : `tests/test_errors.py::test_api_error_renders_code_and_generic_message` (application FastAPI minimale dans le test) ; `::test_validation_errors_keep_fastapi_format` (422 inchangé).
  - Code : `app/core/errors.py` (`ApiError(status, code, message)`, gestionnaire d'exception à enregistrer par `create_app`) ; `app/schemas/errors.py` (`ErrorBody`).
  - Fichiers : `app/core/errors.py`, `app/schemas/errors.py`, `tests/test_errors.py`.
  - Fin : tests verts.

- [ ] **T010** `backend` [P ∥ T007, T008, T009] — `authz.py` pur : permissions, rôles, règles de gestion des rôles
  - Sous-agent : `backend-engineer` · Dépend de : — · Couvre : AC-46 (volet code), socle de AC-20, AC-25, AC-27, AC-28, AC-33, AC-37
  - Tests d'abord : `tests/test_authz.py::test_can_change_role[...]` (paramétré : Admin Membre→Invité ✅, Admin Membre→Admin ❌, Admin Admin→Membre ❌, Propriétaire Membre→Admin ✅, Propriétaire Admin→Propriétaire ✅, Membre Invité→Membre ❌) ; `::test_can_manage_role[...]` ; `::test_no_permission_spans_organizations` (ensemble des permissions figé, aucune permission « toutes organisations ») ; `::test_schema_literals_match_authz` (les `Literal` `Role` et `Permission` de `schemas/common.py` sont identiques aux valeurs de `authz.py`) ; `::test_public_routes_are_health_and_login_only` (`PUBLIC_ROUTES == {"health", "auth_login", "auth_callback"}`).
  - Code : `app/core/authz.py` (`Role`, `Permission`, `ROLE_PERMISSIONS`, `USER_PERMISSIONS`, `ROLE_MANAGEMENT_PERMISSION`, `permissions_for` lu à chaque appel, `can_manage_role`, `can_change_role`, `PUBLIC_ROUTES`, helper qui pose l'attribut `__forge_permission__` sur une dépendance) ; `app/schemas/common.py`. Pas encore de `require()` (T021 et T026).
  - Fichiers : `app/core/authz.py`, `app/schemas/common.py`, `tests/test_authz.py`.
  - Fin : tests verts ; aucun nom de rôle hors de `authz.py`, à part l'invariant « dernier Propriétaire » qui arrivera dans `services/memberships.py`.

- [ ] **T011** `backend` — Moteur async, `RequestDb` et dépendance `request_db`
  - Sous-agent : `backend-engineer` · Dépend de : T005, T007 · Couvre : AC-45 (volet application : pas de fuite de contexte par le pool)
  - Tests d'abord : `tests/db/test_request_db.py::test_set_organization_scopes_queries_to_transaction` ; `::test_context_does_not_leak_to_next_transaction_on_pooled_connection` (pool de taille 1, deux transactions successives) ; `::test_exception_rolls_back` ; `::test_dependency_commits_before_response` (application minimale + `TestClient` avec `loop_factory`).
  - Code : `app/db/engine.py` (`create_async_engine(settings.database_url)`) ; `app/db/context.py` (`RequestDb` : connexion, `set_user`, `set_organization` via `SELECT set_config(…, true)` paramétré ; dépendance `request_db` en `Depends(..., scope="function")` : `connect` → `begin` → `yield` → `commit` / `rollback`). `record_denial` arrive en T025. Dans `tests/conftest.py` : boucle selector pour `pytest-asyncio` sous `win32` (mécanisme vérifié en T001) et fixture `request_db_factory` vers `forge_test`, réutilisée par T018 et T025.
  - Fichiers : `app/db/engine.py`, `app/db/context.py`, `tests/conftest.py`, `tests/db/test_request_db.py`.
  - Fin : tests verts sous Windows ; aucune requête SQL construite par concaténation.

- [ ] **T012** `backend` — `create_app(model, *, settings, http_transport, clock)`, lifespan et clients de test
  - Sous-agent : `backend-engineer` · Dépend de : T007, T008, T009, T011 · Couvre : FR-25 (docs hors dev, cf. R9), risque Windows
  - Tests d'abord : `tests/test_app_factory.py::test_docs_and_openapi_not_served_outside_dev` (404 sur `/docs`, `/redoc`, `/openapi.json` en `test`) ; `::test_docs_served_in_dev` ; `::test_proactor_loop_is_refused_with_explicit_message` (le message cite l'option `--loop app.core.aio:selector_loop_factory`) ; `::test_engine_disposed_on_shutdown` ; `::test_api_error_handler_registered`. Les tests existants de `tests/test_api.py` restent verts (les agents sont encore ouverts).
  - Code : `app/main.py` (signature du plan, moteur créé dans le lifespan puis `dispose`, `clock` dans `app.state`, gestionnaire `ApiError`, `docs_url` / `redoc_url` / `openapi_url` à `None` hors `dev`, garde Proactor) ; `app/api/routes/health.py` (nom de route explicite `health`) ; `tests/conftest.py` (`make_client` construit des `Settings` de test : `ENVIRONMENT=test`, `TEST_DATABASE_URL`, `COOKIE_SECURE=true` ; `TestClient(base_url="https://testserver", backend_options={"loop_factory": asyncio.SelectorEventLoop})` sous `win32`).
  - Fichiers : `app/main.py`, `app/api/routes/health.py`, `tests/conftest.py`, `tests/test_app_factory.py`.
  - Fin : tests verts ; `uv run uvicorn app.main:app --loop app.core.aio:selector_loop_factory` démarre, et sans `--loop` sous Windows l'erreur explicite s'affiche.

- [ ] **T013** `backend` [P ∥ T014] — Garde réseau en test
  - Sous-agent : `backend-engineer` · Dépend de : T012 · Couvre : AC-58 (aucun accès réseau à Google ni au LLM)
  - Tests d'abord : `tests/test_no_network.py::test_outbound_network_is_blocked` (connexion vers une adresse publique → erreur explicite) ; `::test_loopback_is_allowed` (Postgres local reste joignable).
  - Code : fixture `autouse` qui remplace `socket.socket.connect` et refuse toute adresse autre que `127.0.0.1`, `::1` ou `localhost`.
  - Fichiers : `tests/conftest.py`, `tests/test_no_network.py`.
  - Fin : toute la suite reste verte avec la garde active.

- [ ] **T014** `backend` [P ∥ T013] — Contrôle automatique des routes (refus par défaut), avec liste « pas encore protégées »
  - Sous-agent : `backend-engineer` · Dépend de : T010, T012 · Couvre : AC-29 (partiel : complété en T023 et T036)
  - Tests d'abord : `tests/api/test_route_protection.py::test_checker_flags_route_without_permission` (application synthétique : une route sans `__forge_permission__` ni exemption est signalée → rouge tant que le vérificateur n'existe pas) ; `::test_every_route_declares_permission_or_exemption` (parcourt `app.routes` et l'arbre `route.dependant.dependencies`) ; `::test_not_yet_protected_is_exactly_known_set` (la liste `NOT_YET_PROTECTED = {"list_agents", "invoke", "stream"}` vaut exactement l'ensemble des routes non protégées : une **nouvelle** route oubliée casse le test, et la liste doit être vidée en T036).
  - Code : vérificateur dans le fichier de test (aucun code d'application, à part l'attribut déjà posé par `authz.py`).
  - Fichiers : `tests/api/__init__.py`, `tests/api/test_route_protection.py`.
  - Fin : tests verts ; la liste `NOT_YET_PROTECTED` est commentée « à vider en T036 ».

- [ ] **Fin de phase 2** : `.\scripts\check.ps1` vert.

---

## Phase 3 : Authentification

Objectif : faux Google, OIDC (code + PKCE + state + nonce), `login_requests`, sessions serveur, CSRF, déconnexion, journal plateforme sans secret.

- [ ] **T015** `backend` [P ∥ T017] — `IdentityProvider`, `GoogleOIDCProvider.authorization_url` et faux Google (découverte, JWKS)
  - Sous-agent : `backend-engineer` · Dépend de : T007, T008 · Couvre : AC-4 (volet unitaire)
  - Tests d'abord : `tests/auth/test_google.py::test_authorization_url_requests_basic_identity_with_pkce` (scope exactement `{openid, email, profile}`, `prompt=select_account`, ni `access_type=offline` ni scope Drive, `code_challenge_method=S256`, `state` et `nonce` présents) ; `::test_discovery_document_is_cached`.
  - Code : `uv remove --dev httpx` puis `uv add httpx "pyjwt[crypto]"` ; `app/auth/provider.py` (`IdentityProvider` Protocol, `VerifiedIdentity`) ; `app/auth/google.py` (découverte, `authorization_url`, client `httpx.AsyncClient` au transport injectable) ; `tests/fakes/google.py` (clé RSA générée, document de découverte, JWKS, `handler` pour `httpx.MockTransport`).
  - Fichiers : `pyproject.toml`, `uv.lock`, `app/auth/__init__.py`, `app/auth/provider.py`, `app/auth/google.py`, `tests/fakes/google.py`, `tests/auth/__init__.py`, `tests/auth/test_google.py`.
  - Fin : tests verts ; aucun faux fournisseur dans `app/` (FR-6).

- [ ] **T016** `backend` [P ∥ T017, T018] — Échange du code et validation de l'ID token
  - Sous-agent : `backend-engineer` · Dépend de : T015 · Couvre : AC-2 et AC-5 (volet unitaire), AC-13 (interrupteur de panne du faux Google)
  - Tests d'abord : `tests/auth/test_google.py::test_exchange_returns_verified_identity` ; `::test_id_token_rejected[bad_signature-bad_audience-bad_issuer-expired-nonce_mismatch-azp_mismatch]` ; `::test_unverified_email_is_rejected` ; `::test_jwks_reloaded_on_unknown_kid` ; `::test_hd_claim_is_ignored` (FR-1) ; `::test_access_token_is_not_kept` (`VerifiedIdentity` ne porte aucun jeton).
  - Code : `GoogleOIDCProvider.exchange(code, code_verifier, nonce)` (validation exacte du plan) ; faux Google : endpoint de jeton (codes à usage unique, contrôle PKCE, `email_verified` et `hd` paramétrables), interrupteur « indisponible ».
  - Fichiers : `app/auth/google.py`, `tests/fakes/google.py`, `tests/auth/test_google.py`.
  - Fin : tests verts.

- [ ] **T017** `backend` [P ∥ T015, T016, T018] — Journal plateforme et filtre du journal d'accès
  - Sous-agent : `backend-engineer` · Dépend de : — (phase 2 terminée) · Couvre : AC-51 (volet unitaire), FR-39, FR-41
  - Tests d'abord : `tests/test_security_log.py::test_security_event_has_closed_field_list` (seuls `name`, `user_id`, `organization_id`, `reason` ; nom d'événement et raison dans les listes fermées du plan) ; `::test_redact_filter_masks_auth_query_strings` (un `LogRecord` d'accès `/api/auth/callback?code=…&state=…` devient `?[redacted]` ; les autres chemins sont intacts).
  - Code : `app/core/security_log.py` (`security_event`, logger `forge.security`, `RedactQueryFilter`). L'installation du filtre dans l'application se fait en T024.
  - Fichiers : `app/core/security_log.py`, `tests/test_security_log.py`.
  - Fin : tests verts.

- [ ] **T018** `backend` [P ∥ T016, T017] — Services `login_requests`, sessions et utilisateurs
  - Sous-agent : `backend-engineer` · Dépend de : T008, T011, T015 · Couvre : AC-3, AC-8, AC-9, AC-10, AC-11 (volet service)
  - Tests d'abord : `tests/auth/test_sessions_service.py::test_login_request_is_single_use` ; `::test_expired_login_request_is_refused` ; `::test_expired_login_requests_are_purged` ; `::test_session_token_stored_only_as_sha256` ; `::test_session_validity[fresh-idle_23h59-idle_24h01-absolute_7d-revoked]` (horloge factice) ; `::test_touch_updates_last_seen` ; `tests/auth/test_users_service.py::test_upsert_user_by_google_sub_updates_profile` (même `id`, nouvel email, nom et photo).
  - Code : `app/auth/sessions.py` (`create_login_request`, `consume_login_request` par `UPDATE … WHERE consumed_at IS NULL AND expires_at > :now RETURNING`, `create_session` avec jeton `token_urlsafe(32)` et CSRF, `load_valid_session` avec motif de refus, `touch`, `revoke`) ; `app/auth/users.py` (`upsert_user` par `google_sub`). Toutes les dates viennent de `now` passé en argument.
  - Fichiers : `app/auth/sessions.py`, `app/auth/users.py`, `tests/auth/test_sessions_service.py`, `tests/auth/test_users_service.py`.
  - Fin : tests verts.

- [ ] **T019** `backend` — Routes `GET /api/auth/login` et `GET /api/auth/callback` (parcours réussi)
  - Sous-agent : `backend-engineer` · Dépend de : T012, T016, T018 · Couvre : AC-1, AC-3, AC-4, AC-6
  - Tests d'abord : `tests/auth/test_login.py::test_login_succeeds_for_workspace_and_gmail_accounts[workspace-gmail]` ; `::test_relogin_with_changed_email_keeps_same_account` ; `::test_authorization_request_asks_only_basic_identity` (302, paramètres de l'URL, cookie `forge_login` `HttpOnly`, `SameSite=Lax`, 10 min) ; `::test_login_response_has_no_google_token_and_hardened_cookie` (aucun jeton Google dans les en-têtes ni le corps ; `forge_session` `HttpOnly; Secure; SameSite=Lax; Path=/`, sans `Domain` ; 303 vers `APP_BASE_URL/` ; `forge_login` effacé).
  - Code : `app/api/routes/auth.py` (`auth_login`, `auth_callback` : noms de route exemptés) ; `app/main.py` (router, fournisseur construit avec `http_transport`) ; `tests/conftest.py` (fixture `fake_google`, helper `login(client, identity)` qui fait tout le parcours OIDC).
  - Fichiers : `app/api/routes/auth.py`, `app/main.py`, `tests/conftest.py`, `tests/auth/test_login.py`.
  - Fin : tests verts.

- [ ] **T020** `backend` — Callback : refus, rejeu, fixation de session et événements de connexion
  - Sous-agent : `backend-engineer` · Dépend de : T017, T019 · Couvre : AC-2, AC-5, AC-11
  - Tests d'abord : `tests/auth/test_login.py::test_login_refused_when_email_not_verified` (aucune ligne `sessions`, aucun cookie de session, `login_failed` / `email_not_verified` journalisé) ; `::test_callback_rejected[missing_cookie-state_mismatch-replayed-nonce_mismatch-bad_audience-bad_issuer-bad_signature-expired_request]` (303 vers `/?login_error=failed`, aucune session) ; `::test_callback_cancelled_by_user` (`error=access_denied` → `login_error=cancelled`) ; `::test_login_unavailable_without_google_credentials` (`login_error=unavailable`) ; `::test_pre_login_session_id_never_authenticated` (un cookie `forge_session` présenté avant la connexion reste refusé, la nouvelle session a un autre identifiant, l'ancienne est révoquée).
  - Code : `app/api/routes/auth.py` (comparaisons en `hmac.compare_digest`, `security_event` `login_succeeded` / `login_failed` avec la raison).
  - Fichiers : `app/api/routes/auth.py`, `tests/auth/test_login.py`.
  - Fin : tests verts.

- [ ] **T021** `backend` — Session courante, `require_authenticated` et `GET /api/auth/session`
  - Sous-agent : `backend-engineer` · Dépend de : T020 · Couvre : AC-8, AC-9, AC-13
  - Tests d'abord : `tests/auth/test_sessions.py::test_session_endpoint_returns_user_and_csrf_token` ; `::test_session_idle_timeout[23h59-ok-24h01-refused]` ; `::test_session_absolute_timeout_despite_activity` ; `::test_authorized_request_works_when_identity_provider_down` (faux Google en panne, aucune requête vers lui) ; `::test_rejected_session_is_logged[unknown-revoked-idle_expired-absolute_expired]` (`session_rejected`).
  - Code : `app/auth/dependencies.py` (`current_session` : cookie → hash → `load_valid_session(now=clock.now())` → `touch` ; 401 `not_authenticated`) ; `app/core/authz.py` (`require_authenticated(perm)`, attribut `__forge_permission__`) ; `app/api/routes/auth.py` (`GET /auth/session`, `session:read`) ; `app/schemas/auth.py` (`UserOut`, `SessionOut`).
  - Fichiers : `app/auth/dependencies.py`, `app/core/authz.py`, `app/api/routes/auth.py`, `app/schemas/auth.py`, `tests/auth/test_sessions.py`.
  - Fin : tests verts ; T014 toujours vert (la nouvelle route déclare sa permission).

- [ ] **T022** `backend` — CSRF et `POST /api/auth/logout`
  - Sous-agent : `backend-engineer` · Dépend de : T021 · Couvre : AC-10, AC-12 (volet backend)
  - Tests d'abord : `tests/auth/test_sessions.py::test_logout_revokes_server_side_session` (copie du cookie refusée en 401 après la déconnexion ; `logout` journalisé) ; `tests/auth/test_csrf.py::test_every_mutating_route_requires_csrf[route]` (paramétré depuis `app.routes` : toute méthode non sûre, session valide, en-têtes d'organisation et corps JSON valide minimal, sans `X-CSRF-Token` → 403 `csrf_failed` ; il s'étendra seul aux routes des phases 4 et 5) ; `::test_wrong_csrf_token_changes_nothing` (mauvais jeton sur `logout` → 403 et session toujours valide).
  - Code : contrôle CSRF dans `app/auth/dependencies.py` (`hmac.compare_digest`, avant toute écriture, pour `POST`, `PATCH`, `PUT`, `DELETE`) ; `POST /auth/logout` (`session:logout`, 204, cookie effacé).
  - Fichiers : `app/auth/dependencies.py`, `app/api/routes/auth.py`, `tests/auth/test_sessions.py`, `tests/auth/test_csrf.py`.
  - Fin : tests verts.

- [ ] **T023** `backend` [P ∥ T024] — Refus anonyme par défaut et unicité du chemin de connexion
  - Sous-agent : `backend-engineer` · Dépend de : T022 · Couvre : AC-7, AC-29 (complément), AC-30
  - Tests d'abord : `tests/api/test_route_protection.py::test_anonymous_request_to_protected_route_is_401[route]` (paramétré depuis `app.routes`, hors exemptions et hors `NOT_YET_PROTECTED`) ; `::test_exemptions_exist_as_routes` (chaque nom de `PUBLIC_ROUTES` correspond à une route réelle) ; `tests/auth/test_login_paths.py::test_session_creation_only_from_google_callback` (analyse AST de `app/` : `create_session` n'est appelée que depuis `auth_callback` ; d'abord vu rouge sur un extrait synthétique) ; `::test_no_fake_identity_provider_in_app_package`.
  - Code : aucun code applicatif attendu (si un test révèle un trou, le corriger dans la route concernée).
  - Fichiers : `tests/api/test_route_protection.py`, `tests/auth/test_login_paths.py`.
  - Fin : tests verts.

- [ ] **T024** `backend` [P ∥ T023] — Journaux sans secret de bout en bout
  - Sous-agent : `backend-engineer` · Dépend de : T017, T022 · Couvre : AC-51
  - Tests d'abord : `tests/test_security_log.py::test_login_logout_events_logged_without_secrets` (`caplog` : connexion réussie, connexion refusée, déconnexion ; les trois événements présents ; aucune des valeurs connues n'apparaît dans aucun enregistrement : cookie de session, cookie de connexion, CSRF, `code`, `id_token`, `state`, `code_verifier`) ; `::test_app_installs_access_log_redaction` (après le lifespan, le logger `uvicorn.access` porte le filtre).
  - Code : installation de `RedactQueryFilter` sur `uvicorn.access` dans `create_app`.
  - Fichiers : `app/main.py`, `tests/test_security_log.py`.
  - Fin : tests verts.

- [ ] **Fin de phase 3** : `.\scripts\check.ps1` vert.

---

## Phase 4 : Organisations, invitations, membres et journal d'audit

Objectif : contexte d'organisation par requête, `require(perm)`, refus journalisés malgré le `ROLLBACK`, et toutes les routes de gestion. Les tâches sont séquentielles : elles partagent `app/main.py`, `tests/factories.py` et les services.

- [ ] **T025** `backend` — Service d'audit et `RequestDb.record_denial`
  - Sous-agent : `backend-engineer` · Dépend de : T011, T022 · Couvre : socle de AC-20, AC-33, AC-48 (refus journalisé)
  - Tests d'abord : `tests/services/test_audit.py::test_record_writes_event_in_active_org` ; `::test_record_denial_survives_rollback` (écriture métier annulée, entrée `denied` conservée) ; `::test_details_accept_only_scalars` (aucun secret possible par construction, FR-41).
  - Code : `app/services/audit.py` (`record(db, action, target_type, target_id, outcome, details, now)`) ; `RequestDb.record_denial(action, target, details)` dans `app/db/context.py` (`ROLLBACK` → nouvelle transaction → contexte reposé → `INSERT` → `COMMIT` → lève l'`ApiError`).
  - Fichiers : `app/services/__init__.py`, `app/services/audit.py`, `app/db/context.py`, `tests/services/__init__.py`, `tests/services/test_audit.py`.
  - Fin : tests verts.

- [ ] **T026** `backend` — `require(perm)`, contexte d'organisation et `GET /api/organization`
  - Sous-agent : `backend-engineer` · Dépend de : T025 · Couvre : AC-16, R2 (`organization_access_denied`)
  - Tests d'abord : `tests/api/test_organizations.py::test_get_active_organization_returns_role_and_permissions` ; `::test_cannot_activate_organization_without_membership` (Alice, membre d'Acme seulement, avec `X-Organization-Id` = Globex → 404 `organization_not_found`, **octet pour octet** identique à un uuid inexistant ; `organization_access_denied` au journal plateforme ; rien au journal de Globex) ; `::test_missing_or_malformed_org_header_is_400`.
  - Code : `require(perm)` dans `app/core/authz.py` (session → CSRF → en-tête → `set_user` / `set_organization` → adhésion lue avec filtre explicite + RLS → permission → sinon `record_denial("permission.denied")` + 403) ; `app/api/routes/organizations.py` (`GET /organization`) ; `app/schemas/organizations.py` (`OrganizationSummary`, `ActiveOrganization`) ; router dans `app/main.py` ; `tests/factories.py` (monde Acme / Globex, utilisateurs Alice, Bob, Carole, Dave, adhésions insérées en `forge_app` sous contexte, sessions créées par le service de T018, helper `client_as(user, org)` qui pose cookie, `X-CSRF-Token` et `X-Organization-Id`) ; fixture `world` dans `tests/conftest.py`.
  - Fichiers : `app/core/authz.py`, `app/api/routes/organizations.py`, `app/schemas/organizations.py`, `app/main.py`, `tests/factories.py`, `tests/conftest.py`, `tests/api/test_organizations.py`.
  - Fin : tests verts ; T014, T022 et T023 s'appliquent à la nouvelle route et restent verts.

- [ ] **T027** `backend` — `GET /api/organizations` et `POST /api/organizations`
  - Sous-agent : `backend-engineer` · Dépend de : T026 · Couvre : AC-14, AC-15, AC-17
  - Tests d'abord : `tests/api/test_organizations.py::test_create_organization_makes_creator_sole_owner` (201, unique Propriétaire, `organization.created` lu dans `audit_events` sous le contexte d'Acme) ; `::test_list_my_organizations_shows_only_memberships` (aucune trace de Globex) ; `::test_switch_active_organization_applies_new_role` (Alice Membre d'Acme et Admin de Globex : changer l'en-tête suffit, les permissions renvoyées sont celles d'Admin, sans nouvelle connexion) ; `::test_organization_name_is_validated[empty-blank-too_long]` (422) ; `::test_organization_names_are_not_unique`.
  - Code : `app/services/organizations.py` (`create` : `uuid4` en Python, `set_organization(id)` **avant** l'`INSERT`, adhésion Propriétaire et audit dans la même transaction ; `list_mine` via `app_my_memberships()`) ; routes et schéma `OrganizationCreate` (nom nettoyé par `strip`).
  - Fichiers : `app/services/organizations.py`, `app/api/routes/organizations.py`, `app/schemas/organizations.py`, `tests/api/test_organizations.py`.
  - Fin : tests verts.

- [ ] **T028** `backend` — Renommer et quitter l'organisation
  - Sous-agent : `backend-engineer` · Dépend de : T027 · Couvre : AC-28, AC-35 (départ), AC-36, AC-59
  - Tests d'abord : `tests/api/test_organizations.py::test_owner_renames_organization` (nouveau nom visible par les autres membres à leur prochain `GET`, `organization.renamed` journalisé) ; `::test_non_owner_cannot_rename[admin-member-guest]` (403 + `permission.denied`) ; `tests/api/test_members.py::test_member_leaves_organization` (plus membre, requête suivante dans Acme → 404, `member.left` journalisé) ; `::test_last_owner_cannot_leave` (409 `last_owner`, refus journalisé) ; `tests/test_authz.py::test_changing_role_definition_changes_behavior` (`monkeypatch` de `ROLE_PERMISSIONS` : `organization:rename` donné à `member` → le renommage par un Membre aboutit, sans autre modification).
  - Code : `PATCH /organization` (`organization:rename`, schéma `OrganizationRename`) ; `POST /organization/leave` (`organization:leave`) ; `app/services/memberships.py` (`leave` + invariant « au moins un Propriétaire »).
  - Fichiers : `app/api/routes/organizations.py`, `app/services/organizations.py`, `app/services/memberships.py`, `app/schemas/organizations.py`, `tests/api/test_organizations.py`, `tests/api/test_members.py`, `tests/test_authz.py`.
  - Fin : tests verts.

- [ ] **T029** `backend` — Lister les membres et changer un rôle
  - Sous-agent : `backend-engineer` · Dépend de : T028 · Couvre : AC-33 (modification), AC-34, AC-35 (rétrogradation), AC-37, AC-62, AC-63
  - Tests d'abord, dans `tests/api/test_members.py` : `::test_list_members_requires_members_read[owner-admin-member-guest]` ; `::test_admin_cannot_modify_admin_or_owner[admin-owner]` (403 + `denied` journalisé) ; `::test_owner_promotes_then_demotes_admin` (permissions de Bob suivant chaque changement, deux `member.role_changed` avec `from_role` / `to_role`) ; `::test_last_owner_cannot_be_demoted` (409 `last_owner`) ; `::test_no_self_elevation[admin_to_owner-member_to_admin-member_to_owner]` ; `::test_ownership_transfer_promote_then_leave` (AC-62) ; `::test_owner_demotes_coowner_then_cannot_demote_self_as_last_owner` (AC-63) ; `::test_unknown_member_is_404`.
  - Code : `app/api/routes/members.py` (`GET /organization/members`, `PATCH /organization/members/{user_id}`) ; `app/schemas/members.py` ; `app/services/memberships.py` (`change_role` : `can_change_role(perms, from_role, to_role)`, invariant dernier Propriétaire, audit) ; router dans `app/main.py`. Le verrou de concurrence peut déjà être posé ici ; il est exigé par T030.
  - Fichiers : `app/api/routes/members.py`, `app/schemas/members.py`, `app/services/memberships.py`, `app/main.py`, `tests/api/test_members.py`.
  - Fin : tests verts.

- [ ] **T030** `backend` — Invariant « au moins un Propriétaire » sous concurrence
  - Sous-agent : `backend-engineer` · Dépend de : T029 · Couvre : AC-35 (concurrence)
  - Tests d'abord : `tests/services/test_memberships.py::test_concurrent_owner_demotions_keep_one_owner` (deux connexions, Alice et Bob Propriétaires se rétrogradent mutuellement en même temps : une demande aboutit, l'autre reçoit `last_owner`, il reste exactement un Propriétaire). Si le test passe d'emblée parce que T029 verrouille déjà, le voir rouge en retirant temporairement le `FOR UPDATE`.
  - Code : `SELECT … FROM memberships WHERE organization_id = :org AND role = 'owner' FOR UPDATE` avant toute rétrogradation, tout retrait ou tout départ d'un Propriétaire.
  - Fichiers : `app/services/memberships.py`, `tests/services/test_memberships.py`.
  - Fin : test vert de façon stable (lancé 20 fois de suite sans échec).

- [ ] **T031** `backend` — Retirer un membre
  - Sous-agent : `backend-engineer` · Dépend de : T030 · Couvre : AC-32, AC-33 (retrait), AC-35 (retrait)
  - Tests d'abord, dans `tests/api/test_members.py` : `::test_removed_member_loses_access_keeps_other_memberships` (requête suivante de Bob dans Acme → 404 ; son adhésion à Globex intacte ; `member.removed` journalisé) ; `::test_admin_cannot_remove_admin_or_owner[admin-owner]` (403 + `denied`) ; `::test_last_owner_cannot_be_removed` (409).
  - Code : `DELETE /organization/members/{user_id}` (`members:manage` + `can_manage_role(perms, target_role)`) ; `memberships.remove`.
  - Fichiers : `app/api/routes/members.py`, `app/services/memberships.py`, `tests/api/test_members.py`.
  - Fin : tests verts.

- [ ] **T032** `backend` — Invitations côté organisation : créer, lister, révoquer
  - Sous-agent : `backend-engineer` · Dépend de : T031 · Couvre : AC-19, AC-20, AC-25, AC-26
  - Tests d'abord, dans `tests/api/test_invitations.py` : `::test_admin_invites_member` (201, `pending`, `invitation.created` journalisé, email normalisé en minuscules) ; `::test_admin_cannot_invite_admin_or_owner[admin-owner]` (403 + `denied` journalisé) ; `::test_admin_cannot_revoke_admin_invitation` ; `::test_owner_revokes_invitation` (`invitation.revoked`) ; `::test_inviting_existing_member_is_refused` (409 `already_member`, message explicite, rôle de Bob inchangé) ; `::test_duplicate_pending_invitation_is_409` (R5 ; nouvelle invitation permise après refus ou expiration) ; `::test_list_pending_invitations_excludes_expired` ; `::test_invalid_email_is_422`.
  - Code : `app/api/routes/invitations.py` (`GET` / `POST /organization/invitations`, `DELETE /organization/invitations/{invitation_id}`) ; `app/schemas/invitations.py` (`InvitationCreate`, `UserRef`, `Invitation`) ; `app/services/invitations.py` (`expires_at = now + INVITATION_TTL_DAYS`) ; router dans `app/main.py`.
  - Fichiers : `app/api/routes/invitations.py`, `app/schemas/invitations.py`, `app/services/invitations.py`, `app/main.py`, `tests/api/test_invitations.py`.
  - Fin : tests verts.

- [ ] **T033** `backend` — Invitations côté invité : lister, accepter, refuser
  - Sous-agent : `backend-engineer` · Dépend de : T032 · Couvre : AC-21, AC-22, AC-23, AC-24, AC-60, AC-61
  - Tests d'abord, dans `tests/api/test_invitations.py` : `::test_invitee_accepts_with_case_insensitive_email` (Bob connecté en `Bob@Exemple.com` ; membre avec le rôle prévu ; réutilisation → 404) ; `::test_other_email_cannot_accept_invitation` (404, pas membre) ; `::test_invitee_declines_invitation` (`invitation.declined`, plus utilisable) ; `::test_expired_or_revoked_invitation_cannot_be_accepted[expired-revoked]` (`clock.advance(days=7)` → 410 + `invitation.expired_on_use` validé malgré l'erreur ; révoquée → 404) ; `::test_invitation_sends_no_email_and_appears_at_next_login` (parcours de connexion par le faux Google, puis `GET /invitations/mine` ; aucune connexion sortante, garde de T013 ; aucun import de `smtplib` ni `email` dans `app/`) ; `::test_same_domain_without_invitation_gets_no_membership` ; `::test_accept_when_already_member_is_409` ; `tests/api/test_members.py::test_owner_invites_second_owner` (AC-61 : deux Propriétaires avec les mêmes permissions).
  - Code : `GET /invitations/mine` (`app_my_pending_invitations(now)`), `POST /invitations/{id}/accept` et `/decline` (`invitation:respond`, `app_my_invitation(id)`, `set_organization` **seulement après** la preuve que l'invitation s'adresse à l'utilisateur, `UPDATE … WHERE status = 'pending' RETURNING`) ; schéma `MyInvitation`.
  - Fichiers : `app/api/routes/invitations.py`, `app/schemas/invitations.py`, `app/services/invitations.py`, `tests/api/test_invitations.py`, `tests/api/test_members.py`.
  - Fin : tests verts.

- [ ] **T034** `backend` — Journal d'audit : `GET /api/organization/audit`
  - Sous-agent : `backend-engineer` · Dépend de : T033 · Couvre : AC-48, AC-49, AC-50 (volet routes)
  - Tests d'abord : `tests/api/test_audit.py::test_audit_log_records_sequence_without_other_org` (invitation, acceptation, changement de rôle, retrait, refus de permission dans Acme, plus de l'activité dans Globex : chaque action avec auteur, cible, date et résultat ; aucune entrée de Globex) ; `::test_member_and_guest_cannot_read_audit[member-guest]` ; `::test_audit_pagination_with_before_id` ; `::test_audit_limit_is_bounded[0-201]` (422) ; `tests/api/test_route_protection.py::test_no_route_mutates_audit` (aucune route `PATCH`, `PUT` ou `DELETE` sous `/organization/audit`, et aucune route n'importe une écriture d'audit autre que `services.audit.record`).
  - Code : `app/api/routes/audit.py` ; `app/schemas/audit.py` (`AuditAction`, `AuditActor`, `AuditEvent`, `AuditPage`) ; lecture dans `app/services/audit.py` ; router dans `app/main.py`.
  - Fichiers : `app/api/routes/audit.py`, `app/schemas/audit.py`, `app/services/audit.py`, `app/main.py`, `tests/api/test_audit.py`, `tests/api/test_route_protection.py`.
  - Fin : tests verts.

- [ ] **T035** `backend` — Identifiants d'une autre organisation indiscernables d'identifiants inexistants
  - Sous-agent : `backend-engineer` · Dépend de : T034 · Couvre : AC-47
  - Tests d'abord : `tests/api/test_cross_org_ids.py::test_foreign_ids_look_like_missing_ids[patch_member-delete_member-revoke_invitation]` (un Admin d'Acme vise un membre ou une invitation de Globex : statut, en-têtes utiles et corps identiques à ceux d'un uuid aléatoire ; rien n'est modifié dans Globex).
  - Code : aucun attendu (le RLS rend la ligne invisible) ; corriger le chemin de code s'il diffère.
  - Fichiers : `tests/api/test_cross_org_ids.py`.
  - Fin : test vert.

- [ ] **Fin de phase 4** : `.\scripts\check.ps1` vert.

---

## Phase 5 : Agents et conversations

- [ ] **T036** `backend` — Routes d'agents protégées par `require("agents:use")` et non-régression 000
  - Sous-agent : `backend-engineer` · Dépend de : T035 · Couvre : AC-18 (volet API), AC-29 (complet), AC-31, AC-38, AC-39 (volet API)
  - Tests d'abord : `tests/test_api.py` adapté (`test_list_agents`, `test_invoke_returns_answer_and_thread`, `test_invoke_runs_custom_tool`, `test_stream_emits_sse_events`, `test_unknown_agent_returns_404` passent par un Membre authentifié d'Acme ; `test_health` reste anonyme) ; `tests/api/test_agents_access.py::test_guest_cannot_list_or_invoke_agents[list-invoke-stream]` (403 `permission_denied` **avant** l'ouverture du flux) ; `::test_anonymous_cannot_use_agents` ; `tests/api/test_organizations.py::test_user_without_organization_cannot_use_agents` ; `tests/api/test_members.py::test_demotion_to_guest_takes_effect_on_next_request` (AC-31) ; `tests/api/test_route_protection.py` : `NOT_YET_PROTECTED` vidé, le test exige désormais un ensemble vide.
  - Code : `require("agents:use")` sur les trois routes de `app/api/routes/agents.py` ; `make_client` étendu (ou fixture `member_client`) pour un Membre authentifié avec le faux modèle.
  - Fichiers : `app/api/routes/agents.py`, `tests/conftest.py`, `tests/test_api.py`, `tests/api/test_agents_access.py`, `tests/api/test_organizations.py`, `tests/api/test_members.py`, `tests/api/test_route_protection.py`.
  - Fin : tests verts ; plus aucune route hors exemption sans permission.

- [ ] **T037** `backend` [P ∥ T038] — Conversations : propriété, `thread_id` UUID et clé de checkpoint composée
  - Sous-agent : `backend-engineer` · Dépend de : T036 · Couvre : AC-38 (non-régression après le changement de contrat), AC-40, AC-41, R3
  - Tests d'abord : `tests/api/test_conversations.py::test_other_user_cannot_continue_conversation[member-admin]` (réponse identique à celle d'un uuid inexistant ; le faux modèle n'a aucune réponse scriptée, donc l'appeler ferait échouer le test) ; `::test_conversation_not_reachable_from_other_organization` ; `::test_owner_continues_own_conversation` ; `::test_unknown_thread_id_is_404` (R3) ; `::test_thread_id_of_other_agent_is_404` ; `::test_non_uuid_thread_id_is_422` ; `::test_stream_refusal_happens_before_sse` (404 en JSON, pas un événement `error`) ; `::test_no_db_connection_held_during_stream` (pool rendu avant le premier token).
  - Code : `app/schemas/agents.py` (`thread_id: UUID | None`, `RunResponse.thread_id: UUID`) ; `app/services/conversations.py` (`get_or_create(db, conversation_id, agent_name)`, filtre explicite organisation + utilisateur, RLS en dessous) ; `app/api/routes/agents.py` (conversation contrôlée ou créée **avant** de renvoyer l'`EventSourceResponse`, transaction validée par `request_db` en `scope="function"`, clé `f"{organization_id}/{user_id}/{conversation_id}"`). `AgentRegistry`, `studio.py` et `langgraph.json` inchangés.
  - Fichiers : `app/schemas/agents.py`, `app/services/conversations.py`, `app/api/routes/agents.py`, `tests/api/test_conversations.py`.
  - Fin : tests verts, `tests/test_api.py` toujours vert sans modification.

- [ ] **T038** `backend` [P ∥ T037] — Matrice complète des permissions (FR-23)
  - Sous-agent : `backend-engineer` · Dépend de : T036 · Couvre : AC-27
  - Tests d'abord : `tests/api/test_permission_matrix.py::test_role_permission_matrix[role-action]` (matrice FR-23 **recopiée telle quelle** dans le test, sans importer `authz.py` ; une action par ligne : `GET /organizations` + `GET /organization`, `POST /organization/leave`, `GET /agents` (liste seulement, pour ne pas dépendre de T037), `GET /organization/members` + `GET /organization/invitations`, inviter / retirer / changer le rôle d'un Membre ou d'un Invité, nommer ou retirer un Admin, `GET /organization/audit`, nommer ou retirer un Propriétaire, `PATCH /organization` ; ✅ → 2xx, ❌ → 403). Le monde de test est recréé pour chaque cas.
  - Code : aucun attendu. Un écart révèle un bug dans `authz.py` ou dans une route : le corriger dans la tâche, ou s'arrêter si c'est la matrice de la spec qui est en cause.
  - Fichiers : `tests/api/test_permission_matrix.py` (helpers locaux au fichier, pas de modification de `tests/factories.py`).
  - Fin : test vert ; durée de la suite notée dans le Journal (risque « durée » du plan).

- [ ] **Fin de phase 5** : `.\scripts\check.ps1` vert.

---

## Phase 6 : Interface

Aucune nouvelle dépendance frontend. Toutes les tâches suivent `frontend/CLAUDE.md` : `fetch` simulé, comportement visible, clavier.

- [ ] **T039** `frontend` [P ∥ T040] — Client `lib/api.ts` : types, `request()`, CSRF, en-tête d'organisation, `ApiError`, 401
  - Sous-agent : `frontend-engineer` · Dépend de : T037 (contrat figé) · Couvre : AC-12 (volet UI), AC-55 (volet client)
  - Tests d'abord, dans `src/lib/api.test.ts` : « adds X-CSRF-Token on unsafe methods » (et pas sur `GET`) ; « adds X-Organization-Id when orgId is given » ; « throws ApiError with status and code » ; « calls onUnauthenticated on 401 » ; « LOGIN_URL is /api/auth/login ».
  - Code : types TS du plan (miroir exact des schémas Pydantic) ; `request()` interne ; `setCsrfToken()` (jeton gardé en mémoire du module, jamais dans `localStorage`) ; `setOnUnauthenticated()` ; fonctions `getSession`, `logout`, `listMyOrganizations`, `createOrganization`, `listMyInvitations`, `acceptInvitation`, `declineInvitation`, `getActiveOrganization`, `renameOrganization`, `leaveOrganization`, `listMembers`, `changeMemberRole`, `removeMember`, `listInvitations`, `createInvitation`, `revokeInvitation`, `listAuditEvents`. Les fonctions d'agents (`listAgents`, `streamAgent`) sont **inchangées** ici : elles changent en T048, avec leurs appelants.
  - Fichiers : `src/lib/api.ts`, `src/lib/api.test.ts`.
  - Fin : `pnpm lint`, `pnpm typecheck`, `pnpm test` verts.

- [ ] **T040** `frontend` [P ∥ T039] — Messages d'erreur en français
  - Sous-agent : `frontend-engineer` · Dépend de : — (phase 5 terminée) · Couvre : FR-47 (socle des messages de AC-20, AC-26, AC-35, AC-55)
  - Tests d'abord : `src/lib/errors.test.ts` « maps every backend error code to a French message » (liste des codes du plan) ; « falls back to a generic message for unknown codes » ; « never exposes the raw message ».
  - Code : `src/lib/errors.ts` (`messageFor(code)`), sans dépendre de `api.ts`.
  - Fichiers : `src/lib/errors.ts`, `src/lib/errors.test.ts`.
  - Fin : vérifications frontend vertes.

- [ ] **T041** `frontend` [P ∥ T042, T043, T044, T045, T046] — `AuthProvider`, `useAuth` et `LoginPage`
  - Sous-agent : `frontend-engineer` · Dépend de : T039, T040 · Couvre : AC-52 (page), AC-55 (contexte)
  - Tests d'abord : `src/features/auth/LoginPage.test.tsx` « shows only the Google sign-in link » (lien vers `/api/auth/login`, accessible au clavier) ; « shows session-ended message » ; « shows login failure message and cleans the URL » (`?login_error=failed`, `history.replaceState`) ; `src/features/auth/AuthProvider.test.tsx` « loads the session and sets the CSRF token » ; « becomes a visitor with reason session_ended on 401 after being signed in » ; « logout calls the API and becomes a visitor ».
  - Fichiers : `src/features/auth/AuthProvider.tsx`, `src/features/auth/useAuth.ts`, `src/features/auth/LoginPage.tsx`, `src/features/auth/LoginPage.test.tsx`, `src/features/auth/AuthProvider.test.tsx`.
  - Fin : vérifications frontend vertes.

- [ ] **T042** `frontend` [P ∥ T041, T043, T044, T045, T046] — `useOrganizations` et `OrganizationSwitcher`
  - Sous-agent : `frontend-engineer` · Dépend de : T039, T040 · Couvre : AC-53 (sélecteur)
  - Tests d'abord : `src/features/organizations/OrganizationSwitcher.test.tsx` « lists organizations with role and switches on change » (`<select aria-label="Organisation">`, clavier) ; `src/features/organizations/useOrganizations.test.tsx` « restores the remembered organization only if still a member » (`localStorage` `forge.activeOrganizationId` revalidé contre la liste) ; « loads permissions of the active organization ».
  - Fichiers : `src/features/organizations/useOrganizations.ts`, `src/features/organizations/useOrganizations.test.tsx`, `src/features/organizations/OrganizationSwitcher.tsx`, `src/features/organizations/OrganizationSwitcher.test.tsx`.
  - Fin : vérifications frontend vertes.

- [ ] **T043** `frontend` [P ∥ T041, T042, T044, T045, T046] — Écran « aucune organisation », invitations reçues et création d'organisation
  - Sous-agent : `frontend-engineer` · Dépend de : T039, T040 · Couvre : AC-18 (volet UI), FR-22 (affichage)
  - Tests d'abord : `src/features/organizations/NoOrganizationScreen.test.tsx` « shows pending invitations and create form » ; `src/features/organizations/MyInvitations.test.tsx` « accepts an invitation then notifies the parent » ; « declines an invitation » ; « shows translated error when the invitation expired » (410) ; `src/features/organizations/CreateOrganizationForm.test.tsx` « creates an organization and notifies the parent » ; « shows validation error for an empty name ».
  - Code : composants pilotés par props (`onChanged`), sans importer `useOrganizations` ; hook `useMyInvitations` local.
  - Fichiers : `src/features/organizations/NoOrganizationScreen.tsx`, `src/features/organizations/NoOrganizationScreen.test.tsx`, `src/features/organizations/MyInvitations.tsx`, `src/features/organizations/useMyInvitations.ts`, `src/features/organizations/MyInvitations.test.tsx`, `src/features/organizations/CreateOrganizationForm.tsx`, `src/features/organizations/CreateOrganizationForm.test.tsx`.
  - Fin : vérifications frontend vertes.

- [ ] **T044** `frontend` [P ∥ T041, T042, T043, T045, T046] — Réglages de l'organisation : renommer, quitter
  - Sous-agent : `frontend-engineer` · Dépend de : T039, T040 · Couvre : volet UI de AC-35, AC-36, AC-59
  - Tests d'abord : `src/features/organizations/OrganizationSettings.test.tsx` « rename is offered only with organization:rename » ; « renames and notifies the parent » ; « leave shows translated last_owner error » ; « leave notifies the parent on success ».
  - Code : composant piloté par props (`organization: ActiveOrganization`, `onChanged`).
  - Fichiers : `src/features/organizations/OrganizationSettings.tsx`, `src/features/organizations/OrganizationSettings.test.tsx`.
  - Fin : vérifications frontend vertes.

- [ ] **T045** `frontend` [P ∥ T041, T042, T043, T044, T046] — Membres et invitations en attente
  - Sous-agent : `frontend-engineer` · Dépend de : T039, T040 · Couvre : volet UI de AC-19, AC-25, AC-26, AC-32, AC-34 (FR-44 : choix filtrés par permission, le serveur reste l'autorité)
  - Tests d'abord : `src/features/members/MembersPanel.test.tsx` « lists members with roles » ; « role choices follow admins:manage and owners:manage » ; « changes a role and removes a member » ; « shows translated error on refusal » ; « shows empty state for pending invitations » ; « revokes a pending invitation » ; `src/features/members/InviteForm.test.tsx` « invites with an allowed role » ; « shows already_member message ».
  - Fichiers : `src/features/members/useMembers.ts`, `src/features/members/MembersPanel.tsx`, `src/features/members/MembersPanel.test.tsx`, `src/features/members/InviteForm.tsx`, `src/features/members/InviteForm.test.tsx`.
  - Fin : vérifications frontend vertes.

- [ ] **T046** `frontend` [P ∥ T041, T042, T043, T044, T045] — Journal d'audit
  - Sous-agent : `frontend-engineer` · Dépend de : T039, T040 · Couvre : volet UI de AC-48
  - Tests d'abord : `src/features/audit/AuditLogPanel.test.tsx` « renders events with actor, action, target, date and outcome » ; « loads older events with before_id » ; « shows empty state » ; « shows translated error ».
  - Fichiers : `src/features/audit/useAuditLog.ts`, `src/features/audit/AuditLogPanel.tsx`, `src/features/audit/AuditLogPanel.test.tsx`.
  - Fin : vérifications frontend vertes.

- [ ] **T047** `frontend` — Coquille `App.tsx` : connexion, organisations, onglets selon les permissions
  - Sous-agent : `frontend-engineer` · Dépend de : T041, T042, T043, T044, T045, T046 · Couvre : AC-18 (écran), AC-52, AC-53, AC-54, AC-55
  - Tests d'abord, dans `src/App.test.tsx` : « shows only the login page to visitors » ; « user without organization sees the no-organization screen » ; « switching organization updates name and role-dependent screens » ; « management tabs follow permissions [member-admin] » ; « returns to login with session-ended message on 401 » ; « shows pending invitations inside the app » (FR-22) ; « tabs are keyboard reachable ».
  - Code : `AuthProvider` dans `main.tsx` ou `App.tsx` ; chargement → `LoginPage` → `NoOrganizationScreen` → coquille (sélecteur, nom de l'utilisateur, Déconnexion, onglets `Chat | Membres | Journal | Organisation` en `useState`) ; `<main key={activeOrgId}>`. L'onglet Chat garde provisoirement l'appel actuel de `ChatPanel` (branché en T048).
  - Fichiers : `src/App.tsx`, `src/App.test.tsx`, `src/main.tsx`.
  - Fin : vérifications frontend vertes.

- [ ] **T048** `frontend` — Chat dans l'organisation active, message pour les Invités
  - Sous-agent : `frontend-engineer` · Dépend de : T047 · Couvre : AC-38 (volet UI, non-régression 000 AC-5), AC-39 (volet UI)
  - Tests d'abord, dans `src/features/chat/ChatPanel.test.tsx` : test existant « streams the assistant answer into the conversation » adapté (appel avec `X-Organization-Id` et `X-CSRF-Token`) ; « guest sees no shared agent message » (sans `agents:use`, texte « Aucun agent ne vous est partagé pour l'instant », aucun appel à `fetch`) ; « 403 on agents shows the same message » ; « reuses the server thread_id ».
  - Code : `listAgents(orgId)` et `streamAgent(orgId, agent, message, threadId?, signal?)` dans `src/lib/api.ts` (passent par `request()`) ; `useChat(orgId, agent)` ; `ChatPanel` reçoit `orgId` et les permissions ; branchement dans `App.tsx` (sélecteur d'agent dans l'onglet Chat).
  - Fichiers : `src/lib/api.ts`, `src/features/chat/useChat.ts`, `src/features/chat/ChatPanel.tsx`, `src/features/chat/ChatPanel.test.tsx`, `src/App.tsx`.
  - Fin : vérifications frontend vertes ; `src/App.test.tsx` toujours vert.

- [ ] **Fin de phase 6** : `.\scripts\check.ps1` vert.

---

## Phase 7 : Environnement de développement, documentation, non-régression

- [ ] **T049** `infra` — Test d'infrastructure : base créée et migrée par la commande de lancement
  - Sous-agent : `backend-engineer` · Dépend de : T006, T048 · Couvre : AC-56 (volet automatisé, cf. R8)
  - Tests d'abord : `tests/infra/test_dev_environment.py::test_db_up_creates_and_migrates_database` (projet compose séparé `COMPOSE_PROJECT_NAME=forge-smoke`, port 5434, volume neuf : `up -d --wait db` puis `alembic upgrade head` comme `dev.ps1`, `alembic current` = `head`, rôles présents ; nettoyage du projet à la fin).
  - Code : marqueur `infra` dans `pyproject.toml` ; dans `tests/conftest.py`, saut des tests `infra` sauf si `RUN_INFRA=1` (comme `live`). Si besoin, `scripts/dev.ps1` expose l'étape « base + migrations » de façon réutilisable par le test, sans changer son comportement.
  - Fichiers : `pyproject.toml`, `tests/conftest.py`, `tests/infra/__init__.py`, `tests/infra/test_dev_environment.py`, éventuellement `scripts/dev.ps1`.
  - Fin : `$env:RUN_INFRA=1; uv run pytest -m infra` vert ; `uv run pytest` sans la variable saute ces tests ; les données du projet `forge` du développeur ne sont pas touchées.

- [ ] **T050** `infra` — `scripts/db-reset.ps1` et persistance des données locales
  - Sous-agent : `backend-engineer` · Dépend de : T049 · Couvre : AC-57
  - Tests d'abord : `tests/infra/test_dev_environment.py::test_data_persists_across_restart_and_reset_wipes_it` (projet `forge-smoke` : création d'une organisation, arrêt puis relance du conteneur → elle existe ; `db-reset.ps1 -Force` → elle n'existe plus et la base est migrée).
  - Code : `scripts/db-reset.ps1` (confirmation, `-Force` pour la sauter, `docker compose down --volumes` → `up -d --wait db` → `alembic upgrade head` ; respecte `COMPOSE_PROJECT_NAME` et `POSTGRES_PORT` de l'environnement).
  - Fichiers : `scripts/db-reset.ps1`, `tests/infra/test_dev_environment.py`.
  - Fin : `RUN_INFRA=1` vert.

- [ ] **T051** `docs` [P ∥ T052, T053] — `docs/architecture.md`
  - Sous-agent : orchestrateur · Dépend de : T050 · Couvre : définition de « terminé » (doc impactée)
  - Contenu : partie A (schéma avec `AuthProvider`, `auth/`, `db/`, `services/`, Postgres ; arborescence ; table d'API complète avec la colonne permission ; flux de connexion et de requête en organisation, en renvoyant au plan pour les diagrammes détaillés ; mémoire des conversations : `InMemorySaver` + table `conversations` + clé composée ; retrait de l'avertissement « aucune authentification ») ; partie B (D1, D2, D3 « fait (001) ») ; tableau « Ce qui va changer » (lignes 001 faites, checkpointer reporté en 003).
  - TDD : sans objet. Vérification : chaque route, table et fichier cité existe dans le code.
  - Fichiers : `docs/architecture.md`.

- [ ] **T052** `docs` [P ∥ T051, T053] — `backend/CLAUDE.md` et `frontend/CLAUDE.md`
  - Sous-agent : orchestrateur · Dépend de : T050 · Couvre : définition de « terminé »
  - Contenu backend : structure (`auth/`, `db/`, `services/`, `core/authz.py`, `core/clock.py`, `core/errors.py`, `core/security_log.py`, `migrations/`) ; règles : une transaction par requête, jamais de `SET` de session, `set_config(…, true)` seulement, horloge injectée, requêtes paramétrées uniquement, règle des tables d'organisation (même migration : `organization_id`, `ENABLE` + `FORCE`, politique `TO forge_app`, droits minimaux) et test d'introspection, liste blanche `SECURITY DEFINER`, `--loop app.core.aio:selector_loop_factory` sous Windows, commandes Alembic, base Docker requise par pytest, `tests/factories.py`, faux Google, `RUN_INFRA=1`. Contenu frontend : `AuthProvider`, `orgId` explicite à chaque appel, CSRF dans `request()`, `ApiError` + `lib/errors.ts`, tester des permissions et jamais des rôles, pas de routeur.
  - TDD : sans objet.
  - Fichiers : `backend/CLAUDE.md`, `frontend/CLAUDE.md`.

- [ ] **T053** `docs` [P ∥ T051, T052] — `README.md`, `CLAUDE.md` racine et `.env.example`
  - Sous-agent : orchestrateur · Dépend de : T050 · Couvre : AC-56 (prérequis documentés), FR-49 (commande de réinitialisation documentée)
  - Contenu : prérequis (Docker Desktop, client OAuth Google « Application Web » avec l'URI `http://localhost:5173/api/auth/callback`, utilisateurs de test de l'écran de consentement) ; commandes `docker compose up -d db`, `uv run alembic upgrade head`, `.\scripts\db-reset.ps1`, `$env:RUN_INFRA=1; uv run pytest -m infra`, uvicorn avec `--loop` ; `check.ps1` exige Docker. Relecture finale de `.env.example` : chaque champ de `Settings` et chaque variable de `compose.yaml` y figure, sans secret réel.
  - TDD : sans objet. Vérification : en partant d'un clone propre et en suivant le README seul, `dev.ps1` démarre.
  - Fichiers : `README.md`, `CLAUDE.md`, `.env.example`.

- [ ] **T054** `backend` — Non-régression de la spec 000 et vérification finale
  - Sous-agent : `backend-engineer` (liste de contrôle manuelle avec l'orchestrateur) · Dépend de : T051, T052, T053 · Couvre : AC-38, AC-56 (volet manuel), non-régression 000
  - Tests : spec 000 AC-1 `test_list_agents`, AC-2 `test_invoke_returns_answer_and_thread`, AC-3 `test_stream_emits_sse_events`, AC-4 `test_invoke_runs_custom_tool`, AC-6 `test_unknown_agent_returns_404` (backend, Membre authentifié) et AC-5 `ChatPanel.test.tsx` : tous présents et verts. `.\scripts\check.ps1` complet ; `RUN_INFRA=1` complet.
  - Contrôle manuel (pour `review.md`, R8) : `.\scripts\dev.ps1` sur un poste avec `.env` rempli ; connexion Google réelle ; création d'une organisation ; chat en flux ; `uv run langgraph dev` démarre toujours (graphes de `studio.py` inchangés).
  - Fichiers : aucun (sauf correctif), Journal de `tasks.md`.
  - Fin : tout est vert, le résultat est noté dans le Journal, la spec peut passer en `review`.

- [ ] **Fin de phase 7** : `.\scripts\check.ps1` vert de bout en bout, et `RUN_INFRA=1` vert.

---

## Chemin critique

```
T001 → T002 → T003 → T004 → T005 → T011 → T012 → T015 → T016 → T019 → T020 → T021 → T022
     → T025 → T026 → T027 → T028 → T029 → T030 → T031 → T032 → T033 → T034 → T035
     → T036 → T037 → T039 → T041…T046 (une branche) → T047 → T048 → T049 → T050 → T051…T053 → T054
```

Hors chemin critique : T006 (pendant T004 et T005), T007 à T010 (en parallèle, avant T011 et T012 ; T007 précède T011), T013 et T014 (après T012), T017 et T018 (pendant T015 et T016), T023 et T024 (après T022), T038 (pendant T037), T040 (pendant T039), et cinq des six tâches T041 à T046.

La phase 4 est entièrement séquentielle : ses tâches partagent `app/main.py`, `tests/factories.py`, `app/services/memberships.py` et les fichiers de tests de membres et d'invitations.

## Vérification des [P]

Deux tâches sont `[P]` entre elles seulement si aucune ne dépend de l'autre **et** si leurs fichiers sont disjoints.

| Groupe | Tâches | Fichiers (disjoints) | Fichiers sensibles vérifiés |
|---|---|---|---|
| Phase 1 | T006 ∥ T004, T005 | T006 : `scripts/dev.ps1` · T004 / T005 : `migrations/versions/0002_…`, `tests/db/*` | T004 et T005 partagent la migration 0002 : **séquentiels entre eux** |
| Phase 2 | T007 ∥ T008 ∥ T009 ∥ T010 | T007 : `core/config.py`, `.env.example`, `tests/test_config.py` · T008 : `core/clock.py`, `tests/fakes/__init__.py`, `tests/fakes/clock.py`, `tests/test_clock.py` · T009 : `core/errors.py`, `schemas/errors.py`, `tests/test_errors.py` · T010 : `core/authz.py`, `schemas/common.py`, `tests/test_authz.py` | aucun ne touche `main.py`, `conftest.py`, `pyproject.toml` |
| Phase 2 | T013 ∥ T014 | T013 : `tests/conftest.py`, `tests/test_no_network.py` · T014 : `tests/api/__init__.py`, `tests/api/test_route_protection.py` | seul T013 touche `conftest.py` |
| Phase 3 | T015 ∥ T017 | T015 : `pyproject.toml`, `uv.lock`, `app/auth/{__init__,provider,google}.py`, `tests/fakes/google.py`, `tests/auth/{__init__,test_google}.py` · T017 : `core/security_log.py`, `tests/test_security_log.py` | seul T015 touche `pyproject.toml` ; T017 n'installe pas le filtre dans `main.py` (fait en T024) |
| Phase 3 | T016 ∥ T017 ∥ T018 | T016 : `app/auth/google.py`, `tests/fakes/google.py`, `tests/auth/test_google.py` · T018 : `app/auth/sessions.py`, `tests/auth/test_sessions_service.py` | T018 dépend de T015 (paquets `app/auth/` et `tests/auth/` créés) et utilise la fixture `request_db_factory` de T011 sans toucher `conftest.py` |
| Phase 3 | T023 ∥ T024 | T023 : `tests/api/test_route_protection.py`, `tests/auth/test_login_paths.py` · T024 : `app/main.py`, `tests/test_security_log.py` | seul T024 touche `main.py` |
| Phase 5 | T037 ∥ T038 | T037 : `schemas/agents.py`, `services/conversations.py`, `routes/agents.py`, `tests/api/test_conversations.py` · T038 : `tests/api/test_permission_matrix.py` | T038 n'utilise que `GET /agents` et n'ajoute rien à `tests/factories.py` ni à `conftest.py` |
| Phase 6 | T039 ∥ T040 | T039 : `src/lib/api.ts`, `src/lib/api.test.ts` · T040 : `src/lib/errors.ts`, `src/lib/errors.test.ts` | `errors.ts` ne dépend pas d'`api.ts` |
| Phase 6 | T041 ∥ T042 ∥ T043 ∥ T044 ∥ T045 ∥ T046 | `features/auth/*` · `features/organizations/{useOrganizations,OrganizationSwitcher}*` · `features/organizations/{NoOrganizationScreen,MyInvitations,useMyInvitations,CreateOrganizationForm}*` · `features/organizations/OrganizationSettings*` · `features/members/*` · `features/audit/*` | aucun ne touche `lib/api.ts`, `App.tsx` ni un fichier d'index commun ; composants pilotés par props. Si une fonction manque dans `api.ts`, s'arrêter : c'est un trou de T039 |
| Phase 7 | T051 ∥ T052 ∥ T053 | `docs/architecture.md` · `backend/CLAUDE.md`, `frontend/CLAUDE.md` · `README.md`, `CLAUDE.md`, `.env.example` | fichiers disjoints |

Non `[P]`, par exemple : T019 et T024 (`app/main.py`) ; T003, T011, T012, T013, T019, T026, T036 et T049 (`tests/conftest.py`) ; T001, T003, T015 et T049 (`pyproject.toml`) ; T039 et T048 (`src/lib/api.ts`) ; T047 et T048 (`src/App.tsx`).

## Couverture des critères d'acceptation

| AC | Tâche(s) | Test principal |
|---|---|---|
| AC-1 | T019 | `tests/auth/test_login.py::test_login_succeeds_for_workspace_and_gmail_accounts` |
| AC-2 | T016, T020 | `tests/auth/test_login.py::test_login_refused_when_email_not_verified` |
| AC-3 | T018, T019 | `tests/auth/test_login.py::test_relogin_with_changed_email_keeps_same_account` |
| AC-4 | T015, T019 | `tests/auth/test_login.py::test_authorization_request_asks_only_basic_identity` |
| AC-5 | T016, T020 | `tests/auth/test_login.py::test_callback_rejected[…]` |
| AC-6 | T019 | `tests/auth/test_login.py::test_login_response_has_no_google_token_and_hardened_cookie` |
| AC-7 | T023 | `tests/auth/test_login_paths.py::test_session_creation_only_from_google_callback` |
| AC-8 | T007, T018, T021 | `tests/auth/test_sessions.py::test_session_idle_timeout` |
| AC-9 | T007, T018, T021 | `tests/auth/test_sessions.py::test_session_absolute_timeout_despite_activity` |
| AC-10 | T018, T022 | `tests/auth/test_sessions.py::test_logout_revokes_server_side_session` |
| AC-11 | T018, T020 | `tests/auth/test_login.py::test_pre_login_session_id_never_authenticated` |
| AC-12 | T022, T039 | `tests/auth/test_csrf.py::test_every_mutating_route_requires_csrf` ; `src/lib/api.test.ts` |
| AC-13 | T016, T021 | `tests/auth/test_sessions.py::test_authorized_request_works_when_identity_provider_down` |
| AC-14 | T027 | `tests/api/test_organizations.py::test_create_organization_makes_creator_sole_owner` |
| AC-15 | T027 | `tests/api/test_organizations.py::test_switch_active_organization_applies_new_role` |
| AC-16 | T026 | `tests/api/test_organizations.py::test_cannot_activate_organization_without_membership` |
| AC-17 | T005, T027 | `tests/api/test_organizations.py::test_list_my_organizations_shows_only_memberships` |
| AC-18 | T036, T043, T047 | `tests/api/test_organizations.py::test_user_without_organization_cannot_use_agents` ; `NoOrganizationScreen.test.tsx` |
| AC-19 | T032 | `tests/api/test_invitations.py::test_admin_invites_member` |
| AC-20 | T010, T032 | `tests/api/test_invitations.py::test_admin_cannot_invite_admin_or_owner` |
| AC-21 | T033 | `tests/api/test_invitations.py::test_invitee_accepts_with_case_insensitive_email` |
| AC-22 | T033 | `tests/api/test_invitations.py::test_other_email_cannot_accept_invitation` |
| AC-23 | T033 | `tests/api/test_invitations.py::test_invitee_declines_invitation` |
| AC-24 | T007, T033 | `tests/api/test_invitations.py::test_expired_or_revoked_invitation_cannot_be_accepted` |
| AC-25 | T032 | `tests/api/test_invitations.py::test_admin_cannot_revoke_admin_invitation` |
| AC-26 | T032 | `tests/api/test_invitations.py::test_inviting_existing_member_is_refused` |
| AC-27 | T038 | `tests/api/test_permission_matrix.py::test_role_permission_matrix` |
| AC-28 | T010, T028 | `tests/test_authz.py::test_changing_role_definition_changes_behavior` |
| AC-29 | T014, T023, T036 | `tests/api/test_route_protection.py::test_every_route_declares_permission_or_exemption` |
| AC-30 | T023 | `tests/api/test_route_protection.py::test_anonymous_request_to_protected_route_is_401` |
| AC-31 | T036 | `tests/api/test_members.py::test_demotion_to_guest_takes_effect_on_next_request` |
| AC-32 | T031 | `tests/api/test_members.py::test_removed_member_loses_access_keeps_other_memberships` |
| AC-33 | T029, T031 | `tests/api/test_members.py::test_admin_cannot_modify_admin_or_owner` ; `::test_admin_cannot_remove_admin_or_owner` |
| AC-34 | T029 | `tests/api/test_members.py::test_owner_promotes_then_demotes_admin` |
| AC-35 | T028, T029, T030, T031 | `tests/api/test_members.py::test_last_owner_cannot_leave` / `_be_demoted` / `_be_removed` ; `tests/services/test_memberships.py::test_concurrent_owner_demotions_keep_one_owner` |
| AC-36 | T028 | `tests/api/test_members.py::test_member_leaves_organization` |
| AC-37 | T010, T029 | `tests/api/test_members.py::test_no_self_elevation` |
| AC-38 | T036, T037, T048, T054 | `tests/test_api.py` (5 tests adaptés) ; `ChatPanel.test.tsx` |
| AC-39 | T036, T048 | `tests/api/test_agents_access.py::test_guest_cannot_list_or_invoke_agents` ; `ChatPanel.test.tsx` « guest sees no shared agent message » |
| AC-40 | T004, T037 | `tests/api/test_conversations.py::test_other_user_cannot_continue_conversation` |
| AC-41 | T037 | `tests/api/test_conversations.py::test_conversation_not_reachable_from_other_organization` |
| AC-42 | T004 | `tests/db/test_isolation.py::test_unfiltered_select_sees_only_active_org` |
| AC-43 | T004 | `tests/db/test_isolation.py::test_unfiltered_update_delete_cannot_touch_other_org` |
| AC-44 | T004 | `tests/db/test_isolation.py::test_insert_or_move_row_into_other_org_is_rejected` |
| AC-45 | T002, T004, T011 | `tests/db/test_isolation.py::test_no_context_returns_no_rows` ; `tests/db/test_db_roles.py::test_app_role_cannot_bypass_rls` ; `tests/db/test_request_db.py::test_context_does_not_leak_to_next_transaction_on_pooled_connection` |
| AC-46 | T002, T005, T010 | `tests/test_authz.py::test_no_permission_spans_organizations` ; `tests/db/test_db_roles.py::test_security_definer_functions_are_whitelisted` ; `::test_no_role_bypasses_rls` |
| AC-47 | T035 | `tests/api/test_cross_org_ids.py::test_foreign_ids_look_like_missing_ids` |
| AC-48 | T025, T034, T046 | `tests/api/test_audit.py::test_audit_log_records_sequence_without_other_org` |
| AC-49 | T034 | `tests/api/test_audit.py::test_member_and_guest_cannot_read_audit` |
| AC-50 | T004, T034 | `tests/db/test_db_roles.py::test_audit_table_is_append_only_for_app` ; `tests/api/test_route_protection.py::test_no_route_mutates_audit` |
| AC-51 | T017, T024 | `tests/test_security_log.py::test_login_logout_events_logged_without_secrets` |
| AC-52 | T041, T047 | `src/App.test.tsx` « shows only the login page to visitors » |
| AC-53 | T042, T047 | `src/App.test.tsx` « switching organization updates name and role-dependent screens » |
| AC-54 | T047 | `src/App.test.tsx` « management tabs follow permissions » |
| AC-55 | T039, T041, T047 | `src/App.test.tsx` « returns to login with session-ended message on 401 » ; `src/lib/api.test.ts` |
| AC-56 | T006, T049, T053, T054 | `tests/infra/test_dev_environment.py::test_db_up_creates_and_migrates_database` (`RUN_INFRA=1`) + contrôle manuel (R8) |
| AC-57 | T050 | `tests/infra/test_dev_environment.py::test_data_persists_across_restart_and_reset_wipes_it` |
| AC-58 | T002, T004, T013 | `scripts/check.ps1` ; `tests/test_no_network.py::test_outbound_network_is_blocked` ; `tests/db/test_db_roles.py::test_test_database_is_postgres_with_forced_rls` |
| AC-59 | T028 | `tests/api/test_organizations.py::test_owner_renames_organization` ; `::test_non_owner_cannot_rename` |
| AC-60 | T033 | `tests/api/test_invitations.py::test_invitation_sends_no_email_and_appears_at_next_login` ; `::test_same_domain_without_invitation_gets_no_membership` |
| AC-61 | T033 | `tests/api/test_members.py::test_owner_invites_second_owner` |
| AC-62 | T029 | `tests/api/test_members.py::test_ownership_transfer_promote_then_leave` |
| AC-63 | T029 | `tests/api/test_members.py::test_owner_demotes_coowner_then_cannot_demote_self_as_last_owner` |

Contrôle : les 63 critères (AC-1 à AC-63) ont chacun au moins une tâche et un test nommé. Aucun n'est « (live) ».

## Remarques sur le plan

Points trouvés en découpant. Le plan n'a pas été modifié.

- **P1 (ordre de AC-29 et AC-30)** : le découpage indicatif met le contrôle des routes en phase 2, alors que les routes d'agents ne peuvent être protégées qu'en phase 5 (`require` a besoin des sessions et du contexte d'organisation). `tasks.md` introduit donc, dans le seul fichier de test, une liste `NOT_YET_PROTECTED` qui doit valoir **exactement** les trois routes d'agents (T014) et qui est vidée en T036. Toute nouvelle route oubliée casse le test dès la phase 2.
- **P2 (`dev.ps1` en phase 1)** : à partir de T012, l'application refuse la boucle Proactor, et à partir de la phase 3 elle a besoin des tables. Sans `--loop` et sans `alembic upgrade head`, `dev.ps1` serait cassé de la phase 2 à la phase 7. La mise à jour du script passe donc en T006. `db-reset.ps1` et les tests `RUN_INFRA=1` restent en phase 7.
- **P3 (AC-18, AC-27, AC-31 en phase 5)** : ces trois critères mentionnent l'utilisation d'un agent (AC-18 et AC-31) ou la ligne « Utiliser les agents intégrés » de la matrice (AC-27). Ils sont testés après la protection des routes d'agents (T036, T038).
- **P4 (tests async sous Windows)** : le plan règle la boucle pour uvicorn et `TestClient`, mais pas pour `pytest-asyncio`, qui utilise la boucle Proactor par défaut sous Windows. Or `RequestDb`, les services de sessions et d'audit, et le test de concurrence (T030) sont async. La pointe T001 vérifie le mécanisme (point 5) et T011 l'installe dans `conftest.py`.
- **P5 (`uvicorn --loop`)** : vérifié dans uvicorn 0.54 : une valeur `module:callable` est importée et utilisée **directement** comme fabrique de boucle, appelée sans argument. `selector_loop_factory` doit donc être un callable sans paramètre qui renvoie une boucle (pas une fonction qui prend `use_subprocess`). La pointe le confirme à l'exécution, y compris avec `--reload`.
- **P6 (données de test sous `FORCE RLS`)** : avec `FORCE ROW LEVEL SECURITY` et des politiques `TO forge_app` uniquement, `forge_owner` ne peut ni lire ni insérer dans les tables d'organisation (aucune politique ne s'applique à lui, donc refus par défaut). Seul `TRUNCATE` lui reste possible. Les fixtures (T004, T026) insèrent donc en `forge_app` sous contexte. Conséquence pour les specs suivantes, hors périmètre ici : une migration de **données** sur une table d'organisation ne pourra pas se faire en `forge_owner` sans politique dédiée.
- **P7 (module de l'upsert des utilisateurs)** : le plan ne nomme pas le module qui crée ou met à jour `users` par `google_sub`. **Décision de l'utilisateur (2026-11-24)** : module séparé `app/auth/users.py` (T018), pour qu'un module ait une seule responsabilité : l'identité d'un côté, les sessions de l'autre.
- **P8 (fichiers de test hors de la liste du plan)** : `tests/test_aio.py`, `tests/test_config.py`, `tests/test_clock.py`, `tests/test_errors.py`, `tests/test_app_factory.py`, `tests/fakes/clock.py`, `tests/db/test_request_db.py`, `tests/db/test_self_reader_functions.py`, `tests/auth/test_sessions_service.py`, `tests/services/test_audit.py`, `src/lib/errors.test.ts` et les tests de composants (`MyInvitations`, `CreateOrganizationForm`, `OrganizationSettings`, `MembersPanel`, `InviteForm`, `AuditLogPanel`, `useOrganizations`, `AuthProvider`, `LoginPage`), ainsi que `src/features/organizations/useMyInvitations.ts` et `CreateOrganizationForm.tsx`. Ils découlent du TDD par tâche et restent dans les dossiers prévus par le plan.
- **P9 (réglages à l'import)** : `app/main.py` crée `app = create_app()` au niveau du module. Si les nouveaux champs de `Settings` étaient obligatoires, un `.env` ancien (sans `DATABASE_URL`) casserait tout import, y compris celui des tests. T007 les garde optionnels, avec une erreur claire au démarrage ou à l'usage.
- **P10 (interface inutilisable entre T036 et T048)** : dès que les routes d'agents exigent une session et une organisation (T036), le frontend actuel ne peut plus discuter avec l'agent tant que la phase 6 n'est pas terminée. `check.ps1` reste vert (les tests frontend simulent `fetch`), mais `dev.ps1` ne permet pas de chatter pendant cette fenêtre.
- **P11 (AC-12 paramétré)** : le test qui parcourt `app.routes` suppose que le contrôle CSRF, porté par une dépendance, s'exécute avant la validation du corps (422) et avant la recherche de la ressource. C'est le cas si le CSRF est vérifié dans la dépendance de session. Le test envoie quand même un corps JSON valide minimal et des uuid aléatoires dans le chemin, pour ne pas dépendre de cet ordre.

## Journal

Notes d'implémentation, écarts au plan, décisions prises en cours de route (avec date).

- 2026-11-24 : découpage validé par l'utilisateur (P1 à P11 acceptées ; P7 tranchée : `app/auth/users.py`).
- *(T001 : résultats de la pointe technique à consigner ici : versions, tag d'image retenu, chemin du volume, mécanisme de boucle pour pytest-asyncio.)*
