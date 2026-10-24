# ============================================================
# Graw Server Panel - Go 后端（backend-go）多阶段构建
# 阶段 1: 构建前端静态资源 (Node)
# 阶段 2: 编译 Go 后端（纯静态二进制）
# 阶段 3: 精简运行时（宿主机管理所需的常用 CLI）
# ============================================================

FROM --platform=$BUILDPLATFORM node:20-alpine AS frontend-builder
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json* frontend/pnpm-lock.yaml* ./
RUN npm install --no-audit --no-fund
COPY frontend/ .
RUN npm run build

FROM --platform=$BUILDPLATFORM golang:1.23-alpine AS go-builder
WORKDIR /build/backend-go
COPY backend-go/go.mod backend-go/go.sum ./
RUN go mod download
COPY backend-go/ .
RUN CGO_ENABLED=0 go build -trimpath -ldflags "-s -w" -o /out/graw-go .

FROM alpine:3.20 AS runtime

ENV TZ=Asia/Shanghai \
    GRAW_DATA_DIR=/app/backend/data \
    GRAW_FRONTEND_DIST=/app/frontend/dist

# 面板需以 root 高权限运行才能管理宿主机（进程/文件/Docker/防火墙等），
# 仅部署于可信环境。docker-cli 供容器/镜像/卷管理，openssh-client 供子节点
# SSH 与 Git 部署，git/iproute2/procps 供部署与端口排查。
RUN apk add --no-cache \
        ca-certificates curl tzdata \
        docker-cli docker-cli-compose \
        openssh-client git iproute2 procps \
    && rm -rf /var/cache/apk/*

WORKDIR /app/backend-go
COPY --from=go-builder /out/graw-go /app/backend-go/graw-go
COPY --from=frontend-builder /build/frontend/dist /app/frontend/dist
RUN mkdir -p /app/backend/data

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/health || exit 1

EXPOSE 8000
CMD ["/app/backend-go/graw-go"]
