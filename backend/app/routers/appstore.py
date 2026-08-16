# -*- coding: utf-8 -*-
"""
appstore.py - Graw 社区应用商店

核心思路：
    Docker 为底层，GitHub 链接即应用。应用商店索引（index.json）托管在
    GitHub Pages / raw 链接上，面板启动时拉取该索引渲染应用列表，
    安装时按 download_url 拉取 docker-compose.yml，注入安装选项后
    在本机执行 `docker compose up -d`。

端点：
    GET  /api/appstore/config           读取索引地址配置
    PUT  /api/appstore/config           更新索引地址配置
    GET  /api/appstore/index            获取应用商店索引（支持 ?refresh=1 强制刷新）
    GET  /api/appstore/app/{id}/compose 获取某个应用的 docker-compose.yml 原文
    POST /api/appstore/install          安装应用（下载 compose → 注入选项 → compose up）

数据文件：
    backend/data/appstore.json          索引地址等配置
    backend/data/appstore/<name>/       每个已安装应用的 compose 项目目录
"""
import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import time
import uuid
from typing import Optional

import urllib.request
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

try:
    import yaml  # PyYAML：解析 / 改写 docker-compose.yml
except ImportError:  # pragma: no cover
    yaml = None

from app.routers.docker_api import get_backend, _find_podman
from app.routers import firewall
from app.routers import tasks

logger = logging.getLogger("appstore")
router = APIRouter()
# 图标是给 <img> 标签加载的公开静态资源（无法携带 Bearer token），
# 单独一个无鉴权 router，在 main.py 中以独立前缀挂载。
icons_router = APIRouter()

IS_WINDOWS = os.name == "nt"

_ROUTERS_DIR = os.path.dirname(os.path.abspath(__file__))
# s:\Graw\backend\app\routers -> s:\Graw\backend\data
DATA_DIR = os.path.join(_ROUTERS_DIR, "..", "..", "data")
# s:\Graw\backend\app\routers -> s:\Graw (项目根)
_PROJECT_ROOT = os.path.abspath(os.path.join(_ROUTERS_DIR, "..", "..", ".."))
APP_STORE_DIR = os.path.join(_PROJECT_ROOT, "app-store")
LOCAL_APPS_DIR = os.path.join(APP_STORE_DIR, "apps")
LOCAL_INDEX = os.path.join(APP_STORE_DIR, "index.json")

CFG_FILE = os.path.join(DATA_DIR, "appstore.json")

# 远程索引默认源（未配置 index_url 时使用）
DEFAULT_INDEX_URL = "https://wuhulab.github.io/Graw-app-store/index.json"
# 远程索引缓存时间：最多每天拉取一次（秒）；手动刷新也受此限制
REMOTE_INDEX_TTL = 86400
# 本地索引缓存时间（秒）：开发版使用本地商店，可手动强制刷新
LOCAL_INDEX_TTL = 60
_index_cache = {"at": 0.0, "source": "", "data": None, "error": ""}

# 合法的重启策略
VALID_RESTART = {"no", "always", "unless-stopped", "on-failure"}
# Graw 维护的应用名称：仅英文 / 数字 / 下划线 / 中划线 / 点，开头为字母数字
APP_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")


