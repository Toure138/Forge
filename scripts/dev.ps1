# Starts backend (:8000) and frontend (:5173) in two new windows.
$root = Split-Path -Parent $PSScriptRoot
Start-Process powershell -ArgumentList '-NoExit', '-Command', "cd '$root\backend'; uv run uvicorn app.main:app --reload --port 8000"
Start-Process powershell -ArgumentList '-NoExit', '-Command', "cd '$root\frontend'; pnpm dev"
Write-Host 'Backend  : http://localhost:8000/docs'
Write-Host 'Frontend : http://localhost:5173'
