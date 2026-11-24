# Forge

Environnement de développement d'agents IA : **FastAPI + LangGraph + deepagents** côté backend, **React + Vite + TypeScript** côté frontend, piloté en **Spec-Driven Development** avec Claude Code.

## Démarrage

```powershell
Copy-Item .env.example .env        # puis renseigner ANTHROPIC_API_KEY
cd backend;  uv sync;      cd ..
cd frontend; pnpm install; cd ..
.\scripts\dev.ps1                  # http://localhost:5173  (API : http://localhost:8000/docs)
.\scripts\check.ps1                # lint + types + tests
```

LangGraph Studio, pour visualiser et déboguer les agents : `cd backend; uv run langgraph dev`.

## Travailler avec Claude (SDD)

```
/spec-new  Un agent qui résume une page web donnée par URL
   ↳ tu réponds aux [À CLARIFIER] et tu valides la spec
/spec-plan 001
   ↳ tu valides le plan (architecture, contrat API, tests)
/spec-tasks 001
   ↳ tu valides le découpage
/spec-implement 001            # phase par phase ; ou T003, phase-2, all
/spec-verify 001               # revue des critères d'acceptation → review.md
/spec-status                   # avancement de toutes les specs
```

Sous-agents (`.claude/agents/`) : `spec-writer`, `architect`, `agent-engineer`, `backend-engineer`, `frontend-engineer`, `reviewer`.

Documents : [CLAUDE.md](CLAUDE.md) · [constitution](docs/constitution.md) · [architecture](docs/architecture.md) · [ADR](docs/adr/) · [specs](specs/).