# ------------------------------------------------------------
# 配置读写
# ------------------------------------------------------------
def _load_config() -> dict:
    if not os.path.exists(CFG_FILE):
        return {"index_url": ""}
    try:
        with open(CFG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception as e:
        logger.warning("读取 appstore 配置失败: %s", e)
        return {}


def _save_config(cfg: dict):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(CFG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


@router.get("/config")
async def get_config():
    cfg = _load_config()
    index_url = cfg.get("index_url", "").strip()
    return {
        "index_url": index_url or DEFAULT_INDEX_URL,
        "configured": bool(index_url),
        "default_url": DEFAULT_INDEX_URL,
    }


class ConfigRequest(BaseModel):
    index_url: str = Field(default="", max_length=2048)


@router.put("/config")
async def update_config(req: ConfigRequest):
    # 防 SSRF：保存时即校验 scheme，仅允许 http/https，拒绝 file:// / ftp:// 等
    from urllib.parse import urlparse

    raw = req.index_url.strip()
    if raw:
        scheme = (urlparse(raw).scheme or "").lower()
        if scheme not in ("http", "https"):
            raise HTTPException(status_code=400, detail="仅支持 http/https 地址")
    cfg = _load_config()
    cfg["index_url"] = raw
    _save_config(cfg)
    # 配置变更后清空缓存，下次请求重新拉取
    _index_cache["at"] = 0.0
    return {"ok": True, "index_url": cfg["index_url"]}


# ------------------------------------------------------------
# 索引获取（远程 URL 优先，本地 app-store/index.json 兜底）
# ------------------------------------------------------------
def _fetch_url(url: str, timeout: int = 30) -> str:
    # SSRF 缓解：仅允许 http/https，拒绝 file:// / ftp:// 及本地回环等异常 scheme
    from urllib.parse import urlparse

    scheme = (urlparse(url).scheme or "").lower()
    if scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="仅支持 http/https 地址")
    req = urllib.request.Request(
        url, headers={"User-Agent": "Graw-Panel/1.0", "Accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", "replace")


def _load_local_index() -> Optional[dict]:
    """读取仓库内 app-store/index.json（本地 / 开发模式）。"""
    if not os.path.exists(LOCAL_INDEX):
        return None
    try:
        with open(LOCAL_INDEX, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception as e:
        logger.warning("读取本地索引失败: %s", e)
        return None


def _load_index(refresh: bool = False):
    """返回 (source, data, updated_at, error)。

    开发模式（app-store 目录存在）优先使用本地索引。
    远程索引默认源 DEFAULT_INDEX_URL，最多每天拉取一次（refresh 也受此限制）。
    """
    now = time.time()

    # 检查是否开发模式：本地 app-store 目录存在
    use_local = os.path.isdir(APP_STORE_DIR)

    if not refresh:
        cache_ttl = LOCAL_INDEX_TTL if use_local else REMOTE_INDEX_TTL
        if _index_cache["at"] and (now - _index_cache["at"]) < cache_ttl:
            return _index_cache["source"], _index_cache["data"], _index_cache["at"], _index_cache["error"]

    # 远程索引配置
    cfg = _load_config()
    url = cfg.get("index_url", "").strip()
    if not url:
        url = DEFAULT_INDEX_URL

    source = "local"
    data = None
    error = ""

    if url:
        # 远程索引 TTL：每天最多一次 + 手动刷新也受限制
        if refresh and _index_cache["at"] and (now - _index_cache["at"]) < REMOTE_INDEX_TTL:
            source = "remote_cached"
            error = "已达每日拉取上限（一天最多刷新一次），请明天再试"
            data = _index_cache["data"]
        else:
            try:
                raw = _fetch_url(url)
                parsed = json.loads(raw)
                if isinstance(parsed, dict) and "apps" in parsed:
                    data = parsed
                    source = "remote"
                else:
                    error = "索引格式不正确（缺少 apps 字段）"
                    logger.warning("远程索引格式不正确: %s", url)
            except Exception as e:
                error = f"远程索引拉取失败: {e}"
                logger.warning("拉取远程索引失败 %s: %s", url, e)

    # 远程失败或开发模式时回退本地索引
    if data is None and use_local:
        data = _load_local_index()
        if data is not None:
            source = "local"
        else:
            raise HTTPException(status_code=502, detail=error or "未配置索引地址，且本地无 app-store/index.json")
    elif data is None and not use_local:
        raise HTTPException(status_code=502, detail=error or "未配置索引地址")

    _index_cache.update({"at": now, "source": source, "data": data, "error": error})
    return source, data, now, error


@router.get("/index")
async def get_index(refresh: bool = False):
    source, data, _at, error = _load_index(refresh=refresh)
    store = data.get("store", {})
    apps = []
    for app in data.get("apps", []):
        item = dict(app)
        # 图标统一走本地静态服务：不依赖 GitHub Pages，且立即显示真实图标
        item["icon"] = f"/api/appstore/icons/{app.get('id', '')}"
        apps.append(item)
    return {
        "source": source,
        "error": error,
        "updated_at": store.get("updated_at", ""),
        "store": store,
        "apps": apps,
    }


@icons_router.get("/icons/{app_id}")
async def get_app_icon(app_id: str):
    """返回应用的官方图标（本地 app-store/apps/<id>/icon.png 或 icon.svg）。

    优先 PNG，其次 SVG；统一由本地静态服务提供，不依赖外部 CDN。
    """
    safe = os.path.basename(app_id)
    app_dir = os.path.join(LOCAL_APPS_DIR, safe)
    for fname, mime in (("icon.png", "image/png"), ("icon.svg", "image/svg+xml")):
        path = os.path.join(app_dir, fname)
        if os.path.isfile(path):
            return FileResponse(path, media_type=mime)
    raise HTTPException(status_code=404, detail=f"图标不存在: {app_id}")


# ------------------------------------------------------------
# 应用详情 / compose 获取
# ------------------------------------------------------------
def _find_app(app_id: str) -> dict:
    _source, data, _at, _err = _load_index()
    app = next((a for a in data.get("apps", []) if a.get("id") == app_id), None)
    if not app:
        raise HTTPException(status_code=404, detail=f"应用不存在: {app_id}")
    return app


def _get_compose_text(app: dict) -> str:
    """按 download_url 拉取 docker-compose.yml（本地模式读取仓库文件）。"""
    url = app.get("compose_url", "")
    if url:
        try:
            return _fetch_url(url, timeout=60)
        except Exception as e:
            # 远程拉取失败时尝试本地文件兜底（开发模式）
            local = os.path.join(LOCAL_APPS_DIR, app.get("id", ""), "docker-compose.yml")
            if os.path.exists(local):
                with open(local, "r", encoding="utf-8") as f:
                    return f.read()
            raise HTTPException(status_code=502, detail=f"拉取 docker-compose.yml 失败: {e}")
    local = os.path.join(LOCAL_APPS_DIR, app.get("id", ""), "docker-compose.yml")
    if os.path.exists(local):
        with open(local, "r", encoding="utf-8") as f:
            return f.read()
    raise HTTPException(status_code=404, detail="应用未提供 docker-compose.yml")


@router.get("/app/{app_id}/compose")
async def get_app_compose(app_id: str):
    app = _find_app(app_id)
    try:
        compose = _get_compose_text(app)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取 compose 失败: {e}")
    return {"app_id": app_id, "name": app.get("name", app_id), "compose": compose}


# ------------------------------------------------------------
# GitHub README 爬取
# ------------------------------------------------------------
_GITHUB_RE = re.compile(r"github\.com[:/]([^/]+)/([^/]+?)(?:\.git)?$")


def _parse_github_repo(source: str):
    """从开源社区地址解析出 (owner, repo)，非 GitHub 地址返回 None。"""
    if not source:
        return None
    m = _GITHUB_RE.search(source)
    if not m:
        return None
    return m.group(1), m.group(2).rstrip("/")


@router.get("/app/{app_id}/readme")
async def get_app_readme(app_id: str):
    app = _find_app(app_id)
    repo = _parse_github_repo(app.get("source", ""))
    if not repo:
        raise HTTPException(status_code=400, detail="该应用未提供有效的 GitHub 开源社区地址")
    owner, name = repo

    content = None
    # 优先走 GitHub API（自动识别默认分支与 README 文件名）
    try:
        api_req = urllib.request.Request(
            f"https://api.github.com/repos/{owner}/{name}/readme",
            headers={
                "Accept": "application/vnd.github.raw+json",
                "User-Agent": "Graw-Panel/1.0",
            },
        )
        with urllib.request.urlopen(api_req, timeout=30) as resp:
            content = resp.read().decode("utf-8", "replace")
    except Exception:
        content = None

    # API 失败时回退 raw 直连（依次尝试 HEAD/main/master 分支）
    if content is None:
        for branch in ("HEAD", "main", "master"):
            url = f"https://raw.githubusercontent.com/{owner}/{name}/{branch}/README.md"
            try:
                raw_req = urllib.request.Request(url, headers={"User-Agent": "Graw-Panel/1.0"})
                with urllib.request.urlopen(raw_req, timeout=30) as resp:
                    content = resp.read().decode("utf-8", "replace")
                    break
            except Exception:
                continue

    if content is None:
        raise HTTPException(status_code=502, detail=f"拉取 README 失败: {owner}/{name}")

    return {
        "app_id": app_id,
        "name": app.get("name", app_id),
        "repo": f"{owner}/{name}",
        "source": app.get("source", ""),
        "readme": content,
    }


# ------------------------------------------------------------
# 安装请求模型
# ------------------------------------------------------------
class InstallRequest(BaseModel):
    app_id: str = Field(..., min_length=1)
    # Graw 维护应用名称（仅英文），同时用作 compose 项目名
    app_name: str = Field(..., min_length=1, max_length=64)
    # 选择的版本 tag（对应 data.yml versions[].tag）
    version: str = Field(default="latest", max_length=128)
    # 外部访问端口（None 表示不映射）
    port: Optional[int] = Field(default=None, ge=1, le=65535)
    # 多端口映射：[{container, external}]，允许为应用声明的每个容器端口分别指定外部端口
    ports: Optional[list] = Field(default=None, description="多端口映射 [{container, external}]")
    # 时区（注入 TZ 环境变量）
    timezone: str = Field(default="Asia/Shanghai", max_length=64)
    # 容器名称（留空自动生成 graw-<app_name>-<随机>）
    container_name: Optional[str] = Field(default=None, max_length=64)
    # 是否放行防火墙端口（仅外部访问）
    expose_port: bool = False
    # 重启策略
    restart: str = Field(default="always", max_length=32)
    # CPU 限制（核数，0 = 不限制）
    cpu_limit: float = Field(default=0.0, ge=0)
    # 内存限制（MB，0 = 不限制）
    mem_limit_mb: int = Field(default=0, ge=0)
    # 是否先拉取镜像
    pull: bool = True
    # 用户编辑后的 compose 内容（可选，覆盖下载内容）
    compose: Optional[str] = None


# ------------------------------------------------------------
# compose 改写
# ------------------------------------------------------------
def _set_env(svc: dict, key: str, value: str):
    """向服务的 environment 注入/更新指定环境变量（兼容 dict 与 list 两种写法）。"""
    env = svc.get("environment")
    if isinstance(env, dict):
        env[key] = value
    elif isinstance(env, list):
        prefix = f"{key}="
        for i, item in enumerate(env):
            s = str(item)
            if s.startswith(prefix):
                env[i] = f"{key}={value}"
                break
        else:
            env.append(f"{key}={value}")
    else:
        svc["environment"] = {key: value}


def _apply_port(svc: dict, external: int, container: int) -> bool:
    """替换 service 中已声明的 external:container 端口映射。

    仅在服务已声明该容器端口时替换宿主端口（返回 True）；
    服务未声明该端口时返回 False，避免给配套服务（如 db）误加端口映射。
    """
    target = str(container)
    port_str = f"{external}:{container}"
    ports = svc.get("ports")
    if isinstance(ports, list):
        for i, item in enumerate(ports):
            if isinstance(item, dict):
                mapped = str(item.get("target") or item.get("container_port") or "")
                if mapped == target:
                    item["published"] = external
                    return True
            elif isinstance(item, str):
                parts = item.split(":")
                # 形如 "3001:3001" / "0.0.0.0:3001:3001" / ":3001"
                if len(parts) >= 2 and parts[-1] == target:
                    if len(parts) == 2:
                        ports[i] = port_str
                    else:
                        parts[-2] = str(external)
                        ports[i] = ":".join(parts)
                    return True
    return False


def _apply_compose_options(compose_text: str, req: InstallRequest, app: dict) -> str:
    """解析 compose 并注入 版本/容器名/重启/时区/资源限制/端口。"""
    if yaml is None:
        raise HTTPException(status_code=500, detail="服务器缺少 PyYAML，无法改写 docker-compose.yml")
    # 字符串级占位符替换（${VERSION} / ${TZ}），兼容任意出现位置
    compose_text = compose_text.replace("${VERSION}", req.version).replace("${TZ}", req.timezone)

    try:
        data = yaml.safe_load(compose_text)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"docker-compose.yml 解析失败: {e}")
    if not isinstance(data, dict) or "services" not in data:
        raise HTTPException(status_code=400, detail="docker-compose.yml 缺少 services 定义")
    services = data["services"]
    if not isinstance(services, dict):
        raise HTTPException(status_code=400, detail="services 格式不正确")

    multi = len(services) > 1
    app_ports = app.get("ports") or []
    primary_container_port = app_ports[0].get("container") if app_ports and isinstance(app_ports[0], dict) else None

    for svc_name, svc in services.items():
        if not isinstance(svc, dict):
            continue
        # 容器名称：多服务时以 <名称>_<服务> 区分，避免重名冲突
        if req.container_name:
            svc["container_name"] = f"{req.container_name}_{svc_name}" if multi else req.container_name
        else:
            svc.pop("container_name", None)
        # 重启策略
        svc["restart"] = req.restart
        # 时区
        if req.timezone:
            _set_env(svc, "TZ", req.timezone)
        # 资源限制
        limits = {}
        if req.cpu_limit and req.cpu_limit > 0:
            limits["cpus"] = str(req.cpu_limit)
        if req.mem_limit_mb and req.mem_limit_mb > 0:
            limits["memory"] = f"{req.mem_limit_mb}m"
        if limits:
            deploy = svc.setdefault("deploy", {})
            if not isinstance(deploy, dict):
                deploy = {}
                svc["deploy"] = deploy
            resources = deploy.setdefault("resources", {})
            if not isinstance(resources, dict):
                resources = {}
                deploy["resources"] = resources
            resources["limits"] = limits
        # 端口映射（用户指定外部端口且应用声明了容器端口时）
        if req.ports and isinstance(req.ports, list):
            # 多端口映射：遍历用户填写的每个 {container, external} 对
            for pm in req.ports:
                c_port = int(pm.get("container") or 0)
                e_port = int(pm.get("external") or 0)
                if c_port > 0 and e_port > 0:
                    _apply_port(svc, e_port, c_port)
        elif req.port and primary_container_port:
            _apply_port(svc, req.port, int(primary_container_port))

    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)


# ------------------------------------------------------------
# 执行 docker compose
# ------------------------------------------------------------
def _to_wsl_path(p: str) -> str:
    """把 Windows 路径转换为 WSL 内路径（C:\\x -> /mnt/c/x）。"""
    p = p.replace("\\", "/")
    m = re.match(r"^([A-Za-z]):(/.*)$", p)
    if m:
        return f"/mnt/{m.group(1).lower()}{m.group(2)}"
    return p


def _compose_runner() -> list:
    """返回可执行 compose 子命令的 CLI 前缀（复用 docker_api 引擎发现）。"""
    try:
        kind, _client = get_backend()
    except HTTPException as e:
        raise HTTPException(status_code=503, detail=f"Docker/Podman 不可用，无法安装应用: {e.detail}")
    if kind == "cli":
        return _find_podman()
    # Docker SDK 模式：退化为 docker CLI（compose 需要 CLI）
    docker_cli = shutil.which("docker")
    if docker_cli:
        return [docker_cli]
    raise HTTPException(status_code=503, detail="检测到 Docker SDK 但缺少 docker CLI，无法执行 docker compose")


def _run_compose(prefix: list, compose_path: str, args: list, timeout: int = 1800):
    """执行 compose 子命令，返回 (returncode, stdout, stderr)。"""
    cmd = prefix + ["compose", "-f", compose_path] + args
    logger.info("执行: %s", " ".join(cmd))
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="docker compose 执行超时（可能仍在拉取镜像），请稍后在 Docker 管理中查看状态")
    out = p.stdout.decode("utf-8", "replace")
    err = p.stderr.decode("utf-8", "replace")
    return p.returncode, out, err


