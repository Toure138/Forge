# Architecture

Ce document a deux parties :

- **Partie A, ce qui existe aujourd'hui** : le socle des specs 000 et 001. C'est la référence pour le code actuel.
- **Partie B, l'architecture cible** : la plateforme RAG multi-organisations décidée dans l'[ADR-0002](adr/0002-architecture-rag-multi-organisations.md). Elle se construit spec par spec.

À chaque spec terminée, ce qu'elle a construit passe de la partie B à la partie A. Si une ligne n'est pas dans la partie A, elle n'existe pas encore dans le code.

---

# Partie A : ce qui existe aujourd'hui

État après la spec 001 (organisations, connexion Google, rôles, isolation). Décisions : [ADR-0002](adr/0002-architecture-rag-multi-organisations.md) D1 à D3, [ADR-0003](adr/0003-persistance-et-contexte-de-securite.md). Les diagrammes détaillés (connexion, requête dans une organisation, modèle de données) sont dans le [plan de la spec 001](../specs/001-organisations-auth-rbac/plan.md).

```
┌──────────────────────── frontend (Vite :5173) ────────────────────────┐
│ App.tsx ─ AuthProvider ─ LoginPage | NoOrganizationScreen | zone org   │
│   zone org (key = organisation active) : OrganizationSwitcher,         │
│   onglets Chat · Membres · Journal · Organisation (selon permissions)  │
│ lib/api.ts : request() + cookie + X-CSRF-Token + X-Organization-Id     │
└───────────────────────────────┬───────────────────────────────────────┘
                                │ /api/*  (proxy Vite : même origine)
┌───────────────────────────────▼────────────── backend (FastAPI :8000) ┐
│ api/routes/*  ── schemas/* (contrat)                                    │
│   │  dépendances : CurrentSessionDep (cookie → session → CSRF)          │
│   │                require("perm") (organisation active + adhésion)     │
│   │                RequestDbDep (une transaction, contexte RLS posé)    │
│ auth/ (OIDC Google, sessions)   services/ (organisations, membres,      │
│ core/authz.py (rôles → perms)            invitations, audit, convers.)  │
│ agents/registry.py ── AgentRegistry (InMemorySaver) ── create_deep_agent│
│ core/llm.py get_chat_model()   core/security_log.py (journal plateforme)│
└──────────┬───────────────────────────────────────────┬─────────────────┘
           │ forge_app (sans BYPASSRLS)                │ httpx
┌──────────▼──── PostgreSQL 18 + pgvector (Docker) ─┐  ┌▼──── Google ─────┐
│ globales : users · sessions · login_requests      │  │ OIDC : code +    │
│ RLS forcé : organizations · memberships ·         │  │ PKCE, identité   │
│   invitations · conversations · audit_events      │  │ seule (pas Drive)│
│ SECURITY DEFINER (forge_self_reader) : app_my_*   │  └──────────────────┘
└───────────────────────────────────────────────────┘
```

## Arborescence

| Chemin | Rôle |
|---|---|
| `compose.yaml`, `infra/postgres/initdb/` | Postgres 18 + pgvector en local ; création des rôles `forge_owner`, `forge_app`, `forge_self_reader` et des bases `forge`, `forge_test` |
| `backend/migrations/` | Alembic : tables (0001), RLS forcé, droits minimaux, fonctions `SECURITY DEFINER` (0002) |
| `backend/app/core/` | configuration, fabrique de modèle LLM, `authz.py` (rôles → permissions), horloge, erreurs, journal de sécurité, boucle selector |
| `backend/app/auth/` | fournisseur d'identité Google (OIDC), demandes de connexion, sessions, utilisateurs, dépendance de session |
| `backend/app/db/` | moteur, tables (SQLAlchemy Core), `RequestDb` (transaction et contexte RLS par requête) |
| `backend/app/services/` | logique métier : organisations, adhésions, invitations, audit, conversations |
| `backend/app/agents/<nom>/` | un agent = `graph.py` (NAME, DESCRIPTION, `build`), `prompts.py`, `tools.py` |
| `backend/app/agents/registry.py` | liste des agents exposés par l'API |
| `backend/app/agents/studio.py` | points d'entrée pour LangGraph Studio (`langgraph.json`) |
| `backend/app/api/routes/` | routes FastAPI, montées sous `/api` |
| `backend/app/schemas/` | modèles Pydantic des requêtes et réponses (contrat API) |
| `backend/tests/` | tests avec modèle factice, faux Google et vraie base `forge_test` ; `tests/infra/` (`RUN_INFRA=1`) ; `tests/live/` (`RUN_LIVE=1`) |
| `frontend/src/lib/` | client API typé, messages d'erreur, rôles (affichage), parseur SSE |
| `frontend/src/features/<feature>/` | `auth`, `organizations`, `members`, `audit`, `chat` : composants, hooks et tests |
| `scripts/` | `dev.ps1` (base + migrations + API + interface), `check.ps1` (toutes les vérifications), `db-reset.ps1` (réinitialiser la base locale) |
| `specs/` · `docs/adr/` | specs SDD · Architecture Decision Records |

