from fastapi import FastAPI, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, Response
from contextlib import asynccontextmanager
import os
import asyncio

from app.routers import (
    system,
    docker_api,
    dockervolumes,
    containeredit,
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
    ui,
    frp,
    netstorage,
    update,
    waf,
    webmode,
    backup,
    notify,
    uptime,
    certcheck,
    panelbackup,
    loginlog,
    webstats,
    rewrite,
    sitesopts,
    svcmonitor,
    sshkeys,
    healthcheck,
    ftpusers,
    toolbox,
    phpversions,
)
from app.auth import (
    seed_default_users,
    get_current_user,
    require_admin,
    require_non_default_password,
)
from app import remote_cap
from app import agent_auth
from app import node_manager
from app import agent_client

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
    # 启动通知中心后台监控（资源阈值告警检查 + 渠道推送）
    await notify.start_monitor()
    # 启动站点可用性后台监控（定期探测 + 宕机/恢复通知）
    await uptime.start_monitor()
    # 启动证书到期后台监控（定期检查剩余天数 + 临期/过期通知）
    await certcheck.start_monitor()
    # 启动服务/端口后台监控（自定义监控项：端口/进程/systemd 服务状态检测）
    await svcmonitor.start_monitor()
    yield
    # 关闭后台采集协程
    await system.stop_metrics_producer()
    await tamper.stop_tamper_monitor()
    await notify.stop_monitor()
    await uptime.stop_monitor()
    await certcheck.stop_monitor()
    await svcmonitor.stop_monitor()


# 安全：默认关闭交互式 API 文档（/docs、/redoc、/openapi.json）。
# 这些端点注册在顶层路径（不在 /api 前缀下），会向任意陌生设备完整
# 暴露全部接口结构与参数（包括管理员端点），等于绕过 ShunX 安全入口
# 直接送上攻击面地图。开发调试时可通过环境变量 GRAW_ENABLE_DOCS=1 打开。
_ENABLE_DOCS = os.environ.get("GRAW_ENABLE_DOCS", "").strip() == "1"

# 面板版本号：用于 /api/health（前端「设置-关于」板块展示）。
# 升级版本时仅需同步修改此处，FastAPI 应用信息与此保持一致。
APP_VERSION = "1.3.4"

app = FastAPI(
    title="Graw Server Panel",
    version=APP_VERSION,
    lifespan=lifespan,
    docs_url="/docs" if _ENABLE_DOCS else None,
    redoc_url="/redoc" if _ENABLE_DOCS else None,
    openapi_url="/openapi.json" if _ENABLE_DOCS else None,
)

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


# 走 Agent 代理的业务路径判定：/api/* 中排除主面板自身/终端/WS 等
# （终端用 WebSocket + paramiko 交互，不走 HTTP 代理；auth/nodes/ui/shunx——
# 登录与节点管理是主面板自身职责；system 的 WS 监控也由主面板承担）。
_AGENT_PROXY_EXCLUDE_PREFIX = (
    "/api/auth",
    "/api/nodes",
    "/api/terminal",
    "/api/agent",
    "/api/ui",
    "/api/shunx",
    "/api/health",
)


def _should_agent_proxy(path: str) -> bool:
    """判断该请求路径是否应经由子节点 Agent 代理。

    除主面板自身职责（登录/节点管理/终端/界面设置/安全入口/健康）外的业务
    /api/* 请求，在「当前节点是远程且配置了 agent」时一律代理到子节点。
    WebSocket 升级请求（Upgrade: websocket）由中间件单独拦截，不代理。
    """
    p = (path or "").rstrip("/")
    if not p.startswith("/api/"):
        return False
    if any(p == x or p.startswith(x + "/") for x in _AGENT_PROXY_EXCLUDE_PREFIX):
        return False
    return True


async def _agent_proxy_request(request: Request) -> Response:
    """把当前请求经子节点 Agent 隧道转发，构造对应 Response。"""
    node = node_manager.get_current_node()
    if not agent_client.agent_ready(node):
        return None
    # 读取请求体（可能是 JSON / 表单 / 上传）
    try:
        raw_body = await request.body()
    except Exception:
        raw_body = b""
    # 保留原路径与查询串
    full_path = request.url.path
    if request.url.query:
        full_path += "?" + request.url.query
    # 透传关键请求头：Content-Type / Accept；Authorization 由 agent_client 注入子节点 JWT
    headers = {"Content-Type": request.headers.get("content-type", "")} if raw_body else {}
    accept = request.headers.get("accept", "")
    if accept:
        headers["Accept"] = accept
    try:
        result = await asyncio.to_thread(
            agent_client.agent_proxy, node, request.method, full_path, headers, raw_body
        )
    except Exception as e:  # noqa: BLE001 - 代理失败给可读错误
        return JSONResponse(status_code=502, content={"detail": f"Agent 代理失败: {str(e)[:300]}"})
    status = result.get("status") or 500
    body = result.get("body") or b""
    ctype = (result.get("headers") or {}).get("content-type", "text/plain")
    return Response(content=body, status_code=status, media_type=ctype)