def _open_firewall_port(port: int, app_name: str) -> None:
    """为外部访问放行防火墙端口（失败仅告警，不阻塞安装）。"""
    rule = {
        "id": str(uuid.uuid4())[:8],
        "port": port,
        "protocol": "tcp",
        "action": "allow",
        "comment": f"应用商店 {app_name}",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    try:
        firewall._add_port_rule(rule)
        firewall_data = firewall._load_fw()
        firewall_data["port_rules"].append(rule)
        firewall._save_fw(firewall_data)
        logger.info("已放行防火墙端口 %s (tcp) for %s", port, app_name)
    except Exception as e:
        logger.warning("放行防火墙端口 %s 失败: %s", port, e)


# ------------------------------------------------------------
# 安装（同步接口，保留兼容）
# ------------------------------------------------------------
@router.post("/install")
async def install(req: InstallRequest):
    # compose 拉取 / 执行均为阻塞 IO，放到线程池避免阻塞事件循环
    return await asyncio.to_thread(_install_sync, req)


def _install_prepare(req: InstallRequest):
    """安装前的公共准备步骤：校验、拉取/改写 compose、写盘、放行防火墙。

    返回 (container_name, project_dir, compose_path, engine_path, warnings, prefix)。
    失败时抛出 HTTPException。
    """
    # 1. 参数校验
    if not APP_NAME_RE.match(req.app_name):
        raise HTTPException(status_code=400, detail="Graw 维护应用名称只能包含英文字母 / 数字 / _ / - / .，且必须以字母或数字开头")
    if req.restart not in VALID_RESTART:
        raise HTTPException(status_code=400, detail=f"非法重启策略: {req.restart}，可选 {sorted(VALID_RESTART)}")
    if req.container_name and not APP_NAME_RE.match(req.container_name):
        raise HTTPException(status_code=400, detail="容器名称只能包含英文字母 / 数字 / _ / - / .")

    # 2. 定位应用并获取 compose
    app = _find_app(req.app_id)
    compose_text = req.compose if req.compose and req.compose.strip() else _get_compose_text(app)

    # 3. 注入安装选项
    try:
        final_compose = _apply_compose_options(compose_text, req, app)
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("改写 compose 失败")
        raise HTTPException(status_code=500, detail=f"改写 compose 失败: {e}")

    # 4. 写入本地项目目录
    # 容器名默认 = graw-<app_name>（app_name 前端默认已含随机后缀，保持一致）
    container_name = req.container_name or f"graw-{req.app_name}"
    project_dir = os.path.normpath(os.path.join(DATA_DIR, "appstore", req.app_name))
    os.makedirs(project_dir, exist_ok=True)
    compose_path = os.path.join(project_dir, "docker-compose.yml")
    with open(compose_path, "w", encoding="utf-8") as f:
        f.write(final_compose)
    logger.info("compose 已写入: %s", compose_path)

    warnings = []
    # 5. 可选：放行防火墙端口（支持多端口）
    if req.expose_port and (req.ports or req.port):
        opened = []
        if req.ports and isinstance(req.ports, list):
            opened = [int(pm["external"]) for pm in req.ports if isinstance(pm, dict) and pm.get("external")]
        elif req.port:
            opened = [req.port]
        for p in opened:
            try:
                _open_firewall_port(p, req.app_name)
            except Exception as e:
                warnings.append(f"防火墙端口 {p} 放行失败: {e}")

    # 6. 引擎与命令路径
    prefix = _compose_runner()
    engine_path = _to_wsl_path(compose_path) if IS_WINDOWS else compose_path
    return container_name, project_dir, compose_path, engine_path, warnings, prefix


def _install_sync(req: InstallRequest):
    container_name, project_dir, compose_path, engine_path, warnings, prefix = _install_prepare(req)

    pull_output = ""
    if req.pull:
        rc, out, err = _run_compose(prefix, engine_path, ["pull"], timeout=1800)
        pull_output = (out or err).strip()
        if rc != 0:
            warnings.append(f"镜像拉取可能失败（继续尝试启动）:\n{pull_output[-2000:]}")

    rc, out, err = _run_compose(prefix, engine_path, ["up", "-d", "--remove-orphans"], timeout=1800)
    output = (out or "").strip()
    errors = (err or "").strip()

    if rc != 0:
        detail = errors or output or "docker compose up 失败"
        logger.error("安装失败 %s: %s", req.app_name, detail[-2000:])
        raise HTTPException(status_code=500, detail=f"{detail[-4000:]}\n\n项目目录: {compose_path}")

    logger.info("应用安装成功: %s (%s)", req.app_name, req.app_id)
    return {
        "ok": True,
        "app_id": req.app_id,
        "app_name": req.app_name,
        "container_name": container_name,
        "version": req.version,
        "project_dir": project_dir,
        "compose_file": compose_path,
        "port": req.port,
        "expose_port": req.expose_port,
        "pull": req.pull,
        "restart": req.restart,
        "output": (output or pull_output or "(无输出)")[-4000:],
        "warnings": warnings,
    }


# ------------------------------------------------------------
# 安装（SSE 流式日志）
# ------------------------------------------------------------
def _run_compose_stream(prefix: list, compose_path: str, args: list, emit, timeout: int = 1800):
    """逐行执行 compose 子命令，把 stdout/stderr 合并后逐行推送给 emit。

    返回 (returncode, 完整输出文本)。
    """
    cmd = prefix + ["compose", "-f", compose_path] + args
    logger.info("执行: %s", " ".join(cmd))
    lines = []
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            bufsize=1,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"无法启动 docker compose: {e}")
    try:
        assert proc.stdout is not None
        while True:
            raw = proc.stdout.readline()
            if raw == b"":
                break
            line = raw.decode("utf-8", "replace").rstrip("\n")
            lines.append(line)
            if line.strip():
                emit({"type": "log", "line": line})
        rc = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except Exception:
            pass
        raise HTTPException(status_code=504, detail="docker compose 执行超时（可能仍在拉取镜像）")
    return rc, "\n".join(lines)


