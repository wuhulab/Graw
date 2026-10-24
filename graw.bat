@echo off
rem =============================================================================
rem graw.bat - Graw "non-Docker local build + daemon" one-shot command (Windows)
rem
rem Production form, different from start.bat (dev hot start):
rem   build frontend dist -> compile Go backend -> backend serves it as a
rem   detached daemon.
rem
rem Usage:
rem   graw.bat deps      install deps (Go modules + npm install)
rem   graw.bat build     build frontend (frontend\dist) and Go binary
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
set "BACKEND_GO_DIR=%ROOT%backend-go"
set "FRONTEND_DIR=%ROOT%frontend"
set "BIN=%BACKEND_GO_DIR%\graw-go.exe"
set "LOG_DIR=%ROOT%logs"
set "LOG_FILE=%LOG_DIR%\graw.log"
set "PID_FILE=%LOG_DIR%\graw.pid"
set "RUNNER=%LOG_DIR%\graw-runner.cmd"
set "PORT=8000"
if defined GRAW_PORT set "PORT=%GRAW_PORT%"
set "HOST=0.0.0.0"
if defined GRAW_HOST set "HOST=%GRAW_HOST%"
set "ADDR=%HOST%:%PORT%"

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
where go >nul 2>nul
if errorlevel 1 (
    echo [graw] Go not found. Please install Go 1.23+ first.
    exit /b 1
)
echo [graw] Downloading Go modules (skipped if cached)...
pushd "%BACKEND_GO_DIR%"
call go mod download
set "DEPS_RC=%ERRORLEVEL%"
popd
if not "%DEPS_RC%"=="0" (
    echo [graw] Go module download failed.
    exit /b 1
)
if not exist "%FRONTEND_DIR%\node_modules" (
    echo [graw] Installing frontend dependencies (npm install, slow at first time)...
    pushd "%FRONTEND_DIR%"
    call npm install --no-audit --no-fund
    if errorlevel 1 (
        popd
        echo [graw] npm install failed.
        exit /b 1
    )
    popd
)
echo [graw] Deps ready.
goto :eof

:build_frontend
if not exist "%FRONTEND_DIR%\node_modules" call :deps
if errorlevel 1 exit /b 1
echo [graw] Building frontend (npm run build)...
pushd "%FRONTEND_DIR%"
call npm run build
set "FE_RC=%ERRORLEVEL%"
popd
if not "%FE_RC%"=="0" (
    echo [graw] Frontend build failed.
    exit /b 1
)
goto :eof

:build_backend
echo [graw] Building Go backend (CGO_ENABLED=0)...
set "CGO_ENABLED=0"
pushd "%BACKEND_GO_DIR%"
call go build -trimpath -ldflags "-s -w" -o "%BIN%" .
set "BUILD_RC=%ERRORLEVEL%"
popd
set "CGO_ENABLED="
if not "%BUILD_RC%"=="0" (
    echo [graw] Go build failed. See the output above.
    exit /b 1
)
goto :eof

:build
call :deps
if errorlevel 1 exit /b 1
call :build_frontend
if errorlevel 1 exit /b 1
call :build_backend
if errorlevel 1 exit /b 1
goto :eof

:start
call :check_running
if "%RUNNING%"=="1" (
    echo [graw] Already running, PID %DPID%. Use "graw.bat restart" to restart.
    goto :eof
)
call :deps
if errorlevel 1 exit /b 1
if not exist "%FRONTEND_DIR%\dist\index.html" call :build_frontend
if errorlevel 1 exit /b 1
call :build_backend
if errorlevel 1 exit /b 1
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

rem Runner script keeps stdout/stderr in one log while the daemon stays detached
rem from the current console (closing this window does not stop the service).
> "%RUNNER%" (
    echo @echo off
    echo cd /d "%BACKEND_GO_DIR%"
    echo "%BIN%" ^>^> "%LOG_FILE%" 2^>^&1
)

set "GRAW_ADDR=%ADDR%"
set "GRAW_DATA_DIR=%ROOT%backend\data"
set "GRAW_FRONTEND_DIST=%FRONTEND_DIR%\dist"

echo [graw] Starting daemon: graw-go on %ADDR% ...
powershell -NoProfile -Command "$p = Start-Process -FilePath 'cmd.exe' -ArgumentList '/c','graw-runner.cmd' -WorkingDirectory '%LOG_DIR%' -WindowStyle Hidden -PassThru; $p.Id | Out-File -Encoding ascii -NoNewline '%PID_FILE%'"
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
echo   deps     download Go modules + npm dependencies
echo   build    build frontend dist and Go backend binary
echo   start    start as daemon (auto deps/build when missing)
echo   stop     stop daemon
echo   restart  stop + start
echo   status   process and health check
echo   log      tail log (Ctrl+C to exit)
exit /b 1
