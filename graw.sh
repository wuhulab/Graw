#!/usr/bin/env bash
# =============================================================================
# graw.sh — Graw 非 Docker「本地构建 + 守护进程」一键命令（Linux / macOS）
#
# 与开发用的 start.sh（前后端热重载）不同，本脚本走「生产形态」：
#   构建前端 dist -> 后端静态托管 -> uvicorn 以守护进程后台运行。
#
# 用法：
#   ./graw.sh deps       安装依赖（venv + pip + npm install）
#   ./graw.sh build      构建前端（frontend/dist）
#   ./graw.sh start      依赖/构建缺失时自动补齐，然后启动守护进程
#   ./graw.sh stop       停止守护进程
#   ./graw.sh restart    重启
#   ./graw.sh status     运行状态（进程 + 健康检查）
#   ./graw.sh log        跟踪运行日志（Ctrl+C 退出，不影响服务）
#
# 环境变量：
#   GRAW_PORT=8000       面板监听端口
#   GRAW_HOST=0.0.0.0    监听地址（仅内网可改 127.0.0.1）
#   FORCE_BUILD=1        start 时强制重新构建前端
# =============================================================================
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$ROOT/backend"
FRONTEND_DIR="$ROOT/frontend"
VENV_DIR="$BACKEND_DIR/.venv"
PY="$VENV_DIR/bin/python"
LOG_DIR="$ROOT/logs"
LOG_FILE="$LOG_DIR/graw.log"
PID_FILE="$LOG_DIR/graw.pid"
HOST="${GRAW_HOST:-0.0.0.0}"
PORT="${GRAW_PORT:-8000}"

log()  { echo "[graw] $*"; }
fail() { echo "[graw] 错误: $*" >&2; exit 1; }

# 安装依赖：venv 不存在则创建；pip/npm 依赖幂等安装（已装则秒过）
do_deps() {
  if [ ! -x "$PY" ]; then
    log "创建 Python venv..."
    (cd "$BACKEND_DIR" && python3 -m venv .venv)
  fi
  log "安装后端依赖（已安装会自动跳过）..."
  "$PY" -m pip install -q -r "$BACKEND_DIR/requirements.txt"
  if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
    log "安装前端依赖（npm install，首次较慢）..."
    (cd "$FRONTEND_DIR" && npm install --no-audit --no-fund)
  fi
}

# 构建前端：产物在 frontend/dist，由后端同源托管
do_build() {
  [ -d "$FRONTEND_DIR/node_modules" ] || do_deps
  log "构建前端（npm run build）..."
  (cd "$FRONTEND_DIR" && npm run build)
}

# 守护进程是否在运行（PID 文件 + 进程存活双确认）
is_running() {
  [ -f "$PID_FILE" ] || return 1
  local pid
  pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

# 启动守护进程：缺依赖/缺构建时自动补齐，nohup 后台运行并写 PID 文件
do_start() {
  if is_running; then
    log "已在运行（PID $(cat "$PID_FILE")），如需重启请执行 ./graw.sh restart"
    exit 0
  fi
  do_deps
  if [ ! -f "$FRONTEND_DIR/dist/index.html" ] || [ -n "$FORCE_BUILD" ]; then
    do_build
  fi
  mkdir -p "$LOG_DIR"
  log "启动守护进程：uvicorn 监听 $HOST:$PORT ..."
  (cd "$BACKEND_DIR" && nohup "$PY" -m uvicorn app.main:app \
      --host "$HOST" --port "$PORT" --workers 1 \
      >> "$LOG_FILE" 2>&1 & echo $! > "$PID_FILE")
  wait_healthy
}

# 健康检查：最多等 30 秒，期间轮询 /api/health
wait_healthy() {
  for _ in $(seq 1 30); do
    if health_ok; then
      log "启动成功 ✔  访问: http://localhost:$PORT  （日志: $LOG_FILE，PID: $(cat "$PID_FILE")）"
      return 0
    fi
    if ! is_running; then
      fail "进程已退出，请查看日志：tail -n 50 $LOG_FILE"
    fi
    sleep 1
  done
  log "警告: 服务未在 30 秒内通过健康检查（可能仍在启动），请查看日志确认"
}

# 健康探测：优先 curl，缺失时回退 python
health_ok() {
  if command -v curl >/dev/null 2>&1; then
    curl -fsS -o /dev/null --max-time 3 "http://127.0.0.1:$PORT/api/health" 2>/dev/null
  else
    "$PY" - <<PYEOF 2>/dev/null
import urllib.request, sys
try:
    urllib.request.urlopen("http://127.0.0.1:$PORT/api/health", timeout=3)
    sys.exit(0)
except Exception:
    sys.exit(1)
PYEOF
  fi
}

# 停止守护进程：SIGTERM 优雅退出，5 秒后 SIGKILL 兜底
do_stop() {
  if ! is_running; then
    log "未在运行"
    rm -f "$PID_FILE"
    return 0
  fi
  local pid
  pid="$(cat "$PID_FILE")"
  log "停止守护进程（PID $pid）..."
  kill "$pid" 2>/dev/null || true
  for _ in $(seq 1 10); do
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.5
  done
  if kill -0 "$pid" 2>/dev/null; then
    log "强制终止..."
    kill -9 "$pid" 2>/dev/null || true
  fi
  rm -f "$PID_FILE"
  log "已停止"
}

do_status() {
  if is_running; then
    log "运行中（PID $(cat "$PID_FILE")，端口 $PORT）"
    if health_ok; then log "健康检查: 正常 ✔"; else log "健康检查: 未就绪（可能正在启动或异常，见 $LOG_FILE）"; fi
  else
    log "未在运行"
    exit 1
  fi
}

case "${1:-}" in
  deps)    do_deps ;;
  build)   do_build ;;
  start)   do_start ;;
  stop)    do_stop ;;
  restart) do_stop; do_start ;;
  status)  do_status ;;
  log)     [ -f "$LOG_FILE" ] || fail "暂无日志: $LOG_FILE"; tail -n 100 -f "$LOG_FILE" ;;
  *)       sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
