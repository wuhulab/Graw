@echo off
setlocal

echo ====================================
echo  Graw Server Panel - Windows Start
echo ====================================

if not exist backend\.venv (
    echo [1/3] Creating Python venv...
    pushd backend
    python -m venv .venv
    popd
)

echo [2/3] Installing backend dependencies...
call backend\.venv\Scripts\activate.bat
pip install -q -r backend\requirements.txt

if not exist frontend\node_modules (
    echo [3/3] Installing frontend dependencies...
    pushd frontend
    call npm install
    popd
)

start "Graw Backend" cmd /k "cd /d %~dp0backend && ..\backend\.venv\Scripts\activate.bat && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"
start "Graw Frontend" cmd /k "cd /d %~dp0frontend && npm run dev"

echo.
echo Backend: http://localhost:8000
echo Frontend: http://localhost:5173
echo.
pause
