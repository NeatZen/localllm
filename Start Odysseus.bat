@echo off

title Odysseus

cd /d "%~dp0"



if not exist "venv\Scripts\python.exe" (

    echo First-time setup required. Run install-turnkey.ps1 once, then try again.

    pause

    exit /b 1

)



for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":7000" ^| findstr "LISTENING"') do (

    echo Port 7000 is already in use. Close the other Odysseus window first, or run:

    echo   taskkill /PID %%a /F

    pause

    exit /b 1

)



echo Starting Odysseus...

echo.

timeout /t 2 /nobreak >nul

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0run-phone.ps1" -OpenBrowser



pause

