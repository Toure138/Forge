# Resets the local database: DELETES the Postgres volume of the compose project (all local data),
# then recreates the database and applies the migrations (dev.ps1 -DbOnly).
# Usage (from repo root):  .\scripts\db-reset.ps1           shows what it deletes, asks to confirm
#                          .\scripts\db-reset.ps1 -Force    no confirmation
# Target: the compose project of the current environment, i.e. `forge` (your dev data) unless
# COMPOSE_PROJECT_NAME / POSTGRES_PORT say otherwise (the infra tests use forge-smoke on 5434).
# Migrations go to DATABASE_OWNER_URL (environment, then .env): it must use the same port.
param([switch]$Force)

$root = Split-Path -Parent $PSScriptRoot

function Stop-Reset([string]$message) {
    Write-Host $message -ForegroundColor Red
    exit 1
}

# 1. Prerequisites, before asking anything.
if (-not (Test-Path (Join-Path $root '.env'))) {
    Stop-Reset "No .env at the repo root: copy .env.example to .env and fill it in (see README)."
}
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    Stop-Reset 'Docker is not installed: install Docker Desktop, start it, then run this script again.'
}
docker info --format '{{.ServerVersion}}' 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Stop-Reset 'Docker is not running: start Docker Desktop, wait until it is ready, then run this script again.'
}

# 2. What will be destroyed, exactly as docker compose resolves it.
Push-Location $root
$configJson = docker compose config --format json
$configExit = $LASTEXITCODE
Pop-Location
if ($configExit -ne 0) {
    Stop-Reset 'docker compose config failed: see the output above (is .env complete?).'
}
$config = ($configJson -join "`n") | ConvertFrom-Json
$project = $config.name
$volume = $config.volumes.pgdata.name
$port = $config.services.db.ports[0].published

Write-Host "This DELETES all the local data of the compose project '$project':" -ForegroundColor Yellow
Write-Host "  project : $project"
Write-Host "  volume  : $volume"
Write-Host "  port    : 127.0.0.1:$port"
Write-Host 'Then it recreates an empty database and applies the migrations.'

if (-not $Force) {
    $answer = Read-Host "Type the project name ($project) to confirm"
    if ($answer -ne $project) {
        Write-Host "Cancelled: nothing was deleted (volume $volume kept)." -ForegroundColor Cyan
        exit 1
    }
}

# 3. Delete the container and its volume.
Write-Host "Deleting project '$project' and volume $volume (docker compose down --volumes)..." -ForegroundColor Cyan
Push-Location $root
docker compose down --volumes
$downExit = $LASTEXITCODE
Pop-Location
if ($downExit -ne 0) {
    Stop-Reset "docker compose down --volumes failed: see the output above (volume $volume may still exist)."
}

# 4. Recreate and migrate, with the same steps as the launch command.
& (Join-Path $PSScriptRoot 'dev.ps1') -DbOnly
if ($LASTEXITCODE -ne 0) {
    Stop-Reset "The data was deleted but the database could not be recreated: fix the error above, then run .\scripts\dev.ps1."
}
Write-Host "Database reset: project '$project', volume $volume recreated and migrated." -ForegroundColor Green
