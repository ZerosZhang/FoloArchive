@echo off
rem ================================================================
rem  Folo archive - resident scheduler
rem
rem  Runs in the background and archives once a day at the configured
rem  time. Does NOT use the Windows Task Scheduler.
rem
rem    double-click / no arg   start it (minimized window)
rem    stop                    stop it
rem    status                  show status + last log lines
rem
rem  Log file : result\scheduler.log
rem  Time     : config.json -> schedule { "enabled": true, "time": "08:00" }
rem ================================================================
setlocal
cd /d "%~dp0"

set "TITLE=FoloScheduler"
set "PY=%~dp0.venv\Scripts\python.exe"
set "LOG=%~dp0result\scheduler.log"

if /i "%~1"=="stop"   goto :stop
if /i "%~1"=="status" goto :status

rem ---------------------------------------------------------- start
if not exist "%PY%" (
    echo [ERROR] Virtual env not found: %PY%
    echo         Run the one-click setup script first.
    pause
    exit /b 1
)
echo Starting the resident scheduler (minimized) ...
start "%TITLE%" /min "%PY%" "%~dp0src\scheduler.py"
ping -n 3 127.0.0.1 >nul
echo.
echo   Log    : %LOG%
echo   Stop   : "%~nx0" stop
echo   Status : "%~nx0" status
echo.
exit /b 0

rem ---------------------------------------------------------- stop
:stop
echo Stopping the resident scheduler ...
taskkill /FI "WINDOWTITLE eq %TITLE%*" /T /F
echo.
echo Log tail:
if exist "%LOG%" (powershell -NoProfile -Command "Get-Content -Tail 8 -Encoding UTF8 '%LOG%'") else (echo   no log yet)
echo.
pause
exit /b 0

rem ---------------------------------------------------------- status
:status
echo Process check (window title = %TITLE%):
tasklist /FI "WINDOWTITLE eq %TITLE%*" /FO TABLE
echo.
echo Log tail:
if exist "%LOG%" (powershell -NoProfile -Command "Get-Content -Tail 15 -Encoding UTF8 '%LOG%'") else (echo   no log yet)
echo.
pause
exit /b 0
