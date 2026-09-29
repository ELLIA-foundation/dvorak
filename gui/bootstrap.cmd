@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>&1
if %ERRORLEVEL%==0 (
    set "PYTHON=py"
) else (
    where python >nul 2>&1
    if %ERRORLEVEL%==0 (
        set "PYTHON=python"
    ) else (
        echo Python 3.12+ was not found on PATH.
        exit /b 1
    )
)

if not exist ".venv\Scripts\python.exe" (
    %PYTHON% -m venv .venv
    if errorlevel 1 exit /b 1
)

".venv\Scripts\python.exe" -m pip install --upgrade pip
if errorlevel 1 exit /b 1
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 exit /b 1

echo Ready. Launch with:
echo   gui\run.cmd