## API

Toutes les routes sont sous `/api`. **Refus par défaut** : une route sans permission déclarée ni exemption fait échouer les tests. Exemptions : `health`, `auth_login`, `auth_callback`.

- **Session** : cookie `forge_session` (`HttpOnly`, `Secure` hors poste local, `SameSite=Lax`), exigé partout sauf exemptions, sinon 401 `not_authenticated`.
- **CSRF** : en-tête `X-CSRF-Token` (jeton de `GET /auth/session`) sur toute méthode non sûre, sinon 403 `csrf_failed`.
- **Organisation active** : en-tête `X-Organization-Id` sur les routes marquées « Org ». L'adhésion est revérifiée à chaque requête. Une organisation dont on n'est pas membre répond comme une organisation inexistante (404).
- **Erreurs** : `{code, message}` (422 de validation au format FastAPI). Une ressource d'une autre organisation répond exactement comme une ressource inexistante (FR-37).

| Méthode | Chemin | Permission | Org | Description |
|---|---|---|:-:|---|
| GET | `/health` | exemptée | | `{"status":"ok"}` |
| GET | `/auth/login` | exemptée | | redirige vers Google (code + PKCE + state + nonce) |
| GET | `/auth/callback` | exemptée | | valide la réponse Google, crée la session, redirige vers l'interface |
| GET | `/auth/session` | `session:read` | | utilisateur courant + jeton CSRF |
| POST | `/auth/logout` | `session:logout` | | révoque la session côté serveur (204) |
| GET | `/organizations` | `organization:list` | | mes organisations et mon rôle dans chacune |
| POST | `/organizations` | `organization:create` | | crée une organisation, le créateur en est Propriétaire |
| GET | `/invitations/mine` | `invitation:respond` | | invitations en attente adressées à mon email |
| POST | `/invitations/{id}/accept` | `invitation:respond` | | accepte (adresse vérifiée identique, usage unique, 7 jours) |
| POST | `/invitations/{id}/decline` | `invitation:respond` | | refuse |
| GET | `/organization` | `organization:read` | ✓ | organisation active, mon rôle et mes permissions |
| PATCH | `/organization` | `organization:rename` | ✓ | renommer |
| POST | `/organization/leave` | `organization:leave` | ✓ | quitter (refusé au dernier Propriétaire : 409 `last_owner`) |
| GET | `/organization/members` | `members:read` | ✓ | membres et rôles |
| PATCH | `/organization/members/{user_id}` | `members:manage` + droit de gérer le rôle source et le rôle cible | ✓ | changer un rôle |
| DELETE | `/organization/members/{user_id}` | `members:manage` + droit de gérer le rôle de la cible | ✓ | retirer un membre |
| GET | `/organization/invitations` | `members:read` | ✓ | invitations en attente |
| POST | `/organization/invitations` | `members:manage` + droit d'attribuer le rôle | ✓ | inviter une adresse email avec un rôle |
| DELETE | `/organization/invitations/{id}` | `members:manage` | ✓ | révoquer une invitation |
| GET | `/organization/audit` | `audit:read` | ✓ | journal d'audit, lecture seule, paginé (`before_id`, `limit`) |
| GET | `/agents` | `agents:use` | ✓ | `[{name, description}]` |
| POST | `/agents/{name}/invoke` | `agents:use` | ✓ | `{message, thread_id?}` → `{thread_id, output}` |
| POST | `/agents/{name}/stream` | `agents:use` | ✓ | `{message, thread_id?}` → flux SSE |