def _now_str():
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _verify_compose_containers(prefix: list, app_name: str, container_name: str):
    """安装成功后验证 compose 项目容器是否真实创建并运行。

    podman/docker 的 compose 命名一般为 <project>_<service>_<n>（project=app_name），
    或用户指定的 container_name。仅当「至少一个匹配容器且处于运行态」才视为成功，
    否则视为失败（例如镜像拉取失败导致容器从未创建，podman-compose 却可能返回 0）。

    返回 (ok, detail)。
    """
    try:
        p = subprocess.run(
            prefix + ["ps", "-a", "--format", "json"],
            capture_output=True,
            timeout=60,
        )
    except Exception as e:
        return True, f"无法验证容器状态（跳过）: {e}"
    if p.returncode != 0:
        return True, "无法查询容器列表（跳过验证）"
    out = p.stdout.decode("utf-8", "replace").strip()
    if not out:
        return False, "compose up 返回成功，但未发现任何容器"

    containers = []
    try:
        data = json.loads(out)
        containers = data if isinstance(data, list) else [data]
    except Exception:
        # 非标准 JSON（多行 JSON）时逐行解析
        for line in out.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                containers.append(json.loads(line))
            except Exception:
                pass

    targets = {container_name} if container_name else set()
    prefixes = (app_name + "_",)
    matched = []
    for c in containers:
        # podman 的 Names 可能是字符串或数组，统一转成列表
        raw_name = c.get("Names") or c.get("name") or ""
        names = raw_name if isinstance(raw_name, list) else [raw_name]
        for name in names:
            if not name:
                continue
            if name in targets or any(name.startswith(p) for p in prefixes):
                matched.append(c)
                break

    if not matched:
        return False, "compose up 返回成功，但未找到对应容器（镜像可能拉取失败）"
    running = [
        c for c in matched
        if str(c.get("State", "")).lower() == "running"
        or str(c.get("Status", "")).lower().startswith("up")
    ]
    if not running:
        return False, f"容器已创建但未运行（共 {len(matched)} 个，状态异常）"
    return True, f"{len(running)} 个容器运行中"


