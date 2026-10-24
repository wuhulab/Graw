#!/usr/bin/env bash
# =============================================================================
# graw.sh — Graw 非 Docker「本地构建 + 守护进程」一键命令（Linux / macOS）
#
# 与开发用的 start.sh（前后端分开热启动）不同，本脚本走「生产形态」：
#   构建前端 dist -> 编译 Go 后端 -> 由后端静态托管 -> 守护进程后台运行。
#
# 用法：
#   ./graw.sh deps       安装依赖（Go module + npm install）
#   ./graw.sh build      构建前端（frontend/dist）与 Go 后端二进制
#   ./graw.sh start      依赖/构建缺失时自动补齐，然后启动守护进程
#   ./graw.sh stop       停止守护进程
#   ./graw.sh restart    重启
#   ./graw.sh status     运行状态（进程 + 健康检查）
#   ./graw.sh log        跟踪运行日志（Ctrl+C 退出，不影响服务）
#
# 环境变量：
#   GRAW_PORT=8000       面板监听端口
#   GRAW_HOST=0.0.0.0    监听地址（仅本机可改 127.0.0.1）
#   FORCE_BUILD=1        start 时强制重新构建前端
# =============================================================================
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
BACKEND_GO_DIR="$ROOT/backend-go"
FRONTEND_DIR="$ROOT/frontend"
BIN="$BACKEND_GO_DIR/graw-go"
LOG_DIR="$ROOT/logs"
LOG_FILE="$LOG_DIR/graw.log"
PID_FILE="$LOG_DIR/graw.pid"
HOST="${GRAW_HOST:-0.0.0.0}"
PORT="${GRAW_PORT:-8000}"
ADDR="$HOST:$PORT"

log()  { echo "[graw] $*"; }
fail() { echo "[graw] 错误: $*" >&2; exit 1; }

# 安装依赖：Go module 幂等下载；前端 node_modules 缺失时安装
do_deps() {
  command -v go >/dev/null 2>&1 || fail "未找到 go 命令，请先安装 Go 1.23+"
  command -v npm >/dev/null 2>&1 || fail "未找到 npm 命令，请先安装 Node.js 16+"
  log "下载 Go 模块（已缓存会自动跳过）..."
  (cd "$BACKEND_GO_DIR" && go mod download)
  if [ ! -d "$FRONTEND_DIR/node_modules" ]; then
    log "安装前端依赖（npm install，首次较慢）..."
    (cd "$FRONTEND_DIR" && npm install --no-audit --no-fund)
  fi
}

# 构建前端：产物在 frontend/dist，由 Go 后端同源托管
do_build_frontend() {
  [ -d "$FRONTEND_DIR/node_modules" ] || do_deps
  log "构建前端（npm run build）..."
  (cd "$FRONTEND_DIR" && npm run build)
}

# 编译 Go 后端：纯静态二进制，输出到 backend-go/graw-go
do_build_backend() {
  command -v go >/dev/null 2>&1 || fail "未找到 go 命令，请先安装 Go 1.23+"
  log "编译 Go 后端（CGO_ENABLED=0）..."
  (cd "$BACKEND_GO_DIR" && CGO_ENABLED=0 go build -trimpath -ldflags "-s -w" -o "$BIN" .)
}

do_build() {
  do_build_frontend
  do_build_backend
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
    do_build_frontend
  fi
  do_build_backend
  mkdir -p "$LOG_DIR"
  log "启动守护进程：graw-go 监听 $ADDR ..."
  (
    cd "$BACKEND_GO_DIR"
    GRAW_ADDR="$ADDR" \
    GRAW_DATA_DIR="$ROOT/backend/data" \
    GRAW_FRONTEND_DIST="$FRONTEND_DIR/dist" \
      nohup "$BIN" >> "$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"
  )
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

# 健康探测：优先 curl，其次 wget，最后回退 TCP 端口连通性
health_ok() {
  if command -v curl >/dev/null 2>&1; then
    curl -fsS -o /dev/null --max-time 3 "http://127.0.0.1:$PORT/api/health" 2>/dev/null
  elif command -v wget >/dev/null 2>&1; then
    wget -q -O /dev/null -T 3 "http://127.0.0.1:$PORT/api/health" 2>/dev/null
  else
    (exec 3<>"/dev/tcp/127.0.0.1/$PORT") 2>/dev/null
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
  *)       sed -n '2,21p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
