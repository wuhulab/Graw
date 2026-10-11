@echo off
rem =============================================================================
rem graw.bat - Graw "non-Docker local build + daemon" one-shot command (Windows)
rem
rem Production form, different from start.bat (dev hot-reload):
rem   build frontend dist -> backend serves it -> uvicorn runs as a detached daemon.
rem
rem Usage:
rem   graw.bat deps      install deps (venv + pip + npm install)
rem   graw.bat build     build frontend (frontend\dist)
rem   graw.bat start     auto deps/build when missing, then start daemon
rem   graw.bat stop      stop daemon
rem   graw.bat restart   restart
rem   graw.bat status    process + health check
rem   graw.bat log       tail running log (Ctrl+C to exit, service keeps running)
rem
rem Env vars: GRAW_PORT (default 8000), GRAW_HOST (default 0.0.0.0)
rem Note: keep this file ASCII-only, cmd parses batch under legacy code page.
rem =============================================================================
setlocal EnableExtensions

set "ROOT=%~dp0"
set "BACKEND_DIR=%ROOT%backend"
set "FRONTEND_DIR=%ROOT%frontend"
set "PYEXE=%BACKEND_DIR%\.venv\Scripts\python.exe"
set "LOG_DIR=%ROOT%logs"
set "LOG_FILE=%LOG_DIR%\graw.log"
set "PID_FILE=%LOG_DIR%\graw.pid"
set "PORT=8000"
if defined GRAW_PORT set "PORT=%GRAW_PORT%"
set "HOST=0.0.0.0"
if defined GRAW_HOST set "HOST=%GRAW_HOST%"

if "%~1"=="" goto :usage
if /i "%~1"=="deps"    goto :deps
if /i "%~1"=="build"   goto :build
if /i "%~1"=="start"   goto :start
if /i "%~1"=="stop"    goto :stop
if /i "%~1"=="restart" goto :restart
if /i "%~1"=="status"  goto :status
if /i "%~1"=="log"     goto :log
goto :usage

:deps
if not exist "%PYEXE%" (
    echo [graw] Creating Python venv...
    pushd "%BACKEND_DIR%"
    python -m venv .venv
    popd
)
echo [graw] Installing backend dependencies (skipped if already installed)...
"%PYEXE%" -m pip install -q -r "%BACKEND_DIR%\requirements.txt"
if not exist "%FRONTEND_DIR%\node_modules" (
    echo [graw] Installing frontend dependencies (npm install, slow at first time)...
    pushd "%FRONTEND_DIR%"
    call npm install --no-audit --no-fund
    popd
)
echo [graw] Deps ready.
goto :eof

:build
if not exist "%PYEXE%" call :deps
if not exist "%FRONTEND_DIR%\node_modules" call :deps
echo [graw] Building frontend (npm run build)...
pushd "%FRONTEND_DIR%"
call npm run build
popd
goto :eof

:start
call :check_running
if "%RUNNING%"=="1" (
    echo [graw] Already running, PID %DPID%. Use "graw.bat restart" to restart.
    goto :eof
)
call :deps
if not exist "%FRONTEND_DIR%\dist\index.html" call :build
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"
echo [graw] Starting daemon: uvicorn on %HOST%:%PORT% ...
rem Launch uvicorn detached from console (DETACHED_PROCESS=8), so closing this
rem window does not stop the service. stdout/stderr go to logs\graw.log and the
rem child PID is written to logs\graw.pid.
pushd "%BACKEND_DIR%"
"%PYEXE%" -c "import subprocess;log=open(r'%LOG_FILE%','ab',buffering=0);p=subprocess.Popen([r'%PYEXE%','-m','uvicorn','app.main:app','--host','%HOST%','--port','%PORT%','--workers','1'],stdout=log,stderr=subprocess.STDOUT,creationflags=8);open(r'%PID_FILE%','w').write(str(p.pid));print('[graw] daemon pid:',p.pid)"
popd
call :wait_healthy
goto :eof

:check_running
rem Check daemon state: read PID file and verify the process exists.
rem Result: RUNNING=0/1, DPID=pid.
set "RUNNING=0"
if not exist "%PID_FILE%" goto :eof
set /p DPID=<"%PID_FILE%"
tasklist /FI "PID eq %DPID%" 2>nul | find /i "%DPID%" >nul
if not errorlevel 1 set "RUNNING=1"
goto :eof

:stop
if not exist "%PID_FILE%" (
    echo [graw] Not running.
    goto :eof
)
set /p DPID=<"%PID_FILE%"
echo [graw] Stopping daemon (PID %DPID%)...
taskkill /PID %DPID% /T /F >nul 2>&1
del "%PID_FILE%" >nul 2>&1
echo [graw] Stopped.
goto :eof

:restart
call :stop
call :start
goto :eof

:status
if not exist "%PID_FILE%" (
    echo [graw] Not running.
    exit /b 1
)
set /p DPID=<"%PID_FILE%"
echo [graw] Running (PID %DPID%, port %PORT%).
curl -fsS -o nul --max-time 3 http://127.0.0.1:%PORT%/api/health >nul 2>&1
if errorlevel 1 (
    echo [graw] Health check: not ready yet (see %LOG_FILE%).
) else (
    echo [graw] Health check: OK.
)
goto :eof

:log
if not exist "%LOG_FILE%" (
    echo [graw] No log yet: %LOG_FILE%
    goto :eof
)
powershell -NoProfile -Command "Get-Content '%LOG_FILE%' -Tail 100 -Wait"
goto :eof

:wait_healthy
rem Poll /api/health up to 30s. DPID must be read BEFORE the for-block (batch
rem expands %DPID% at parse time of the whole block). Sleep via ping because
rem timeout requires an interactive console.
if exist "%PID_FILE%" set /p DPID=<"%PID_FILE%"
for /l %%i in (1,1,30) do (
    curl -fsS -o nul --max-time 3 http://127.0.0.1:%PORT%/api/health >nul 2>&1
    if not errorlevel 1 (
        echo [graw] Started OK. Open: http://localhost:%PORT%   Log: %LOG_FILE%
        goto :eof
    )
    tasklist /FI "PID eq %DPID%" 2>nul | find /i "%DPID%" >nul
    if errorlevel 1 (
        echo [graw] Daemon exited unexpectedly. Check log: %LOG_FILE%
        goto :eof
    )
    ping -n 2 127.0.0.1 >nul
)
echo [graw] Warning: health check not passed in 30s, confirm via %LOG_FILE%
goto :eof

:usage
echo Usage: graw.bat {deps^|build^|start^|stop^|restart^|status^|log}
echo   deps     install venv + pip + npm dependencies
echo   build    build frontend dist
echo   start    start as daemon (auto deps/build when missing)
echo   stop     stop daemon
echo   restart  stop + start
echo   status   process and health check
echo   log      tail log (Ctrl+C to exit)
exit /b 1
