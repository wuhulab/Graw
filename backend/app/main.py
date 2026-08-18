from fastapi import FastAPI, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from contextlib import asynccontextmanager
import os

from app.routers import (
    system,
    docker_api,
    process,
    files,
    terminal,
    notes,
    auth,
    sites,
    databases,
    cron,
    firewall,
    ssl,
    logs,
    protection,
    shunx,
    tamper,
    appstore,
    tasks,
    runtime,
    disks,
    nodes,
)
from app.auth import (
    seed_default_users,
    get_current_user,
    require_admin,
    require_non_default_password,
)

# 权限分级：
#   PROTECTED - 仅需登录（只读信息类接口，如系统概览/备忘录，供桌面展示）
#   ADMIN     - 需管理员权限（文件/终端/计划任务/Docker/防火墙等管理类接口，
#               涉及写操作或任意命令执行，必须限制为管理员）
# require_admin 内部已级联 require_non_default_password + get_current_user。
PROTECTED = [Depends(get_current_user), Depends(require_non_default_password)]
ADMIN = [Depends(require_admin)]


def _secure_data_dir() -> None:
    """收紧面板数据目录权限（Linux 生效）：目录 0700、文件 0600。

    data/ 内含 users.json / secret.key / nodes.json（SSH 凭据）、
    databases.json（数据库凭据）等敏感信息；默认 umask 下其他用户
    可读。此处启动时统一收紧，避免同机低权用户窃取凭据或伪造 JWT。
    Windows 上 chmod 无实际意义，静默忽略。
    """
    data_dir = os.path.join(os.path.dirname(__file__), "..", "data")
    try:
        os.chmod(data_dir, 0o700)
        for root, dirs, files in os.walk(data_dir):
            for d in dirs:
                os.chmod(os.path.join(root, d), 0o700)
            for f in files:
                os.chmod(os.path.join(root, f), 0o600)
    except OSError:
        # Windows / 特殊文件系统（如 FAT）不支持时忽略，不影响功能
        pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    seed_default_users()
    # 安全加固：收紧 data 目录及敏感文件权限（Linux）
    _secure_data_dir()
    # 启动统一系统指标采集（供首页三卡片共享单条 WS），预热缓存并后台广播
    await system.start_metrics_producer()
    # 启动 ShunX 网页防篡改后台监控（定时备份 + 篡改检测回滚 + 在线告警推送）
    await tamper.start_tamper_monitor()
    yield
    # 关闭后台采集协程
    await system.stop_metrics_producer()
    await tamper.stop_tamper_monitor()


app = FastAPI(title="Graw Server Panel", version="1.0.0", lifespan=lifespan)

# 跨域策略：面板前后端始终同源部署——开发模式经 Vite proxy 代理 /api，
# 生产模式由本服务直接托管 frontend/dist，因此无需开放任何跨域来源。
# 此前 allow_origins=["*"] + allow_credentials=True 的组合会向任意来源
# 回显 CORS 头（Starlette 在 credentials 模式下反射 Origin），属危险配置。
app.add_middleware(
    CORSMiddleware,
    allow_origins=[],  # 不放行任何跨域来源（同源请求不受影响）
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """为所有响应附加基础安全头（CSP / 防嗅探 / 防点击劫持 / 防泄露）。"""
    response = await call_next(request)
    # CSP：默认仅允许同源；因面板前端运行时会注入 <style>（Vue scoped/echarts/xterm），
    # style-src 需放行 inline；xterm/echarts 依赖 eval 动态编译，放行 unsafe-eval。
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; "
        "script-src 'self' 'unsafe-inline' 'unsafe-eval'; "
        "style-src 'self' 'unsafe-inline'; "
        "img-src 'self' data: blob:; "
        "font-src 'self' data:; "
        "connect-src 'self' ws: wss:; "
        "frame-ancestors 'none'; "
        "base-uri 'self'; "
        "form-action 'self'",
    )
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    return response


# 公开路由：登录、当前用户、改密、健康检查
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])

# 只读信息路由：登录即可（桌面系统概览卡片 / 备忘录）。
# 注意：system 路由鉴权改为在各个 HTTP 端点内声明（_PROTECTED），
# 以便其 WebSocket 端点改用 ?token= 鉴权，而不是继承这里的全局依赖
# （全局 HTTPException 依赖会让 WS 连接无法建立）。
app.include_router(system.router, prefix="/api/system", tags=["system"])
app.include_router(
    notes.router, prefix="/api/notes", tags=["notes"], dependencies=PROTECTED
)

