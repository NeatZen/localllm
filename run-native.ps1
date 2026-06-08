# Run NeatAi natively on Windows (no Docker).
# Turnkey path: run install-turnkey.ps1 once first.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".\venv\Scripts\python.exe")) {
    Write-Host "Run install-turnkey.ps1 first (one-time setup)." -ForegroundColor Yellow
    exit 1
}

if (-not (Test-Path ".\data\auth.json")) {
    .\venv\Scripts\python.exe setup.py
}

Write-Host "Starting NeatAi at http://127.0.0.1:7000"
Write-Host "Built-in local AI starts automatically on first boot (no Ollama needed)."
Write-Host "Stop Docker NeatAi first if port 7000 is busy: docker compose down"
.\venv\Scripts\uvicorn.exe app:app --host 127.0.0.1 --port 7000
