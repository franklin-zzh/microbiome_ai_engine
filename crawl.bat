@echo off
setlocal
set SCRIPT_DIR=%~dp0
"%SCRIPT_DIR%crawler\.venv\Scripts\python.exe" -m crawler %*
endlocal
