@echo off
setlocal
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo GUI venv is missing. Run gui\bootstrap.cmd first.
    exit /b 1
)

set "PYTHONPATH=%~dp0"
".venv\Scripts\python.exe" -m dvorak_gui %*
