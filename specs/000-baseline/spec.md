---
id: "000"
title: Socle — agent assistant exposé en chat streaming
status: done
created: 2026-11-23
owner: utilisateur
---

# Socle : agent assistant exposé en chat streaming

> Spec rétroactive qui documente l'état initial du dépôt. Elle sert d'exemple de format.

## Contexte et objectif
Disposer d'un socle fonctionnel de bout en bout (UI → API → agent → LLM) sur lequel ajouter des agents et des fonctionnalités en SDD.

## Utilisateurs et scénarios
- **US-1** : En tant que développeur, je veux discuter avec un agent depuis le navigateur et voir sa réponse s'afficher au fil de l'eau.
- **US-2** : En tant que développeur, je veux continuer une conversation (contexte conservé).
- **US-3** : En tant que développeur, je veux choisir l'agent parmi ceux enregistrés.

## Exigences fonctionnelles
- **FR-1** : L'API DOIT lister les agents disponibles.
- **FR-2** : L'API DOIT exécuter un agent en mode réponse complète et en mode streaming SSE.
- **FR-3** : Une conversation DOIT être identifiée par un `thread_id` renvoyé au client.
- **FR-4** : L'agent `assistant` DOIT disposer d'un outil `get_current_time`.

## Critères d'acceptation
- **AC-1** (FR-1) : **quand** on appelle `GET /api/agents`, **alors** la liste contient `assistant`. → `tests/test_api.py::test_list_agents`
- **AC-2** (FR-2) : **quand** on invoque l'agent, **alors** on reçoit sa réponse et un `thread_id`. → `test_invoke_returns_answer_and_thread`
- **AC-3** (FR-2) : **quand** on streame, **alors** on reçoit `metadata`, puis des `token`, puis `done`. → `test_stream_emits_sse_events`
- **AC-4** (FR-4) : **quand** le modèle appelle `get_current_time`, **alors** l'outil s'exécute et l'agent termine. → `test_invoke_runs_custom_tool`
- **AC-5** (US-1) : **quand** l'utilisateur envoie un message dans l'UI, **alors** la réponse streamée s'affiche. → `frontend/src/features/chat/ChatPanel.test.tsx`
- **AC-6** : **étant donné** un agent inconnu, **alors** l'API renvoie 404. → `test_unknown_agent_returns_404`

## Hors périmètre
Authentification, persistance des conversations, historique côté UI, déploiement.