La matrice rôle → permissions est dans `backend/app/core/authz.py`, seul endroit où les rôles sont nommés. Un Invité n'a pas `agents:use` : le partage d'agents arrive en spec 008.

### Protocole SSE (`/agents/{name}/stream`)

Tous les refus (401, 403, 404 agent, organisation ou conversation, 422) arrivent en JSON **avant** l'ouverture du flux. Une fois le flux ouvert :

| event | data | Quand |
|---|---|---|
| `metadata` | `{"thread_id": uuid}` | premier événement |
| `token` | `{"text": str}` | fragment de réponse de l'agent principal |
| `tool` | `{"name": str, "status": "start"\|"end"}` | appel d'outil (y compris `task` pour les sous-agents) |
| `error` | `{"message": str}` | exception pendant l'exécution : `message` est générique (`"The agent encountered an error."`), jamais le texte de l'exception, qui est journalisé côté serveur (`forge.agents`) ; l'interface affiche son propre message |
| `done` | `{}` | dernier événement |

Les tokens produits par les sous-agents (namespaces imbriqués) ne sont pas diffusés. Aucune connexion à la base n'est tenue pendant le flux.

`POST /agents/{name}/invoke` suit le même schéma : conversation contrôlée ou créée et transaction validée (connexion rendue au pool) **avant** l'appel au modèle. Une exception de l'agent y répond 500 `{"code": "agent_error", "message": "The agent encountered an error."}`, journalisée comme pour le flux.

## Flux principaux

