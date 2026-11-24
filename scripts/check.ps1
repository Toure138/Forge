# Runs every quality gate. Exit code != 0 if anything fails.
# Usage (from repo root):  .\scripts\check.ps1   or   powershell -File scripts/check.ps1
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
$failed = @()

function Invoke-Step($name, $dir, [scriptblock]$cmd) {
    Write-Host "`n=== $name ===" -ForegroundColor Cyan
    Push-Location (Join-Path $root $dir)
    & $cmd
    if ($LASTEXITCODE -ne 0) { $script:failed += $name }
    Pop-Location
}

Invoke-Step 'backend: ruff format' 'backend' { uv run ruff format --check . }
Invoke-Step 'backend: ruff check'  'backend' { uv run ruff check . }
Invoke-Step 'backend: mypy'        'backend' { uv run mypy }
Invoke-Step 'backend: pytest'      'backend' { uv run pytest }
Invoke-Step 'frontend: lint'       'frontend' { pnpm lint }
Invoke-Step 'frontend: typecheck'  'frontend' { pnpm typecheck }
Invoke-Step 'frontend: test'       'frontend' { pnpm test }

if ($failed.Count -gt 0) {
    Write-Host "`nFAILED: $($failed -join ', ')" -ForegroundColor Red
    exit 1
}
Write-Host "`nALL CHECKS PASSED" -ForegroundColor Green
