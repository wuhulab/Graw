@echo off
setlocal

echo ====================================
echo  Graw Server Panel - Windows Start
echo ====================================

where go >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Go not found. Please install Go 1.23+ first.
    pause
    exit /b 1
)

echo [1/2] Downloading Go modules...
pushd backend-go
call go mod download
popd

if not exist frontend\node_modules (
    echo [2/2] Installing frontend dependencies...
    pushd frontend
    call npm install
    popd
)

start "Graw Backend (Go)" cmd /k "cd /d %~dp0backend-go && go run ."
start "Graw Frontend" cmd /k "cd /d %~dp0frontend && npm run dev"

echo.
echo Backend (Go): http://localhost:8000
echo Frontend:     http://localhost:5173
echo.
pause
