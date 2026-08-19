@echo off
setlocal
set SCRIPT_DIR=%~dp0
"%SCRIPT_DIR%backend\.venv\Scripts\python.exe" -m crawler.ingest %*
endlocal
