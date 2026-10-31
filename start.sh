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

# 释放开发端口：优先 lsof，其次 fuser；缺少工具时静默跳过，保证脚本可继续
free_port() {
    local port="$1" pids=""
    if command -v lsof >/dev/null 2>&1; then
        pids="$(lsof -ti "tcp:$port" 2>/dev/null || true)"
        if [ -n "$pids" ]; then
            kill -9 $pids 2>/dev/null || true
            echo "  freed port $port (PID: $(echo $pids | tr '\n' ' '))"
        fi
    elif command -v fuser >/dev/null 2>&1; then
        fuser -k "$port/tcp" >/dev/null 2>&1 || true
    fi
}

echo "[1/3] Freeing dev ports 8000 (backend) and 5173 (frontend)..."
free_port 8000
free_port 5173
sleep 0.3

echo "[2/3] Downloading Go modules..."
(cd backend-go && go mod download)

if [ ! -d frontend/node_modules ]; then
    echo "[3/3] Installing frontend dependencies..."
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
