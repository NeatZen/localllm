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
echo Open http://localhost:7000 in your browser when ready.
echo.
echo Note: ChromaDB warnings are normal without Docker. Built-in AI uses port 11435.
echo.

REM CUDA runtime DLLs for GPU llama-cpp-python (CUDA 13+ uses bin\x64).
set "CUDA_ROOT="
for /f "delims=" %%i in ('dir /b /ad /o-n "C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v*" 2^>nul') do (
    if not defined CUDA_ROOT set "CUDA_ROOT=C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\%%i"
)
if defined CUDA_ROOT (
    if exist "%CUDA_ROOT%\bin\x64" set "PATH=%CUDA_ROOT%\bin\x64;%PATH%"
    if exist "%CUDA_ROOT%\bin" set "PATH=%CUDA_ROOT%\bin;%PATH%"
    set "CUDA_PATH=%CUDA_ROOT%"
)

timeout /t 3 /nobreak >nul
start "" "http://localhost:7000"
venv\Scripts\uvicorn.exe app:app --host 127.0.0.1 --port 7000

pause
