from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
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
)
from app.auth import seed_default_users, get_current_user

PROTECTED = [Depends(get_current_user)]


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

# 公开路由：登录、当前用户、改密、健康检查
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])

# 受保护路由：所有业务接口都要求登录
app.include_router(
    system.router, prefix="/api/system", tags=["system"], dependencies=PROTECTED
)
app.include_router(
    docker_api.router, prefix="/api/docker", tags=["docker"], dependencies=PROTECTED
)
app.include_router(
    process.router, prefix="/api/process", tags=["process"], dependencies=PROTECTED
)
app.include_router(
    files.router, prefix="/api/files", tags=["files"], dependencies=PROTECTED
)
# 终端为 WebSocket，Bearer 头无法在浏览器 WS 中设置，故在处理函数内
# 通过 ?token= 查询参数鉴权（见 routers/terminal.py）。
app.include_router(terminal.router, prefix="/api/terminal", tags=["terminal"])
app.include_router(
    notes.router, prefix="/api/notes", tags=["notes"], dependencies=PROTECTED
)
app.include_router(
    sites.router, prefix="/api/sites", tags=["sites"], dependencies=PROTECTED
)
app.include_router(
    databases.router,
    prefix="/api/databases",
    tags=["databases"],
    dependencies=PROTECTED,
)
app.include_router(
    cron.router, prefix="/api/cron", tags=["cron"], dependencies=PROTECTED
)
app.include_router(
    firewall.router,
    prefix="/api/firewall",
    tags=["firewall"],
    dependencies=PROTECTED,
)
app.include_router(ssl.router, prefix="/api/ssl", tags=["ssl"], dependencies=PROTECTED)
app.include_router(
    logs.router, prefix="/api/logs", tags=["logs"], dependencies=PROTECTED
)
app.include_router(
    protection.router,
    prefix="/api/protection",
    tags=["protection"],
    dependencies=PROTECTED,
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


@app.get("/api/health")
async def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
