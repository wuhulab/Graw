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
    appstore,
    tasks,
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    seed_default_users()
    yield


app = FastAPI(title="Graw Server Panel", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
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

# 只读信息路由：登录即可（桌面系统概览卡片 / 备忘录）
app.include_router(
    system.router, prefix="/api/system", tags=["system"], dependencies=PROTECTED
)
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


@app.get("/api/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
