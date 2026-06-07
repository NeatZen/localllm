@echo off
title Odysseus (Phone / Network)
cd /d "%~dp0"

if not exist "venv\Scripts\python.exe" (
    echo First-time setup required. Run install-turnkey.ps1 once, then try again.
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start-phone.ps1"
pause
