# One-click dev server launcher (uses .venv automatically).
# Usage: .\dev.ps1  (or right-click -> Run with PowerShell)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$py = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "[ERROR] .venv not found. Create it first:" -ForegroundColor Red
    Write-Host "  python -m venv .venv"
    Write-Host "  .venv\Scripts\pip install -r requirements.txt"
    exit 1
}

Write-Host "[dev] Starting FastAPI dev server: http://localhost:8000 (Ctrl+C to stop)" -ForegroundColor Green
Write-Host "[dev] Docs: http://localhost:8000/docs  |  Admin: http://localhost:8000/admin" -ForegroundColor DarkGray
& $py -m uvicorn main:app --reload --host 0.0.0.0 --port 8000