@app.middleware("http")
async def remote_capability_guard(request: Request, call_next):
    """远端子节点能力门控（纵深防御，先注册=更内层，被外层 agent 代理优先接管）。"""
    if remote_cap.reject_if_local_remote(request.url.path):
        return JSONResponse(
            status_code=403,
            content={"detail": remote_cap.local_only_reject_reason()},
        )
    return await call_next(request)


@app.middleware("http")
async def agent_proxy_middleware(request: Request, call_next):
    """子节点 Agent 代理（最后注册=最外层，最先执行）。

    当前节点为远程且已配置 agent 时，业务 HTTP 请求优先经隧道代理到子节点，
    从而覆盖 local 类接口（否则它们会被内层 remote_cap 拦为 403）。未配置
    agent 时不代理任何请求，交回 call_next 由 remote_cap 兜底。

    「统一面板兼容」：若请求携带 X-Graw-Node，则用请求级节点覆盖全局节点，
    Agent 代理与业务路由都据此定位到对应子节点；请求处理完后复位。
    """
    if request.scope.get("type") == "http":
        # WebSocket 升级不代理（metrics/tamper 等由主面板承担，终端走 paramiko）
        upgrade = (request.headers.get("upgrade") or "").lower()
        path = request.url.path
        # 读取请求级目标节点（统一面板兼容：窗口聚焦节点经此头下发），并覆盖当前线程
        req_node = (request.headers.get("x-graw-node") or "").strip()
        prev_node = node_manager._req_ctx_node()
        if req_node:
            node_manager.set_request_node(req_node)
        try:
            if upgrade != "websocket" and _should_agent_proxy(path):
                proxied = await _agent_proxy_request(request)
                if proxied is not None:
                    return proxied
            return await call_next(request)
        finally:
            # 请求线程处理完复位请求级节点，避免串线程污染
            node_manager.set_request_node(prev_node)
    return await call_next(request)


# 公开路由：登录、当前用户、改密、健康检查
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])

