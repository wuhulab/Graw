@echo off
setlocal

echo ====================================
echo  Graw Server Panel - Windows Start
echo ====================================

echo [1/3] Freeing dev ports 8000 (backend) and 5173 (frontend)...
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-NetTCPConnection -LocalPort 8000,5173 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { if ($_ -ne 0) { Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue; Write-Host ('  killed PID ' + $_ + ' (port in use)') } }; Start-Sleep -Milliseconds 300"

where go >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Go not found. Please install Go 1.23+ first.
    pause
    exit /b 1
)

echo [2/3] Downloading Go modules...
pushd backend-go
call go mod download
popd

if not exist frontend\node_modules (
    echo [3/3] Installing frontend dependencies...
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
