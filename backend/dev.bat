@echo off
chcp 65001 >nul
REM ============================================================
REM One-click dev server launcher (uses .venv automatically).
REM Usage: double-click this file, or run dev.bat from a console.
REM ============================================================
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv not found. Create it first:
    echo   python -m venv .venv
    echo   .venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)

echo [dev] Starting FastAPI dev server: http://localhost:8000  (Ctrl+C to stop)
echo [dev] Docs: http://localhost:8000/docs  ^|  Admin: http://localhost:8000/admin
".venv\Scripts\python.exe" -m uvicorn main:app --reload --host 0.0.0.0 --port 8000
