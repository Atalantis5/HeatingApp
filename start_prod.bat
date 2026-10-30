@echo off
chcp 65001 >nul
setlocal EnableExtensions

REM ==========================================================================
REM  start_prod.bat - Production launcher (single port / single window)
REM   Activate .venv, run: uvicorn main:app --host 127.0.0.1 --port 8000
REM   FastAPI serves both the built frontend (dist) and the API on port 8000.
REM   Opens default browser at http://127.0.0.1:8000 automatically.
REM   Tip: set START_PROD_SKIP_BROWSER=1 to skip the browser (CI/headless).
REM ==========================================================================
cd /d "%~dp0"

echo ============================================================
echo  Gravity Heating Hydraulic Calculator - Production Server
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

if not exist "dist\index.html" (
    echo [ERROR] Missing frontend build: dist\index.html
    echo         Build it first: npm install ^&^& npm run build
    pause
    exit /b 1
)

call .venv\Scripts\activate.bat

echo.
echo [INFO] Single-port server: http://127.0.0.1:8000  ^(frontend + API^)
echo [INFO] Browser will open automatically when the service is ready.
echo.

if defined START_PROD_SKIP_BROWSER (
    echo [INFO] START_PROD_SKIP_BROWSER is set, skipping browser auto-open.
) else (
    REM Hidden background helper: poll http://127.0.0.1:8000 until ready, then open browser
    start "" /b powershell -NoProfile -WindowStyle Hidden -Command "for($i=0;$i -lt 60;$i++){try{$r=Invoke-WebRequest -UseBasicParsing 'http://127.0.0.1:8000/' -TimeoutSec 1; if($r.StatusCode -eq 200){Start-Process 'http://127.0.0.1:8000'; break}}catch{}; Start-Sleep -Milliseconds 500}"
)

uvicorn main:app --host 127.0.0.1 --port 8000

echo.
echo [INFO] Server stopped. Press any key to close this window.
pause
exit /b 0
