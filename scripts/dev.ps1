# Starts the dev environment: Postgres (Docker) + migrations, then the backend (:8000) and the
# frontend (:5173) in two new windows.
# Usage (from repo root):  .\scripts\dev.ps1
#   -DbOnly  stop after the database and migrations steps (no windows): used by db-reset.ps1
#            and by the infra tests (backend/tests/infra).
# Needs: Docker Desktop running, and a .env at the repo root (copy .env.example).
param([switch]$DbOnly)

$root = Split-Path -Parent $PSScriptRoot

function Stop-Dev([string]$message) {
    Write-Host $message -ForegroundColor Red
    exit 1
}

# 1. Prerequisites, with a clear message instead of a stack trace.
if (-not (Test-Path (Join-Path $root '.env'))) {
    Stop-Dev "No .env at the repo root: copy .env.example to .env and fill it in (see README)."
}
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Stop-Dev 'Docker is not installed: install Docker Desktop, start it, then run this script again.'
}
docker info --format '{{.ServerVersion}}' 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Stop-Dev 'Docker is not running: start Docker Desktop, wait until it is ready, then run this script again.'
}

# 2. Database: start Postgres and wait until it is healthy (data persists in the named volume).
Write-Host 'Starting the database (docker compose up -d --wait db)...' -ForegroundColor Cyan
Push-Location $root
docker compose up -d --wait db
$dbExit = $LASTEXITCODE
Pop-Location
if ($dbExit -ne 0) {
    Stop-Dev "The database did not start: see 'docker compose logs db' (is port 5433 or POSTGRES_PORT already in use?)."
}

# 3. Migrations (as forge_owner, DATABASE_OWNER_URL from .env).
Write-Host 'Applying migrations (alembic upgrade head)...' -ForegroundColor Cyan
Push-Location (Join-Path $root 'backend')
uv run alembic upgrade head
$migrateExit = $LASTEXITCODE
Pop-Location
if ($migrateExit -ne 0) {
    Stop-Dev 'Migrations failed: check DATABASE_OWNER_URL in .env and the output above.'
}
if ($DbOnly) {
    Write-Host 'Database ready and migrated.' -ForegroundColor Green
    exit 0
}

# 4. Backend and frontend. psycopg's async mode needs a selector event loop on Windows.
$backend = 'uv run uvicorn app.main:app --reload --port 8000 --loop app.core.aio:selector_loop_factory'
Start-Process powershell -ArgumentList '-NoExit', '-Command', "cd '$root\backend'; $backend"
Start-Process powershell -ArgumentList '-NoExit', '-Command', "cd '$root\frontend'; pnpm dev"
Write-Host 'Backend  : http://localhost:8000/docs'
Write-Host 'Frontend : http://localhost:5173'
