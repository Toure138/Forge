# ADR-0001 : Stack technique initiale

- **Statut** : accepté
- **Date** : 2026-11-23

## Contexte
On veut une plateforme pour développer, tester et exposer des agents IA, avec une interface web, et la faire évoluer en Spec-Driven Development avec Claude Code.

## Décision
- **Agents** : LangGraph (orchestration, état, checkpoints) avec deepagents (`create_deep_agent` : planification par todo, système de fichiers virtuel, sous-agents).
- **LLM** : Claude par `langchain-anthropic`, modèle par défaut `claude-opus-5-5` avec réflexion adaptative et `effort` configurable. Le provider reste interchangeable via `init_chat_model`.
- **Backend** : FastAPI avec streaming SSE (`sse-starlette`), configuration `pydantic-settings`, gestion des paquets `uv`, qualité `ruff` et `mypy`, tests `pytest`.
- **Frontend** : React 19, Vite, TypeScript, Tailwind v4, `pnpm`, `vitest` avec Testing Library, `oxlint`.
- **Dev des agents** : LangGraph Studio (`langgraph dev`).

## Alternatives considérées
| Option | Pour | Contre |
|---|---|---|
| LangGraph Platform / `langgraph-sdk` côté front | `useStream` prêt à l'emploi | dépendance à un serveur dédié, moins de contrôle sur l'API |
| WebSocket au lieu de SSE | bidirectionnel | plus complexe ; le flux est unidirectionnel ici |

## Conséquences
- L'API est la nôtre (FastAPI). Le front ne dépend pas du serveur LangGraph.
- Conversations en mémoire tant qu'aucun checkpointer persistant n'est ajouté.
