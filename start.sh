#!/usr/bin/env bash
set -e

cd "$(dirname "$0")"

echo "==================================="
echo " Graw Server Panel - Unix Start"
echo "==================================="

if ! command -v go >/dev/null 2>&1; then
    echo "错误: 未找到 go 命令，请先安装 Go 1.23+" >&2
    exit 1
fi

echo "[1/2] Downloading Go modules..."
(cd backend-go && go mod download)

if [ ! -d frontend/node_modules ]; then
    echo "[2/2] Installing frontend dependencies..."
    (cd frontend && npm install)
fi

cleanup() {
    echo ""
    echo "Stopping..."
    kill "$BACK_PID" "$FRONT_PID" 2>/dev/null || true
    exit 0
}
trap cleanup INT TERM

(cd backend-go && go run .) &
BACK_PID=$!
(cd frontend && npm run dev) &
FRONT_PID=$!

echo ""
echo "Backend (Go): http://localhost:8000"
echo "Frontend:     http://localhost:5173"
echo ""
echo "提示: Go 后端修改后需重启（Ctrl+C 后重新运行脚本）。"
echo ""

wait "$BACK_PID" "$FRONT_PID"
