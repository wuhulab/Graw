# ============================================================
# Graw Server Panel - Docker 多阶段构建
# 阶段 1: 构建前端静态资源 (Node)
# 阶段 2: 运行后端服务 (Python)
# ============================================================

# ---------- 阶段 1: 前端构建 ----------
# 关键优化：固定使用 BUILDPLATFORM（构建宿主，即 GitHub runner 的 amd64）来
# 构建前端。前端 dist 是平台无关的静态文件，无需为 arm64 再用 QEMU 模拟
# 跑 npm install / npm run build（模拟执行极慢，是构建卡顿的主因）。
# 只有下面的 runtime 阶段才会按 TARGETPLATFORM 分别构建 amd64/arm64。
FROM --platform=$BUILDPLATFORM node:20-alpine AS frontend-builder

# 设置工作目录
WORKDIR /build/frontend

# 先复制依赖清单，充分利用 Docker 层缓存
COPY frontend/package.json frontend/package-lock.json* frontend/pnpm-lock.yaml* ./

# 安装依赖（优先使用 lock 文件保证可复现构建）
RUN npm install --no-audit --no-fund

# 复制源码并执行生产构建，输出到 frontend/dist
COPY frontend/ .
RUN npm run build

# ---------- 阶段 2: 后端运行时 ----------
FROM python:3.11-slim AS runtime

# 设置环境变量
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai

# 安装系统依赖与清理（保持镜像精简）
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        curl \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

# 说明：作为服务器管理面板，容器需以 root 高权限运行，才能管理宿主机
# 系统资源（进程、网站配置、Docker、防火墙、文件系统等）。仅部署于可信环境。
# 因此这里不创建低权限用户，直接以 root 运行（见文件末尾 CMD）。
WORKDIR /app/backend

# 先复制依赖清单并安装，利用层缓存
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制后端源码
COPY backend/ .

# 复制阶段 1 构建出的前端静态资源（保持与 app/main.py 中 FRONTEND_DIST 一致的目录结构）
COPY --from=frontend-builder /build/frontend/dist /app/frontend/dist

# 创建数据目录（面板数据持久化）
RUN mkdir -p /app/backend/data

# 健康检查（后端提供 /api/health 接口）
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -fsS http://localhost:8000/api/health || exit 1

# 暴露后端端口
EXPOSE 8000

# 启动命令（生产模式，不开启 reload）
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
