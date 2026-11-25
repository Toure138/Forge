# Forge

Plateforme de développement d'agents IA multi-organisations : **FastAPI + LangGraph + deepagents** et **PostgreSQL (RLS)** côté backend, **React + Vite + TypeScript** côté frontend. Le projet est piloté en **Spec-Driven Development** avec Claude Code.

## Prérequis

- **Docker Desktop**, lancé. La base Postgres tourne dans un conteneur, et `check.ps1` comme `pytest` en ont besoin.
- **uv** (Python 3.12) et **pnpm** (Node 22+).
- **Un client OAuth Google**, pour se connecter. Dans la [console Google Cloud](https://console.cloud.google.com/apis/credentials) :
  1. créer un identifiant **« ID client OAuth » de type « Application Web »** ;
  2. déclarer l'**URI de redirection autorisée** `http://localhost:5173/api/auth/callback` ;
  3. sur l'**écran de consentement OAuth**, en mode « Test », ajouter les adresses Google qui pourront se connecter, 100 au maximum ;
  4. reporter l'ID et le secret dans `.env` (`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`).

  Seule l'identité de base est demandée (`openid email profile`), sans aucun accès à Drive. Sans client OAuth, l'application démarre, mais la page de connexion indique que la connexion est indisponible.

## Démarrage

```powershell
Copy-Item .env.example .env        # puis remplir .env (voir ci-dessous)
cd backend;  uv sync;      cd ..
cd frontend; pnpm install; cd ..
.\scripts\dev.ps1                  # base Docker + migrations + API :8000 + interface :5173
```

Dans `.env` :
- remplace les trois mots de passe `change-me-…` par des valeurs générées, par exemple `python -c "import secrets; print(secrets.token_urlsafe(24))"`, et reporte-les dans les quatre URL de base ;
- `GOOGLE_CLIENT_ID` et `GOOGLE_CLIENT_SECRET` (voir les prérequis) ;
- `ANTHROPIC_API_KEY`, pour discuter avec l'agent.

Ouvre ensuite **http://localhost:5173**, pas `127.0.0.1` : l'URI de redirection Google et le cookie de session dépendent de cette origine. Connecte-toi, crée une organisation, puis invite d'autres comptes Google. Forge n'envoie aucun email : préviens les personnes toi-même. L'invitation leur apparaît quand elles se connectent avec l'adresse invitée.

## Commandes utiles

```powershell
.\scripts\check.ps1                # TOUTES les vérifications (démarre la base, puis ruff, mypy, pytest, oxlint, tsc, vitest)
.\scripts\dev.ps1 -DbOnly          # base + migrations seulement, sans ouvrir l'API ni l'interface
.\scripts\db-reset.ps1             # DÉTRUIT et recrée la base locale : affiche projet, volume et port,
                                   # puis demande de taper le nom du projet (forge) ; -Force pour sauter la question

# Backend (dans backend/)
docker compose up -d db            # base seule (depuis la racine du projet)
uv run alembic upgrade head        # appliquer les migrations
uv run uvicorn app.main:app --reload --port 8000 --loop app.core.aio:selector_loop_factory
uv run pytest                      # tests unitaires et d'API (modèle factice, faux Google, base Docker)
$env:RUN_INFRA=1; uv run pytest -m infra   # tests d'infrastructure (~1 min, projet compose forge-smoke sur le port 5434, hors check.ps1)
$env:RUN_LIVE=1;  uv run pytest -m live    # tests contre le vrai LLM (payants)
uv run langgraph dev               # LangGraph Studio : visualiser et déboguer les agents
```

Sous Windows, l'option `--loop app.core.aio:selector_loop_factory` est **obligatoire** : le mode async de psycopg ne fonctionne pas avec la boucle par défaut de Windows. Sans elle, l'API refuse de démarrer avec un message explicite. `dev.ps1` la passe déjà.

## Travailler avec Claude (SDD)

```
/spec-new  Un agent qui résume une page web donnée par URL
   ↳ tu réponds aux [À CLARIFIER] et tu valides la spec
/spec-plan 002
   ↳ tu valides le plan (architecture, contrat API, tests)
/spec-tasks 002
   ↳ tu valides le découpage
/spec-implement 002            # phase par phase ; ou T003, phase-2, all
/spec-verify 002               # revue des critères d'acceptation → review.md
/spec-status                   # avancement de toutes les specs
```

Sous-agents (`.claude/agents/`) : `spec-writer`, `architect`, `agent-engineer`, `backend-engineer`, `frontend-engineer`, `reviewer`.

Documents : [CLAUDE.md](CLAUDE.md) · [constitution](docs/constitution.md) · [architecture](docs/architecture.md) · [ADR](docs/adr/) · [specs](specs/).
