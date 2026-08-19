@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0package_backend.ps1"
if %ERRORLEVEL% neq 0 (
    echo.
    echo [ERROR] Packaging failed with error code %ERRORLEVEL%
)
pause
