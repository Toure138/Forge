# Architecture

Ce document a deux parties :

- **Partie A, ce qui existe aujourd'hui** : le socle de la spec 000. C'est la référence pour le code actuel.
- **Partie B, l'architecture cible** : la plateforme RAG multi-organisations décidée dans l'[ADR-0002](adr/0002-architecture-rag-multi-organisations.md). Elle se construit spec par spec.

À chaque spec terminée, ce qu'elle a construit passe de la partie B à la partie A. Si une ligne n'est pas dans la partie A, elle n'existe pas encore dans le code.

---

# Partie A : ce qui existe aujourd'hui

```
┌──────────────── frontend (Vite :5173) ────────────────┐
│  App.tsx ─ ChatPanel ─ useChat ─ lib/api.ts ─ lib/sse │
└──────────────────────────┬────────────────────────────┘
                           │  /api/*  (proxy Vite en dev)
┌──────────────────────────▼──── backend (FastAPI :8000) ┐
│ api/routes/*.py  ── schemas/*.py (contrat)              │
│        │                                                │
│ agents/registry.py ── AgentRegistry (cache des graphes, │
│        │                checkpointer InMemorySaver)     │
│ agents/<nom>/graph.py  build(model, checkpointer)       │
│        ├─ prompts.py   ├─ tools.py                      │
│        └─ create_deep_agent(...)  (LangGraph)           │
│ core/llm.py  get_chat_model()  ← core/config.py (.env)  │
└─────────────────────────────────────────────────────────┘
```

## Arborescence

| Chemin | Rôle |
|---|---|
| `backend/app/core/` | configuration (`Settings`) et fabrique de modèle LLM |
| `backend/app/agents/<nom>/` | un agent = `graph.py` (NAME, DESCRIPTION, `build`), `prompts.py`, `tools.py` |
| `backend/app/agents/registry.py` | liste des agents exposés par l'API |
| `backend/app/agents/studio.py` | points d'entrée pour LangGraph Studio (`langgraph.json`) |
| `backend/app/api/routes/` | routes FastAPI, montées sous `/api` |
| `backend/app/schemas/` | modèles Pydantic des requêtes et réponses |
| `backend/tests/` | tests avec modèle factice ; `tests/live/` pour les tests contre le vrai LLM |
| `frontend/src/lib/` | client API typé et parseur SSE |
| `frontend/src/features/<feature>/` | composants, hooks et tests d'une fonctionnalité |
| `specs/` | specs SDD |
| `docs/adr/` | Architecture Decision Records |

## API

| Méthode | Chemin | Description |
|---|---|---|
| GET | `/api/health` | `{"status":"ok"}` |
| GET | `/api/agents` | `[{name, description}]` |
| POST | `/api/agents/{name}/invoke` | `{message, thread_id?}` → `{thread_id, output}` |
| POST | `/api/agents/{name}/stream` | `{message, thread_id?}` → flux SSE |

> **Attention :** ces routes n'ont **aucune authentification**. C'est acceptable pour un socle local, mais c'est la première chose que la spec 001 corrige.

### Protocole SSE (`/stream`)

| event | data | Quand |
|---|---|---|
| `metadata` | `{"thread_id": str}` | premier événement |
| `token` | `{"text": str}` | fragment de réponse de l'agent principal |
| `tool` | `{"name": str, "status": "start"\|"end"}` | appel d'outil (y compris `task` pour les sous-agents) |
| `error` | `{"message": str}` | exception pendant l'exécution |
| `done` | `{}` | dernier événement |

Les tokens produits par les sous-agents (namespaces imbriqués) ne sont pas diffusés.

## Mémoire des conversations

Le `thread_id` identifie une conversation. Le checkpointer `InMemorySaver` est perdu au redémarrage.

## Ajouter un agent (socle actuel)

1. Créer `backend/app/agents/<nom>/` avec `graph.py`, `prompts.py` et `tools.py` (copier `assistant/`).
2. L'enregistrer dans `AGENTS` (`registry.py`) et dans `studio.py` et `langgraph.json`.
3. Écrire les tests : outils (purs) et API avec `fake_model`.
4. Il apparaît automatiquement dans le sélecteur du frontend.

Cette méthode concerne les **types** d'agents écrits en code. À partir de la spec 008, les agents créés par les utilisateurs seront de la **configuration** en base (voir ADR-0002, D12).

---

# Partie B : architecture cible

Le schéma complet et les flux « question » et « ingestion » sont dans la [section 2 de l'ADR-0002](adr/0002-architecture-rag-multi-organisations.md#2-vue-densemble). Ils ne sont pas recopiés ici pour qu'il n'y ait qu'une seule version de référence.

## Composants et état d'avancement

| Composant | Décision | Spec | État |
|---|---|---|---|
| Postgres partagé, `organization_id` + RLS | D1 | 001 | à faire |
| Connexion Google (OIDC), session en cookie httpOnly, CSRF | D2 | 001 | à faire |
| RBAC d'organisation (Propriétaire, Admin, Membre, Invité), `require(permission)` | D3 | 001 | à faire |
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
| Déploiement GCP | D14 | 009 | à faire |

## Ce qui va changer dans le socle actuel

Le socle de la partie A a été écrit avant l'ADR-0002, et plusieurs de ses choix sont provisoires. Ce tableau dit ce qui sera remplacé, et quand, pour que personne ne s'appuie sur un choix qui va disparaître.

| Aujourd'hui | Demain | Spec |
|---|---|---|
| Routes `/api/agents/*` ouvertes à tous | Toute route exige une session et une permission (refus par défaut) | 001 |
| Aucune base de données | Postgres + pgvector dans Docker en local | 001 |
| Checkpointer `InMemorySaver`, perdu au redémarrage | Checkpointer Postgres, conversations rattachées à un utilisateur et une organisation | 001 ou 003 (décidé dans le plan) |
| Un seul modèle, fixé par `LLM_MODEL` | Modèle choisi par agent dans un catalogue, selon les règles de l'organisation et la confidentialité de la base | 002 |
| `AGENTS` : dictionnaire Python codé en dur | Types d'agents dans le code + agents configurés en base par les utilisateurs | 008 |

## Principes qui encadrent la cible

Les règles X, XI et XII de la [constitution](constitution.md) découlent de l'ADR-0002 : isolation des organisations, aucune élévation de privilèges, et les documents traités comme des données. Chaque `plan.md` doit expliquer comment il les respecte.