- **Connexion** : `GET /auth/login` crée une demande de connexion à usage unique (cookie `forge_login`, 10 min) et redirige vers Google. Google renvoie sur `/auth/callback`. Le serveur consomme la demande, compare le `state`, échange le code (PKCE) et valide l'ID token (signature, audience, émetteur, expiration selon l'horloge, nonce, email vérifié). Il crée alors une **nouvelle** session, hachée en base, révoque celle que présentait le navigateur, et redirige vers l'interface. Le navigateur ne reçoit jamais de jeton Google.
- **Requête dans une organisation** : cookie → session valide (24 h d'inactivité, 7 jours au maximum) → CSRF si écriture → `set_user` → en-tête d'organisation → `set_organization` → adhésion relue (filtre explicite + RLS) → permission → logique métier dans la même transaction. Un refus de permission est journalisé dans une transaction séparée, puis la requête répond 403.
- **Interface** : `GET /auth/session` (jeton CSRF gardé en mémoire) → `GET /organizations` → organisation active de l'onglet → zone d'organisation remontée à chaque changement d'organisation.

## Isolation des organisations

- Toute table métier porte `organization_id` et a un RLS **forcé** (`FORCE ROW LEVEL SECURITY`), avec une politique `TO forge_app`. Sans organisation posée, une requête ne voit aucune ligne.
- Le code filtre **aussi** explicitement par organisation : deux barrières indépendantes (constitution X).
- Les lectures « à moi » qui traversent les organisations (mes organisations, mes invitations) passent par trois fonctions `SECURITY DEFINER` possédées par `forge_self_reader`, lui-même soumis à des politiques limitées à l'utilisateur courant.
- Des tests de fuite couvrent chaque table (`backend/tests/db/test_isolation.py`), et un test d'introspection refuse toute table d'organisation non protégée.

## Mémoire des conversations

Une conversation appartient à un utilisateur dans une organisation (table `conversations`, RLS organisation + propriétaire). Son `thread_id` est un UUID créé par le serveur et renvoyé dans `metadata`. Un `thread_id` inconnu ou appartenant à quelqu'un d'autre répond 404. Le contenu est dans le checkpointer `InMemorySaver`, sous la clé `organisation/utilisateur/conversation` : il est perdu au redémarrage (persistance prévue en spec 003).

## Journaux

- **Audit d'organisation** (table `audit_events`, append-only pour `forge_app`) : création, renommage, invitations, changements de rôle, retraits, départs, refus de permission. Lisible par les Propriétaires et les Admins.
- **Journal plateforme** (logger `forge.security`) : connexions réussies ou refusées, déconnexions, sessions rejetées, tentatives sur une organisation dont on n'est pas membre. Aucun secret : champs fermés, paramètres SQL masqués, query string du callback masquée dans le journal d'accès.
- **Erreurs d'agent** (logger `forge.agents`, niveau ERROR) : l'exception avec sa trace, l'agent, le mode (`invoke` ou `stream`), l'organisation et l'utilisateur (identifiants Forge). Jamais le message de l'utilisateur. Le client ne reçoit qu'un message générique (constitution VIII).

## Ajouter un agent (socle actuel)

1. Créer `backend/app/agents/<nom>/` avec `graph.py`, `prompts.py` et `tools.py` (copier `assistant/`).
2. L'enregistrer dans `AGENTS` (`registry.py`) et dans `studio.py` et `langgraph.json`.
3. Écrire les tests : outils (purs) et API avec `member_client(*replies)` (un Membre authentifié, modèle factice).
4. Il apparaît automatiquement dans le sélecteur du frontend, pour les rôles qui ont `agents:use`.

Cette méthode concerne les **types** d'agents écrits en code. À partir de la spec 008, les agents créés par les utilisateurs seront de la **configuration** en base (voir ADR-0002, D12).

---

# Partie B : architecture cible

Le schéma complet et les flux « question » et « ingestion » sont dans la [section 2 de l'ADR-0002](adr/0002-architecture-rag-multi-organisations.md#2-vue-densemble). Ils ne sont pas recopiés ici pour qu'il n'y ait qu'une seule version de référence.

## Composants et état d'avancement

| Composant | Décision | Spec | État |
|---|---|---|---|
| Postgres partagé, `organization_id` + RLS | D1 | 001 | **fait** |
| Connexion Google (OIDC), session en cookie httpOnly, CSRF | D2 | 001 | **fait** |
| RBAC d'organisation (Propriétaire, Admin, Membre, Invité), `require(permission)` | D3 | 001 | **fait** |
| Passerelle modèles : Claude, Gemini, OpenAI, local | D11 | 002 | à faire |
| Bases de connaissances, RBAC par base, dépôt sécurisé | D3, D15 | 003 | à faire |
| Stockage des originaux (dossier `data/` en local, Cloud Storage en cible) | D5, D14 | 003 | à faire |
| Worker d'ingestion et file de tâches Postgres | D6 | 003 | à faire |
| Lecture des documents (Docling + OCR) | D7 | 003 | à faire |
| Embeddings bge-m3 locaux, pgvector | D5, D8 | 003 | à faire |
| RAG v1 avec citations | D9, D10 | 003 | à faire |
| Évaluations, tests de fuite et d'injection | D13 | 004 | à faire |
| Connecteur Drive, synchro quotidienne, intersection des droits | D4, D6 | 005 | à faire |
| Recherche hybride, reranking, contextualisation | D8, D9 | 006 | à faire |
| Agent RAG (graphe fixe) et Agent Analyste (deepagents) | D10 | 007 | à faire |
| Agents en configuration, partage, accès partiel, audit | D12 | 008 | à faire |
| Accès de support de l'opérateur, suppression d'organisation | D3 | à définir | à faire |
| Déploiement GCP | D14 | 009 | à faire |

## Ce qui va changer dans le socle actuel

Ce tableau dit ce qui sera remplacé dans l'existant, et quand, pour que personne ne s'appuie sur un choix qui va disparaître.

| Aujourd'hui | Demain | Spec |
|---|---|---|
| ~~Routes `/api/agents/*` ouvertes à tous~~ | Toute route exige une session et une permission (refus par défaut) | 001 : **fait** |
| ~~Aucune base de données~~ | Postgres + pgvector dans Docker en local | 001 : **fait** |
| Checkpointer `InMemorySaver`, perdu au redémarrage (la propriété des conversations est en base depuis la 001) | Checkpointer Postgres | 003 |
| Un seul modèle, fixé par `LLM_MODEL` | Modèle choisi par agent dans un catalogue, selon les règles de l'organisation et la confidentialité de la base | 002 |
| `AGENTS` : dictionnaire Python codé en dur | Types d'agents dans le code + agents configurés en base par les utilisateurs | 008 |
| Invité sans aucun accès aux agents | Accès aux agents partagés avec lui | 008 |

## Principes qui encadrent la cible

Les règles X, XI et XII de la [constitution](constitution.md) découlent de l'ADR-0002 : isolation des organisations, aucune élévation de privilèges, et les documents traités comme des données. Chaque `plan.md` doit expliquer comment il les respecte.
