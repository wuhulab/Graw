# ============================================================
# Graw Server Panel - Docker 多阶段构建（Go 后端）
# 阶段 1: 构建前端静态资源 (Node)
# 阶段 2: 编译 Go 后端（按目标平台交叉编译为纯静态二进制）
# 阶段 3: 精简运行时（Alpine + 宿主机管理常用 CLI）
# ============================================================

# ---------- 阶段 1: 前端构建 ----------
# 关键优化：固定使用 BUILDPLATFORM（构建宿主，即 GitHub runner 的 amd64）来
# 构建前端。前端 dist 是平台无关的静态文件，无需为 arm64 再用 QEMU 模拟
# 跑 npm install / npm run build（模拟执行极慢，是构建卡顿的主因）。
FROM --platform=$BUILDPLATFORM node:20-alpine AS frontend-builder

WORKDIR /build/frontend

# 先复制依赖清单，充分利用 Docker 层缓存
COPY frontend/package.json frontend/package-lock.json* frontend/pnpm-lock.yaml* ./

# 安装依赖（优先使用 lock 文件保证可复现构建）
RUN npm install --no-audit --no-fund

# 复制源码并执行生产构建，输出到 frontend/dist
COPY frontend/ .
RUN npm run build

# ---------- 阶段 2: Go 后端编译 ----------
# 同样固定在构建宿主平台执行，再通过 GOOS/GOARCH 交叉编译出目标平台二进制，
# 避免 arm64 走 QEMU 模拟编译。CGO_ENABLED=0 保证产物为纯静态二进制，
# 可直接运行在 Alpine 运行时阶段。
FROM --platform=$BUILDPLATFORM golang:1.23-alpine AS go-builder

ARG TARGETOS
ARG TARGETARCH

WORKDIR /build/backend-go

# 先复制 go.mod / go.sum 并下载依赖，利用层缓存
COPY backend-go/go.mod backend-go/go.sum ./
RUN go mod download

# 复制源码并交叉编译
COPY backend-go/ .
RUN CGO_ENABLED=0 GOOS=$TARGETOS GOARCH=$TARGETARCH \
    go build -trimpath -ldflags "-s -w" -o /out/graw-go .

# ---------- 阶段 3: 后端运行时 ----------
FROM alpine:3.20 AS runtime

ENV TZ=Asia/Shanghai \
    GRAW_DATA_DIR=/app/backend/data \
    GRAW_FRONTEND_DIST=/app/frontend/dist

# 说明：作为服务器管理面板，容器需以 root 高权限运行，才能管理宿主机
# 系统资源（进程、网站配置、Docker、防火墙、文件系统等）。仅部署于可信环境。
# docker-cli 供容器/镜像/卷管理，openssh-client 供子节点 SSH 与 Git 部署，
# git/iproute2/procps 供部署与端口排查。
RUN apk add --no-cache \
        ca-certificates curl tzdata \
        docker-cli docker-cli-compose \
        openssh-client git iproute2 procps \
    && rm -rf /var/cache/apk/*

WORKDIR /app/backend-go

# 复制编译产物与前端静态资源（保持与 backend-go/internal/graw/cfg.go 的
# GRAW_FRONTEND_DIST 默认探测目录结构一致）
COPY --from=go-builder /out/graw-go /app/backend-go/graw-go
COPY --from=frontend-builder /build/frontend/dist /app/frontend/dist

# 创建数据目录（面板数据持久化）
RUN mkdir -p /app/backend/data

# 健康检查（后端提供 /api/health 接口）
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/health || exit 1

# 暴露后端端口
EXPOSE 8000

# 启动命令
CMD ["/app/backend-go/graw-go"]
