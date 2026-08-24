#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

echo "==================================="
echo " Graw Server Panel - Unix Start"
echo "==================================="

if [ ! -d backend/.venv ]; then
    echo "[1/3] Creating Python venv..."
    (cd backend && python3 -m venv .venv)
fi

echo "[2/3] Installing backend dependencies..."
# shellcheck disable=SC1091
source backend/.venv/bin/activate
pip install -q -r backend/requirements.txt

if [ ! -d frontend/node_modules ]; then
    echo "[3/3] Installing frontend dependencies..."
    (cd frontend && npm install)
fi

cleanup() {
    echo "\nStopping..."
    kill $BACK_PID $FRONT_PID 2>/dev/null || true
    exit 0
}
trap cleanup INT TERM

(cd backend && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload) &
BACK_PID=$!
(cd frontend && npm run dev) &
FRONT_PID=$!

echo ""
echo "Backend:  http://localhost:8000"
echo "Frontend: http://localhost:5173"
echo ""

wait $BACK_PID $FRONT_PID
