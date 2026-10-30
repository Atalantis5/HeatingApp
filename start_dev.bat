@echo off
chcp 65001 >nul
setlocal EnableExtensions

REM ==========================================================================
REM  start_dev.bat - One-click dev launcher (Windows)
REM   Window 1: activate .venv, run uvicorn main:app --reload (127.0.0.1:8000)
REM   Window 2: run npm run dev (Vite, 127.0.0.1:5173)
REM   Then opens default browser at http://127.0.0.1:5173
REM   Tip: set START_DEV_SKIP_BROWSER=1 to skip opening the browser (CI/headless)
REM ==========================================================================
cd /d "%~dp0"

echo ============================================================
echo  Gravity Heating Hydraulic Calculator - Dev Launcher
echo  Project: %CD%
echo ============================================================

if not exist ".venv\Scripts\activate.bat" (
    echo [ERROR] Missing .venv virtual environment: %CD%\.venv\Scripts\activate.bat
    echo         Create it first: python -m venv .venv
    pause
    exit /b 1
)

if not exist ".venv\Scripts\uvicorn.exe" (
    echo [ERROR] uvicorn is not installed in .venv
    echo         Install dependencies: .venv\Scripts\python.exe -m pip install -r requirements.txt
    pause
    exit /b 1
)

if not exist "node_modules\.bin\vite.cmd" (
    echo [ERROR] Missing frontend dependencies node_modules
    echo         Install them first: npm install
    pause
    exit /b 1
)

where node >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Node.js not found in PATH. Install Node.js 18+ first.
    pause
    exit /b 1
)

where npm >nul 2>nul
if errorlevel 1 (
    echo [ERROR] npm not found in PATH. Check your Node.js installation.
    pause
    exit /b 1
)

echo.
echo [1/3] Starting backend: .venv + uvicorn main:app --reload  (http://127.0.0.1:8000)
start "Heating-Backend(uvicorn)" cmd /k "call .venv\Scripts\activate.bat && uvicorn main:app --reload --host 127.0.0.1 --port 8000"

echo [2/3] Starting frontend: npm run dev  (http://127.0.0.1:5173)
start "Heating-Frontend(Vite)" cmd /k "npm run dev"

echo [3/3] Waiting for services to be ready ...
ping 127.0.0.1 -n 5 >nul

if defined START_DEV_SKIP_BROWSER (
    echo [INFO] START_DEV_SKIP_BROWSER is set, skipping browser.
) else (
    start "" "http://127.0.0.1:5173"
)

echo.
echo Done. Backend and frontend windows are running.
echo Open http://127.0.0.1:5173 in your browser.
echo Closing this window does NOT stop the two service windows.
echo.
exit /b 0
