@echo off
setlocal
cd /d "%~dp0"

rem Byte-code cache -> .venv\pycache
set "PYTHONPYCACHEPREFIX=%~dp0.venv\pycache"

set "PYTHONW=%~dp0.venv\Scripts\pythonw.exe"

if not exist "%PYTHONW%" (
    echo [ERROR] Virtual env not found: %PYTHONW%
    echo         Setup: python -m venv .venv ^&^& .venv\Scripts\pip install -r requirements.txt
    pause
    exit /b 1
)

if not exist "%~dp0src\archive_gui.py" (
    echo [ERROR] src\archive_gui.py not found
    pause
    exit /b 1
)

start "" "%PYTHONW%" "%~dp0src\archive_gui.py"
exit /b 0