# 子节点 Agent API（机器间鉴权）：/issue 用成对密钥换取 JWT（不依赖面板登录）；
# /cfg 配置接口内部自行 require_admin。router 始终挂载，端点按 agent_cfg.enabled()
# 动态启用（支持设置界面「作为子节点」热开关，无需重启）。未启用时 /issue 返回 404。
app.include_router(agent_auth.router, prefix="/api/agent", tags=["agent"])

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
# Docker 数据卷（volumes）管理：复用 docker_api 的后端探测与 CLI/SDK 工具（管理员）
app.include_router(
    dockervolumes.router,
    prefix="/api/dockervolumes",
    tags=["dockervolumes"],
    dependencies=ADMIN,
)
# 容器资源与端口编辑：读取/更新容器 CPU、内存、环境变量、端口映射（管理员）
app.include_router(
    containeredit.router,
    prefix="/api/containeredit",
    tags=["containeredit"],
    dependencies=ADMIN,
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

# 界面设置：/public 为公开接口（登录页展示用），/config 内部自行做管理员鉴权，
# 故不在此处挂全局 ADMIN 依赖（否则登录页无法公开读取网站名/欢迎语/Logo）。
app.include_router(ui.router, prefix="/api/ui", tags=["ui"])

# Frp（内网穿透）管理：可视化编辑 frps/frpc 配置 + 代理列表 + 进程启停（管理员）
app.include_router(frp.router, prefix="/api/frp", tags=["frp"], dependencies=ADMIN)

# 网络储存（FTP/FTPS/SMB/WebDAV/对象存储）：连接管理 + 远程文件操作（管理员）
app.include_router(
    netstorage.router, prefix="/api/netstorage", tags=["netstorage"], dependencies=ADMIN
)

# 面板自身更新：版本检测（只读）与一键更新（写操作需管理员）
app.include_router(update.router, prefix="/api/update", tags=["update"], dependencies=ADMIN)

# WAF 应用防火墙：站点级 Web 应用防火墙（全局开关 + 每站点策略 + 拦截日志/拦截地图）。
# 全部为读取/写入配置与管理 nginx 片段的管理类接口，挂 ADMIN 依赖。
app.include_router(waf.router, prefix="/api/waf", tags=["waf"], dependencies=ADMIN)

# Web 服务器引擎模式（NGINX / OpenResty）：查询与切换，仅管理员。
# 切换只更新引擎选择，sites/waf 等路由按当前模式解析路径与 reload 命令。
app.include_router(webmode.router, prefix="/api/webmode", tags=["webmode"], dependencies=ADMIN)

# 备份中心：目录/文件通用备份（手动 + cron 计划）、轮转、一键恢复（管理员）
app.include_router(backup.router, prefix="/api/backup", tags=["backup"], dependencies=ADMIN)

# 通知中心：通知渠道（Webhook/Telegram/钉钉/企微/Server酱/邮件）+ 资源阈值告警（管理员）
app.include_router(notify.router, prefix="/api/notify", tags=["notify"], dependencies=ADMIN)

# 站点可用性检测：监控网站/服务 HTTP 可用性，宕机/恢复推送通知（管理员）
app.include_router(uptime.router, prefix="/api/uptime", tags=["uptime"], dependencies=ADMIN)

# 证书到期提醒：检查面板 SSL 证书剩余天数，临期/过期推送通知（管理员）
app.include_router(certcheck.router, prefix="/api/certcheck", tags=["certcheck"], dependencies=ADMIN)

# 面板自身备份：导出/导入 data/ 全部配置归档（迁移与容灾，管理员）
app.include_router(panelbackup.router, prefix="/api/panelbackup", tags=["panelbackup"], dependencies=ADMIN)

# 登录日志 / 异地登录提示：记录登录 IP/时间/设备 + 异常登录检测提醒。
# 普通用户需要查看「我的登录历史」，故挂 PROTECTED 而非 ADMIN；
# list / clear / config 等管理接口内部已用 require_admin 保护。
app.include_router(loginlog.router, prefix="/api/loginlog", tags=["loginlog"], dependencies=PROTECTED)

# 网站访问统计：解析 nginx 访问日志，输出 PV/UV/IP/来源/热门页面（管理员）
app.include_router(webstats.router, prefix="/api/webstats", tags=["webstats"], dependencies=ADMIN)

# 伪静态规则库：常用框架一键伪静态（写入 nginx 配置，管理员）
app.include_router(rewrite.router, prefix="/api/rewrite", tags=["rewrite"], dependencies=ADMIN)

# 站点增强配置：防盗链 / gzip / 静态资源缓存（写入 nginx 配置，管理员）
app.include_router(sitesopts.router, prefix="/api/sitesopts", tags=["sitesopts"], dependencies=ADMIN)

# 服务/端口监控：自定义监控项（端口/进程/systemd 服务）状态看板（管理员）
app.include_router(
    svcmonitor.router, prefix="/api/svcmonitor", tags=["svcmonitor"], dependencies=ADMIN
)

# SSH 密钥管理：生成/导入密钥并一键部署到节点（配合节点管理，管理员）
app.include_router(
    sshkeys.router, prefix="/api/sshkeys", tags=["sshkeys"], dependencies=ADMIN
)

# 一键系统体检：弱密码/异常登录/危险端口/可疑任务扫描（管理员，只读报告）
app.include_router(
    healthcheck.router, prefix="/api/healthcheck", tags=["healthcheck"], dependencies=ADMIN
)

# 虚拟 FTP 用户管理：纯 Python 维护 data/ftp_users.json，无需系统用户（管理员）
app.include_router(
    ftpusers.router, prefix="/api/ftpusers", tags=["ftpusers"], dependencies=ADMIN
)

# 工具箱：Base64 / 哈希 / 时间戳 / 端口扫描 / Whois（执行外部命令与网络连接，仅管理员）
app.include_router(
    toolbox.router, prefix="/api/toolbox", tags=["toolbox"], dependencies=ADMIN
)

# PHP 多版本管理：探测系统 PHP/FPM 版本 + 站点 PHP 版本关联（管理员）
app.include_router(
    phpversions.router,
    prefix="/api/phpversions",
    tags=["phpversions"],
    dependencies=ADMIN,
)


@app.get("/api/health")
async def health():
    """公开健康检查：返回面板状态与版本号（供前端「设置-关于」展示）。"""
    return {"status": "ok", "name": "Graw", "version": APP_VERSION}


# Serve frontend static files if built
# normpath 去掉路径中的 ".." 组件：spa_fallback 的 commonpath 防穿越
# 检查需要与规范化后的 candidate 同基比较，否则带 ".." 的前缀
# 永不相等导致静态回退静默失效（安全上 fail-closed，但功能不可用）。
FRONTEND_DIST = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "frontend", "dist")
)
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
        # 若对应静态文件真实存在则直接返回该文件。
        # 注意：full_path 若是其他盘符的绝对路径（Windows 如 C:/x），
        # os.path.join 会丢弃 FRONTEND_DIST 前缀，跨盘符时 commonpath
        # 抛 ValueError——必须捕获，否则未穿越请求也会 500。
        try:
            candidate = os.path.normpath(os.path.join(FRONTEND_DIST, full_path))
            in_dist = os.path.commonpath([candidate, FRONTEND_DIST]) == FRONTEND_DIST
        except ValueError:
            candidate, in_dist = "", False
        if full_path and in_dist and os.path.isfile(candidate):
            return FileResponse(candidate)
        return FileResponse(os.path.join(FRONTEND_DIST, "index.html"))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