def _install_stream_worker(req: InstallRequest, emit, task_id: str = None):
    """流式安装工作线程；emit 接收事件 dict（status/log/result/error）。
    如果传入 task_id，则同时将日志持久化到任务中心。
    """
    # 如果任务中心模式，先创建任务记录
    if task_id:
        tasks.create_task({
            "id": task_id,
            "type": "appstore-install",
            "status": "running",
            "app_id": req.app_id,
            "app_name": req.app_name,
            "version": req.version,
            "title": f"安装「{req.app_name}」",
        })
        # 注册一个带持久化的 emit 包装
        _orig_emit = emit
        def emit(evt):
            tasks.append_log(task_id, evt)
            _orig_emit(evt)

    try:
        container_name, project_dir, compose_path, engine_path, warnings, prefix = _install_prepare(req)
    except HTTPException as e:
        emit({"type": "error", "message": e.detail})
        if task_id:
            tasks.update_task(task_id, status="error", error=e.detail, finished_at=_now_str())
        return
    except Exception as e:
        logger.exception("安装准备失败")
        emit({"type": "error", "message": f"安装准备失败: {e}"})
        if task_id:
            tasks.update_task(task_id, status="error", error=str(e), finished_at=_now_str())
        return

    emit({"type": "status", "message": "已生成 docker-compose.yml，开始拉取 / 启动容器..."})

    if req.pull:
        emit({"type": "status", "message": "正在拉取镜像（docker compose pull）..."})
        try:
            rc, pull_output = _run_compose_stream(prefix, engine_path, ["pull"], emit)
            if rc != 0:
                warnings.append(f"镜像拉取可能失败（继续尝试启动）:\n{pull_output[-2000:]}")
        except HTTPException as e:
            emit({"type": "error", "message": e.detail})
            if task_id:
                tasks.update_task(task_id, status="error", error=e.detail, finished_at=_now_str())
            return
        emit({"type": "status", "message": "镜像拉取完成，正在启动容器..."})
    else:
        emit({"type": "status", "message": "已跳过拉取镜像，正在启动容器..."})

    try:
        rc, up_output = _run_compose_stream(prefix, engine_path, ["up", "-d", "--remove-orphans"], emit)
    except HTTPException as e:
        emit({"type": "error", "message": e.detail})
        if task_id:
            tasks.update_task(task_id, status="error", error=e.detail, finished_at=_now_str())
        return

    if rc != 0:
        msg = f"docker compose up 失败\n\n{up_output[-4000:]}\n\n项目目录: {compose_path}"
        emit({"type": "error", "message": msg})
        if task_id:
            tasks.update_task(task_id, status="error", error=msg, finished_at=_now_str())
        return

    # 关键修复：podman-compose 在镜像拉取失败时可能误报成功（rc=0 但容器未创建）。
    # up 返回成功后必须实际验证 compose 项目容器确实存在并运行，否则标记失败。
    verify_ok, verify_detail = _verify_compose_containers(prefix, req.app_name, container_name)
    if not verify_ok:
        msg = f"安装未成功：{verify_detail}\n\n{up_output[-4000:]}\n\n项目目录: {compose_path}"
        logger.warning("安装验证失败 %s: %s", req.app_name, verify_detail)
        emit({"type": "error", "message": msg})
        if task_id:
            tasks.update_task(task_id, status="error", error=msg, finished_at=_now_str())
        return
    emit({"type": "status", "message": f"容器验证通过（{verify_detail}）"})

    result_data = {
        "ok": True,
        "app_id": req.app_id,
        "app_name": req.app_name,
        "container_name": container_name,
        "version": req.version,
        "project_dir": project_dir,
        "compose_file": compose_path,
        "port": req.port,
        "expose_port": req.expose_port,
        "restart": req.restart,
        "output": (up_output or "(无输出)")[-4000:],
        "warnings": warnings,
    }
    logger.info("应用安装成功: %s (%s)", req.app_name, req.app_id)
    emit({"type": "result", "data": result_data})
    if task_id:
        tasks.update_task(task_id, status="completed", result=result_data, finished_at=_now_str())


@router.post("/install/stream")
async def install_stream(req: InstallRequest, request: Request):
    """SSE 流式安装：逐步推送状态与 docker compose 输出日志。

    每次安装会在「任务中心」创建一条持久化任务记录，日志写入任务
    日志文件；即使客户端刷新 / 断开，安装也会在后台继续执行。
    """
    task_id = uuid.uuid4().hex[:8]
    queue: asyncio.Queue = asyncio.Queue()

    def worker():
        try:
            _install_stream_worker(req, queue.put_nowait, task_id)
        except Exception as e:
            logger.exception("流式安装异常")
            queue.put_nowait({"type": "error", "message": f"安装异常: {e}"})
        finally:
            queue.put_nowait(None)  # 结束哨兵

    asyncio.get_event_loop().run_in_executor(None, worker)

    async def gen():
        # 先发送 task_id，让前端建立任务关联
        yield f"data: {json.dumps({'type': 'task_id', 'task_id': task_id}, ensure_ascii=False)}\n\n"
        while True:
            try:
                evt = await asyncio.wait_for(queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                # 客户端断开时停止（避免子进程无限等待）
                if await request.is_disconnected():
                    break
                continue
            if evt is None:
                break
            yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
