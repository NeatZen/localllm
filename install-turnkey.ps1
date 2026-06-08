# NeatAi turnkey installer — local AI included, no Docker or Ollama required.
# Right-click -> Run with PowerShell (or run from an ordinary terminal).

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host ""
Write-Host "=== NeatAi Turnkey Setup ===" -ForegroundColor Cyan
Write-Host "This installs NeatAi plus a built-in local AI (Qwen2.5 3B, ~2 GB download)."
Write-Host ""

$py312 = $null
foreach ($c in @(
    { (py -3.12 -c "import sys; print(sys.executable)" 2>$null).Trim() },
    "$env:LOCALAPPDATA\Python\pythoncore-3.12-64\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
)) {
    if ($c -is [scriptblock]) { try { $py312 = & $c } catch {} }
    elseif ($c -and (Test-Path $c)) { $py312 = $c }
    if ($py312 -and (Test-Path $py312)) { break }
    $py312 = $null
}
if (-not $py312) {
    Write-Host "Python 3.12 not found. Install from https://www.python.org/downloads/" -ForegroundColor Red
    exit 1
}
Write-Host "Using Python: $py312"

if (-not (Test-Path ".\venv\Scripts\python.exe")) {
    Write-Host "Creating Python 3.12 environment..."
    & $py312 -m venv venv
}
$venvVer = (.\venv\Scripts\python.exe -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')").Trim()
if ($venvVer -ne "3.12") {
    Write-Host "Recreating venv (found Python $venvVer, need 3.12)..."
    if (Test-Path ".\venv") { Rename-Item ".\venv" "venv-old-$(Get-Date -Format 'yyyyMMdd-HHmmss')" }
    & $py312 -m venv venv
}

Write-Host "Installing dependencies (this may take a few minutes)..."
.\venv\Scripts\pip.exe install --upgrade pip
.\venv\Scripts\pip.exe install -r requirements.txt
.\venv\Scripts\pip.exe install -r requirements-bundled.txt --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cpu

if (-not (Test-Path ".\data\auth.json")) {
    Write-Host "First-time database setup..."
    .\venv\Scripts\python.exe setup.py
}

Write-Host ""
Write-Host "Downloading built-in AI model (~2 GB, one-time only)..."
.\venv\Scripts\python.exe scripts\download_default_model.py
if ($LASTEXITCODE -ne 0) {
    Write-Host "Model download failed. Check your internet connection and try again." -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "=== Setup complete ===" -ForegroundColor Green
Write-Host "Double-click 'Start NeatAi.bat' or run: .\run-native.ps1"
Write-Host ""
