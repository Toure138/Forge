# Forge — instructions pour Claude

Plateforme de développement d'agents IA : **backend** Python (FastAPI + LangGraph + deepagents) et **frontend** React (Vite + TypeScript + Tailwind).

L'utilisateur pilote, Claude exécute. On travaille en **Spec-Driven Development (SDD)** : aucune fonctionnalité n'est codée sans une spec validée.

## Documents de référence (à lire avant toute modification non triviale)

- [docs/constitution.md](docs/constitution.md) : principes non négociables. En cas de conflit, ils l'emportent.
- [docs/architecture.md](docs/architecture.md) : vue d'ensemble et flux de données.
- [docs/adr/](docs/adr/) : décisions d'architecture déjà prises. Ne pas les remettre en cause sans un nouvel ADR.
- [specs/](specs/) : une spec par fonctionnalité (`NNN-slug/spec.md`, `plan.md`, `tasks.md`).
- [backend/CLAUDE.md](backend/CLAUDE.md) et [frontend/CLAUDE.md](frontend/CLAUDE.md) : conventions par couche.

## Workflow SDD

| Étape | Commande | Produit | Sous-agent | Statut après |
|---|---|---|---|---|
| 1. Spécifier | `/spec-new <idée>` | `specs/NNN-slug/spec.md` | `spec-writer` | `draft` |
| 2. Planifier | `/spec-plan NNN` | `plan.md` (+ ADR si besoin) | `architect` | `planned` |
| 3. Découper | `/spec-tasks NNN` | `tasks.md` | `architect` | `ready` |
| 4. Implémenter | `/spec-implement NNN [T00x\|phase\|all]` | code + tests, cases cochées | `backend-engineer`, `agent-engineer`, `frontend-engineer` | `in-progress` |
| 5. Vérifier | `/spec-verify NNN` | `review.md` | `reviewer` | `done` ou retour en 4 |
| Suivi | `/spec-status` | tableau d'avancement | — | — |

Règles de passage :

1. **Porte de validation humaine entre chaque étape.** À la fin d'une étape, résume ce qui a été produit, liste les questions ouvertes, puis **arrête-toi**. Ne passe à l'étape suivante que sur instruction explicite de l'utilisateur.
2. La spec décrit le **quoi et le pourquoi**, jamais le comment. Le comment va dans `plan.md`.
3. Toute ambiguïté devient un marqueur `[À CLARIFIER: question]` dans la spec. On ne devine pas. Une spec qui contient encore des marqueurs ne peut pas passer en `planned`.
4. Si l'implémentation révèle que la spec ou le plan est faux, **arrête-toi** et propose la mise à jour du document. Ne dérive pas en silence.
5. Le statut est porté par le frontmatter de `spec.md` (`status:`). Mets-le à jour à chaque étape.

**Changement rapide (hors SDD)** : un correctif de bug évident, une faute de frappe, un renommage, ou une modification de moins de 30 lignes sans changement de comportement visible. Fais-le directement, avec un test si c'est un bug. En cas de doute, demande s'il faut une spec.

## Commandes

```powershell
.\scripts\dev.ps1                 # base Docker + migrations + backend :8000 + frontend :5173
.\scripts\check.ps1               # TOUTES les vérifications (exige Docker ; à lancer avant de dire « fini »)
.\scripts\db-reset.ps1            # détruit et recrée la base locale (confirmation)
docker compose up -d db           # base Postgres seule

# Backend (dans backend/)
uv run uvicorn app.main:app --reload --port 8000 --loop app.core.aio:selector_loop_factory   # --loop obligatoire sous Windows
uv run alembic upgrade head       # migrations
uv run pytest                     # tests unitaires et d'API (modèle factice, faux Google, base Docker, gratuits)
$env:RUN_INFRA=1; uv run pytest -m infra # tests d'infrastructure (Docker réel, projet séparé)
$env:RUN_LIVE=1; uv run pytest -m live   # tests contre le vrai LLM (payants)
uv run ruff format . ; uv run ruff check --fix . ; uv run mypy
uv run langgraph dev              # LangGraph Studio : visualiser et déboguer les graphes
uv add <pkg>                      # jamais pip install

# Frontend (dans frontend/)
pnpm dev | pnpm test | pnpm lint | pnpm build
pnpm add <pkg>                    # jamais npm ou yarn
```

## Définition de « terminé »

Une tâche est terminée seulement si :

- `.\scripts\check.ps1` passe (ruff, mypy, pytest, oxlint, tsc, vitest) ;
- chaque critère d'acceptation couvert a au moins un test qui le prouve ;
- la case est cochée dans `tasks.md` ;
- la doc impactée est à jour (`architecture.md`, ADR, CLAUDE.md de la couche).

Ne déclare jamais une tâche terminée sans avoir lancé les vérifications. Si l'une échoue, dis-le avec la sortie.

## Conventions transverses

- **Langue** : specs, docs et échanges en français. Code, identifiants, commentaires et messages de commit en anglais.
- **Commits** : Conventional Commits avec la référence de la spec, par exemple `feat(agents): add web search tool [spec-003]`. Un commit par tâche ou par phase. Ne commite que sur demande de l'utilisateur.
- **Branches** : `feat/NNN-slug` par spec.
- **Contrat API** : les schémas Pydantic (`backend/app/schemas/`) font foi. Les types TS (`frontend/src/lib/api.ts`) les reflètent. Toute modification de l'un impose la mise à jour de l'autre dans la même tâche.
- **Secrets** : uniquement dans `.env`, qui n'est pas versionné. Ajoute toute nouvelle variable à `.env.example`.
- **Sécurité multi-organisations** (constitution X à XII, ADR-0003) : toute route déclare une permission (`require("perm")`), jamais un test de rôle ; toute table d'organisation porte `organization_id` avec RLS forcé dans la même migration ; une ressource d'une autre organisation répond comme une ressource inexistante. Détails dans [backend/CLAUDE.md](backend/CLAUDE.md).
- **LLM** : le modèle se construit toujours via `app.core.llm.get_chat_model()`. Le modèle par défaut est `anthropic:claude-opus-5-5`, configurable par `LLM_MODEL` et `LLM_EFFORT`. Avec Claude Opus 5.5, ne passe pas `temperature`, `budget_tokens`, `thinking: disabled` ni un `tool_choice` forcé (`any` ou `tool`) : l'API les rejette en 400. Pour une sortie structurée, utilise `response_format` ou des outils en `auto`.
