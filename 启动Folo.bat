@echo off
rem ==============================================================
rem  Folo archive - desktop window launcher (GUI)
rem
rem  Opens a small window with three buttons:
rem     archive today / open web page / quit service
rem
rem  Runs src\gui.py with pythonw.exe, so NO console window stays
rem  open. Logs are NOT shown in the window - the web page has them.
rem
rem  Extra args are forwarded, e.g.:
rem     start-folo.bat --port 9000 --no-schedule
rem
rem  Keep this file PURE ASCII: cmd.exe reads .bat in the OEM code
rem  page, so UTF-8 Chinese text here would corrupt parsing.
rem
rem  Console (headless-friendly) alternative: start-webui.bat
rem ==============================================================
setlocal
cd /d "%~dp0"

rem Byte-code cache -> .venv\pycache (same as the other launchers)
set "PYTHONPYCACHEPREFIX=%~dp0.venv\pycache"
set "PYTHONUTF8=1"

set "PYW=%~dp0.venv\Scripts\pythonw.exe"
set "PY=%~dp0.venv\Scripts\python.exe"

if not exist "%PYW%" (
    echo [ERROR] Virtual env not found: %PYW%
    echo.
    echo   Setup:
    echo     cd /d "%~dp0"
    echo     python -m venv .venv
    echo     .venv\Scripts\pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

if not exist "%~dp0src\gui.py" (
    echo [ERROR] src\gui.py not found
    pause
    exit /b 1
)

rem tkinter on the command line gives a plain-text answer; otherwise
rem gui.py would die silently inside a message box of its own.
"%PY%" -c "import tkinter" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] This Python has no tkinter, so the desktop window cannot start.
    echo.
    echo   Fix: re-run the Python installer -^> Modify -^> tick
    echo        "tcl/tk and IDLE", then recreate the venv:
    echo          python -m venv .venv
    echo          .venv\Scripts\pip install -r requirements.txt
    echo.
    echo   Or use the plain web UI instead: start-webui.bat
    echo.
    pause
    exit /b 1
)

rem Detached start: this console closes immediately, pythonw keeps the window.
start "" "%PYW%" "%~dp0src\gui.py" %*
exit /b 0
