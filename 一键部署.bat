@echo off
rem ================================================================
rem  Folo archive - one-click setup
rem
rem    <this file>          setup  (installs "openai" only)
rem    <this file> help     show this text
rem
rem  What it does:
rem    1. check Python and Node.js
rem    2. create or repair the .venv
rem       (a .venv copied from another machine never works - it is
rem        detected and rebuilt automatically)
rem    3. install Python dependencies (via a mirror first)
rem    4. verify the CLI loads
rem    5. check config.json and the Folo login state
rem    6. optionally start the web UI
rem ================================================================
setlocal
cd /d "%~dp0"

if /i "%~1"=="help" goto :usage
if /i "%~1"=="-h"   goto :usage
if /i "%~1"=="/?"   goto :usage

echo ================================================================
echo   Folo archive - setup
echo   Folder: %~dp0
echo ================================================================
echo.

rem ------------------------------------------------- 1. Python
set "PYCMD="
python --version >nul 2>&1
if not errorlevel 1 set "PYCMD=python"
if not defined PYCMD (
    py -3 --version >nul 2>&1
    if not errorlevel 1 set "PYCMD=py -3"
)
if not defined PYCMD goto :err_python

echo [1/6] Python ......... ok
%PYCMD% --version

rem ------------------------------------------------- 2. Node.js
where node >nul 2>&1
if errorlevel 1 goto :err_node
where npx  >nul 2>&1
if errorlevel 1 goto :err_node
echo.
echo [2/6] Node.js ........ ok
node --version

rem ------------------------------------------------- 3. venv
echo.
echo [3/6] Virtual env ...
set "VPY=%~dp0.venv\Scripts\python.exe"

if exist "%VPY%" (
    "%VPY%" -c "import sys" >nul 2>&1
    if errorlevel 1 (
        echo       existing .venv is NOT usable - rebuilding
        rmdir /s /q "%~dp0.venv"
    )
)

if not exist "%VPY%" (
    echo       creating .venv ...
    %PYCMD% -m venv "%~dp0.venv"
    if errorlevel 1 goto :err_venv
)
echo       ok

rem ------------------------------------------------- 4. dependencies
echo.
echo [4/6] Installing dependencies ^(openai only^) ...
"%VPY%" -m pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple
"%VPY%" -m pip install openai -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 "%VPY%" -m pip install openai
"%VPY%" -c "import openai" >nul 2>&1
if errorlevel 1 goto :err_pip
echo       ok

rem ------------------------------------------------- 5. self check
echo.
echo [5/6] Checking the CLI ...
"%VPY%" "%~dp0src\archive.py" --list-steps >nul 2>&1
if errorlevel 1 goto :err_selfcheck
echo       ok

rem tkinter is needed by the desktop window (src\gui.py); the web UI does not
rem need it. It ships with the python.org installer unless "tcl/tk" was unticked.
"%VPY%" -c "import tkinter" >nul 2>&1
if errorlevel 1 (
    echo       [WARN] tkinter is missing - the desktop window cannot start.
    echo              Re-run the Python installer -^> Modify -^> tick
    echo              "tcl/tk and IDLE", then run this script again.
    echo              The web UI still works.
) else (
    echo       tkinter ^(desktop window^) ... ok
)

rem ------------------------------------------------- 6. config / login
echo.
echo [6/6] Configuration ...
if exist "%~dp0src\config.json" (
    echo       src\config.json ......................... found
) else (
    echo       [WARN] src\config.json is missing.
    echo              Copy src\config.example.json, fill in api_key / base_url / model,
    echo              and add the "mail" section if you want failure e-mails.
)
if exist "%USERPROFILE%\.folo\config.json" (
    echo       Folo login ^(%USERPROFILE%\.folo\config.json^) ... found
) else (
    echo       [WARN] No Folo login for this account.
    echo              Run: npx --yes folocli@latest login
    echo              or copy .folo\config.json from a machine that is already signed in.
)

rem ------------------------------------------------- summary
echo.
echo ================================================================
echo   Setup finished.
echo ================================================================
echo.
echo   Scripts in this folder:
for %%F in ("%~dp0*.bat") do echo     - %%~nxF
echo.
echo   Hints:
echo     * desktop launcher  : opens the small window (archive / open page / quit)
echo     * web launcher      : console mode, opens the browser UI and stays open
echo.
echo   Note: keep every .bat in this folder PURE ASCII - cmd.exe reads
echo         them in the OEM code page, so UTF-8 Chinese text would break them.
echo.
echo   Reminder: api.folo.is may need a proxy on this network.
echo             Verify with:  npx --yes folocli@latest whoami
echo.

set /p "STARTNOW=Start the desktop window now? [Y/N]: "
if /i "%STARTNOW%"=="Y" (
    echo Starting the desktop window ...
    start "" ".venv\Scripts\pythonw.exe" "src\gui.py"
)

echo.
echo Done.
pause
exit /b 0

:usage
echo Usage:
echo   %~nx0          setup (openai only)
echo   %~nx0 help     this text
pause
exit /b 0

:err_python
echo.
echo [ERROR] Python not found.
echo         Install Python 3.10+ from https://www.python.org/downloads/windows/
echo         and tick "Add python.exe to PATH" during setup.
echo.
pause
exit /b 1

:err_node
echo.
echo [ERROR] Node.js / npx not found.
echo         Install Node.js 18+ from https://nodejs.org/
echo         The project calls "npx folocli" to talk to Folo.
echo.
pause
exit /b 1

:err_venv
echo.
echo [ERROR] Failed to create the virtual environment.
echo         Try: %PYCMD% -m venv "%~dp0.venv"
echo.
pause
exit /b 1

:err_pip
echo.
echo [ERROR] Dependency installation failed (openai could not be imported).
echo         Check your network, then retry this script.
echo.
pause
exit /b 1

:err_selfcheck
echo.
echo [ERROR] "src\archive.py --list-steps" failed.
echo         The environment is incomplete - see the message above.
echo.
pause
exit /b 1
