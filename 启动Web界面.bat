@echo off
rem ==============================================================
rem  Folo archive - local web UI launcher (standard library only)
rem
rem  Starts src\webui.py in the foreground so Ctrl+C stops it.
rem  Open http://127.0.0.1:8765/ in a browser (opened automatically).
rem
rem  Extra args are forwarded, e.g.:
rem    start-webui.bat --port 9000 --no-browser
rem ==============================================================
setlocal
cd /d "%~dp0"

rem Byte-code cache -> .venv\pycache (same as the other launchers)
set "PYTHONPYCACHEPREFIX=%~dp0.venv\pycache"
set "PYTHONUTF8=1"

set "PY=%~dp0.venv\Scripts\python.exe"

if not exist "%PY%" (
    echo [ERROR] Virtual env not found: %PY%
    echo.
    echo   Setup:
    echo     cd /d "%~dp0"
    echo     python -m venv .venv
    echo     .venv\Scripts\pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

if not exist "%~dp0src\webui.py" (
    echo [ERROR] src\webui.py not found
    pause
    exit /b 1
)

echo Starting Folo web UI ... press Ctrl+C to stop.
"%PY%" "%~dp0src\webui.py" %*

echo.
echo Folo web UI stopped.
pause
endlocal