# 管理类路由：仅管理员
app.include_router(
    docker_api.router, prefix="/api/docker", tags=["docker"], dependencies=ADMIN
)
app.include_router(
    process.router, prefix="/api/process", tags=["process"], dependencies=ADMIN
)
app.include_router(files.router, prefix="/api/files", tags=["files"], dependencies=ADMIN)
# 终端为 WebSocket，Bearer 头无法在浏览器 WS 中设置，故在处理函数内
# 通过 ?token= 查询参数鉴权 + 强制管理员校验（见 routers/terminal.py）。
app.include_router(terminal.router, prefix="/api/terminal", tags=["terminal"])
app.include_router(sites.router, prefix="/api/sites", tags=["sites"], dependencies=ADMIN)
app.include_router(
    databases.router, prefix="/api/databases", tags=["databases"], dependencies=ADMIN
)
app.include_router(cron.router, prefix="/api/cron", tags=["cron"], dependencies=ADMIN)
app.include_router(
    firewall.router, prefix="/api/firewall", tags=["firewall"], dependencies=ADMIN
)
app.include_router(ssl.router, prefix="/api/ssl", tags=["ssl"], dependencies=ADMIN)
app.include_router(logs.router, prefix="/api/logs", tags=["logs"], dependencies=ADMIN)
app.include_router(
    protection.router,
    prefix="/api/protection",
    tags=["protection"],
    dependencies=ADMIN,
)

# ShunX 安全入口：/status 为公开接口，/config 内部自行做登录/管理员鉴权，
# 因此不在此处挂全局 PROTECTED 依赖。
app.include_router(shunx.router, prefix="/api/shunx", tags=["shunx"])

# ShunX 网页防篡改：REST 端点内部自行鉴权（只读需登录、写需管理员），
# 另有 /ws 告警推送 WebSocket（?token= 鉴权），故不挂全局依赖。
app.include_router(tamper.router, prefix="/api/tamper", tags=["tamper"])

# Graw 社区应用商店（安装会执行 docker compose，需管理员）
app.include_router(
    appstore.router, prefix="/api/appstore", tags=["appstore"], dependencies=ADMIN
)
# 应用图标是 <img> 加载的公开静态资源（无 Bearer token），单独挂载不加鉴权
app.include_router(
    appstore.icons_router, prefix="/api/appstore", tags=["appstore"]
)

# 任务中心（长线任务：应用商店安装等，需管理员）
app.include_router(
    tasks.router, prefix="/api/tasks", tags=["tasks"], dependencies=ADMIN
)

# 运行环境（创建语言运行时容器，需管理员）
app.include_router(
    runtime.router, prefix="/api/runtime", tags=["runtime"], dependencies=ADMIN
)

# 磁盘管理（查看块设备与分区，管理员）
app.include_router(
    disks.router, prefix="/api/disks", tags=["disks"], dependencies=ADMIN
)

# 多节点（多机）管理：节点增删改查 / 连接测试 / 切换当前管理主机（管理员）
app.include_router(
    nodes.router, prefix="/api/nodes", tags=["nodes"], dependencies=ADMIN
)


@app.get("/api/health")
async def health():
    return {"status": "ok"}


# Serve frontend static files if built
FRONTEND_DIST = os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "dist")
if os.path.exists(FRONTEND_DIST):
    app.mount(
        "/assets",
        StaticFiles(directory=os.path.join(FRONTEND_DIST, "assets")),
        name="assets",
    )

    @app.get("/")
    async def index():
        return FileResponse(os.path.join(FRONTEND_DIST, "index.html"))

    @app.get("/{full_path:path}")
    async def spa_fallback(full_path: str):
        """SPA 回退：支持 ShunX 安全入口等自定义路径，非 API 路径一律返回
        index.html，由前端根据地址栏路径决定展示登录页还是入口门禁。"""
        # API 未命中的路径直接返回 404，避免误回退到前端页面
        if full_path.startswith("api/"):
            return JSONResponse(status_code=404, content={"detail": "Not Found"})
        # 若对应静态文件真实存在则直接返回该文件
        candidate = os.path.normpath(os.path.join(FRONTEND_DIST, full_path))
        if (
            full_path
            and os.path.isfile(candidate)
            and os.path.commonpath([candidate, FRONTEND_DIST]) == FRONTEND_DIST
        ):
            return FileResponse(candidate)
        return FileResponse(os.path.join(FRONTEND_DIST, "index.html"))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
