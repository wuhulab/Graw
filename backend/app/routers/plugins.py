# -*- coding: utf-8 -*-
"""
plugins.py - Graw 应用接口开放协议（GPOP）路由

结构：
  1. 管理接口（ADMIN，前缀 /api/plugins）
     - GET    /api/plugins/protocol        协议信息（版本/能力清单）
     - GET    /api/plugins                  已安装插件列表
     - POST   /api/plugins/install          安装插件（本地示例 或 远程 plugin.yml）
     - POST   /api/plugins/{id}/start       启动（docker compose start）
     - POST   /api/plugins/{id}/stop        停止（docker compose stop）
     - POST   /api/plugins/{id}/restart     重启（docker compose restart）
     - POST   /api/plugins/{id}/uninstall   卸载（docker compose down + 移除注册）
     - POST   /api/plugins/{id}/rotate-token  轮换访问令牌（明文仅返回一次）
     - GET    /api/plugins/{id}/config      读取插件持久化配置（管理视角）

  2. 插件开放接口（端点内按令牌鉴权，前缀 /api/op，不挂全局 ADMIN）
     - GET    /api/op/me        插件自身信息 + 面板基本信息（需 panel_info 能力）
     - POST   /api/op/notify    向面板通知中心推送消息（需 notify 能力）
     - POST   /api/op/audit     写入面板操作审计日志（需 audit 能力）
     - GET    /api/op/config    读取插件自有配置（需 config 能力）
     - PUT    /api/op/config    写入插件自有配置（需 config 能力，限 64KB）
     - GET    /api/op/protocol  协议版本信息（无需鉴权，供插件握手）

鉴权：
  - 开放接口使用 Header：
      X-Graw-Plugin-Id:   <plugin_id>
      Authorization: Bearer <token>
    其中 token 为安装/轮换时面板返回给插件的访问令牌；面板存哈希、比对用
    常量时间比较，真实插件容器从环境变量 GRAW_PLUGIN_TOKEN 读取。
"""
import asyncio
import logging
import os
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Header, Request
from pydantic import BaseModel, Field

from app.auth import get_current_user, get_client_ip
from app import auditlog
from app import hostfs
from app import plugin_protocol as pp
# 复用 appstore 的 compose 执行与 SSRF 防护（不重复造轮子）
from app.routers import appstore as _appstore
from app.routers import notify as _notify

logger = logging.getLogger("graw.plugin")

router = APIRouter()      # 管理接口：main.py 按 enabled 条件挂载（ADMIN）
op_router = APIRouter()   # 插件开放接口：main.py 按 enabled 条件挂载（内部鉴权）
# 开关路由：始终注册（否则关闭插件后无法再次开启），仅含 settings 端点
settings_router = APIRouter()

_PANEL_DEFAULT_URL = "http://127.0.0.1:8000"

# 本地示例插件目录（开发模式）：app-store/plugins/<id>/ 与 项目根 plugin-examples/<id>/
_ROUTERS_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.abspath(os.path.join(_ROUTERS_DIR, "..", "..", ".."))
LOCAL_PLUGIN_ROOTS = (
    os.path.join(_PROJECT_ROOT, "app-store", "plugins"),
    os.path.join(_PROJECT_ROOT, "plugin-examples"),
)


# ------------------------------------------------------------
# 插件功能总开关（settings_router 始终注册，不随 enabled 裁剪）
# ------------------------------------------------------------
class SettingsBody(BaseModel):
    enabled: bool = False


@settings_router.get("/settings")
async def get_settings():
    """读取插件功能总开关状态（供设置界面展示）。"""
    return {
        "enabled": pp.is_enabled(),
        "api_version": pp.OPEN_API_VERSION,
        "capabilities": list(pp.CAPABILITIES),
        # 改为关闭后需重启面板才完全生效，提示给前端
        "restart_required": True,
    }


@settings_router.put("/settings")
async def put_settings(body: SettingsBody):
    """写入插件功能总开关。

    关闭后 main.py 重启时不再注册插件业务路由（真正不加载插件代码）；
    settings 开关路由始终注册，保证面板还能重新打开插件。
    """
    saved = pp.set_enabled(bool(body.enabled))
    logger.info("插件功能总开关已修改: enabled=%s", saved)
    return {
        "ok": True,
        "enabled": saved,
        "restart_required": True,
        "hint": "插件开关修改后需重启面板生效",
    }


# ------------------------------------------------------------
# 协议信息
# ------------------------------------------------------------
@router.get("/protocol")
async def protocol_info():
    """返回协议版本、能力清单与本地示例插件目录，供开发者与前端展示。"""
    examples = _list_local_examples()
    return {
        "api_version": pp.OPEN_API_VERSION,
        "capabilities": list(pp.CAPABILITIES),
        "name": "Graw Plugin Open Protocol",
        "local_examples": examples,
    }


def _list_local_examples() -> list:
    """扫描本地示例插件目录（返回 [{id, found}]，开发模式用）。"""
    found = {}
    for root in LOCAL_PLUGIN_ROOTS:
        if not os.path.isdir(root):
            continue
        for sub in sorted(os.listdir(root)):
            if not sub.startswith(".") and os.path.isdir(os.path.join(root, sub)):
                found[sub] = True
    return [{"id": k} for k in sorted(found)]


# ------------------------------------------------------------
# 列表 / 详情
# ------------------------------------------------------------
@router.get("")
async def list_plugins():
    """返回已安装插件列表（不含令牌哈希等敏感字段）。"""
    out = []
    for rec in pp.list_plugins() or []:
        out.append(_public_record(rec))
    return {"api_version": pp.OPEN_API_VERSION, "count": len(out), "plugins": out}


def _public_record(rec: dict) -> dict:
    """脱敏插件记录：不回传 token_hash；附加 compose 是否存在的标记。"""
    public = {k: v for k, v in rec.items() if k not in ("token_hash", "manifest")}
    manifest = rec.get("manifest") or {}
    if isinstance(manifest, dict) and "env" in manifest:
        public["env"] = manifest["env"]
    compose = rec.get("compose_file") or ""
    # 路径注入防护（py/path-injection）：compose_file 为持久化外部可控值——
    # 先 abspath/normpath 归一化，再用 commonpath 前缀检查验证落在 data/ 内，
    # 文件操作只在检查通过的分支执行（与 main.py spa_fallback 同款守卫语义）。
    root = os.path.normpath(os.path.abspath(pp.DATA_DIR))
    cand = os.path.normpath(os.path.abspath(compose)) if compose else ""
    try:
        safe = bool(cand) and os.path.commonpath([cand, root]) == root
    except ValueError:
        # Windows 跨盘符 / UNC：不可能位于 data 目录内
        safe = False
    public["has_compose"] = safe and bool(cand) and os.path.exists(cand)
    return public


@router.get("/{plugin_id}")
async def get_plugin(plugin_id: str):
    rec = pp.get_plugin(plugin_id)
    if not rec:
        raise HTTPException(status_code=404, detail=f"插件不存在: {plugin_id}")
    return _public_record(rec)


# ------------------------------------------------------------
# 安装
# ------------------------------------------------------------
class InstallRequest(BaseModel):
    # 唯一 ID（同时决定本地目录/项目目录）
    id: str = Field(..., min_length=1, max_length=64)
    # 插件清单来源：local=<id>（本地示例目录） 或 url=<plugin.yml 地址>
    source: str = Field(default="local", max_length=2048)
    # 面板对外开放地址（注入 GRAW_PANEL_URL，容器需据此访问面板）
    panel_url: str = Field(default=_PANEL_DEFAULT_URL, max_length=2048)
    # 安装完成后面板开放访问的端口映射（0 = 自动取清单 entry.port，false 不映射）
    port: int = Field(default=0, ge=0, le=65535)
    # 是否启动前先拉取镜像
    pull: bool = True
    # 重启策略
    restart: str = Field(default="always", max_length=32)


def _read_manifest_local(plugin_id: str) -> tuple:
    """从本地示例目录读取 (plugin.yml 文本, plugin_dir)。找不到返回 (None, None)。"""
    for root in LOCAL_PLUGIN_ROOTS:
        pdir = os.path.join(root, plugin_id)
        yml = os.path.join(pdir, "plugin.yml")
        if os.path.isfile(yml):
            with open(yml, "r", encoding="utf-8") as f:
                return f.read(), pdir
    return None, None


def _read_manifest_remote(url: str) -> tuple:
    """从远程拉取 plugin.yml，返回 (文本, None)。SSRF 防护复用 appstore。"""
    _appstore._assert_public_http_url(url)
    raw = _appstore._fetch_url(url, timeout=30)
    return raw, None


def _load_yml(text: str):
    """YAML 文本解析为 dict；PyYAML 缺失时抛 500。"""
    if _appstore.yaml is None:
        raise HTTPException(status_code=500, detail="服务器缺少 PyYAML，无法解析插件清单")
    try:
        data = _appstore.yaml.safe_load(text)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"插件清单解析失败: {e}")
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="插件清单格式不正确")
    return data


def _find_compose_local(pdir: str) -> str:
    """本地目录中寻找 docker-compose.yml，返回其绝对路径。"""
    for name in ("docker-compose.yml", "docker-compose.yaml", "compose.yml"):
        path = os.path.join(pdir, name)
        if os.path.isfile(path):
            return path
    raise HTTPException(status_code=404, detail=f"本地插件目录缺少 docker-compose.yml: {pdir}")


def _find_compose_remote(manifest: dict, base_url: str) -> str:
    """远程模式下定位 compose：优先清单 compose_url，否则同一路径下的 docker-compose.yml。"""
    compose_url = manifest.get("compose_url") if isinstance(manifest.get("compose_url"), str) else ""
    if not compose_url:
        # 相对 base_url 同目录推导
        base = base_url.rsplit("/", 1)[0] if "/" in base_url else base_url
        compose_url = f"{base}/docker-compose.yml"
    _appstore._assert_public_http_url(compose_url)
    return _appstore._fetch_url(compose_url, timeout=60)


def _inject_plugin_env(services: dict, entry_svc: str, pid: str, token: str, panel_url: str) -> None:
    """向插件主服务注入 GRAW_* 协议环境变量（兼容 environment dict/list 写法）。"""
    svc = services.get(entry_svc)
    if not isinstance(svc, dict):
        return
    envs = {
        "GRAW_PLUGIN_ID": pid,
        "GRAW_PLUGIN_TOKEN": token,
        "GRAW_PANEL_URL": panel_url,
        "GRAW_PLUGIN_API_VERSION": str(pp.OPEN_API_VERSION),
    }
    cur = svc.get("environment")
    if isinstance(cur, dict):
        for k, v in envs.items():
            cur[k] = v
    elif isinstance(cur, list):
        existing = {str(x).split("=", 1)[0] for x in cur}
        for k, v in envs.items():
            if k not in existing:
                cur.append(f"{k}={v}")
    else:
        svc["environment"] = envs


@router.post("/install")
async def install_plugin(req: InstallRequest, request: Request, user: dict = Depends(get_current_user)):
    auditlog.record(
        "安装插件", user["username"], get_client_ip(request),
        f"安装插件 {req.id}（source={req.source or 'local'}）",
    )
    return await asyncio.to_thread(_install_sync, req)


def _install_sync(req: InstallRequest):
    """安装插件：读取清单 → 校验 → 注入环境 → 写入项目目录 → docker compose up。

    一站式完成，失败时抛出 HTTPException；成功返回含令牌明文（仅此一次）。
    """
    # 1. ID 白名单
    try:
        pid = pp._validate_plugin_id(req.id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # 2. 获取 plugin.yml 文本与来源信息
    source = (req.source or "").strip()
    if not source:
        source = "local"
    if source == "local" or source.startswith("local:"):
        look_id = source.split(":", 1)[1] if ":" in source else pid
        yml_text, pdir = _read_manifest_local(look_id)
        if yml_text is None:
            raise HTTPException(status_code=404, detail=f"本地示例插件不存在: {look_id}")
        source_type = "local"
        base_url = ""
    elif source.startswith(("http://", "https://")):
        yml_text, pdir = _read_manifest_remote(source)
        source_type = "remote"
        base_url = source
    else:
        # 兼容：直接给本地目录相对路径（开发调试），不做多余限制
        base_url = ""
        if os.path.isdir(source):
            pdir = source
            yml_path = os.path.join(pdir, "plugin.yml")
            if not os.path.isfile(yml_path):
                raise HTTPException(status_code=404, detail=f"目录缺少 plugin.yml: {pdir}")
            with open(yml_path, "r", encoding="utf-8") as f:
                yml_text = f.read()
            source_type = "dir"
        else:
            raise HTTPException(status_code=400, detail=f"非法 source: {source}")

    # 3. 校验清单：清单中的 id 必须与安装请求 id 一致（防目录穿越/错位）
    data = _load_yml(yml_text)
    manifest = _validate_manifest_http(data)
    if manifest.get("id") != pid:
        raise HTTPException(status_code=400, detail=f"清单 ID 不一致: {manifest.get('id')} != {pid}")

    # 4. 获取 docker-compose 内容
    if source_type == "remote":
        compose_text = _find_compose_remote(manifest, base_url)
    else:
        compose_path_local = _find_compose_local(pdir)
        with open(compose_path_local, "r", encoding="utf-8") as f:
            compose_text = f.read()

    # 5. 解析 compose 并注入插件令牌环境变量
    yaml = _appstore.yaml
    if yaml is None:
        raise HTTPException(status_code=500, detail="服务器缺少 PyYAML，无法改写 compose")
    try:
        cdata = yaml.safe_load(compose_text)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"docker-compose.yml 解析失败: {e}")
    if not isinstance(cdata, dict) or not isinstance(cdata.get("services"), dict):
        raise HTTPException(status_code=400, detail="docker-compose.yml 缺少 services 定义")
    services = cdata["services"]

    # 6. 主服务识别：清单 entry.service 优先，缺省取首个服务
    entry_svc = ""
    entry = manifest.get("entry") or {}
    if isinstance(entry, dict) and isinstance(entry.get("service"), str):
        entry_svc = entry["service"].strip()
    if entry_svc not in services:
        entry_svc = next(iter(services), "")
    if not entry_svc:
        raise HTTPException(status_code=400, detail="compose 中没有可用的服务定义")

    # 7. 生成令牌并注入环境
    token = pp.generate_token()
    panel_url = (req.panel_url or _PANEL_DEFAULT_URL).strip().rstrip("/") or _PANEL_DEFAULT_URL
    if not panel_url.startswith("http://") and not panel_url.startswith("https://"):
        raise HTTPException(status_code=400, detail="panel_url 必须是 http/https 地址")
    _inject_plugin_env(services, entry_svc, pid, token, panel_url)

    # 8. 端口映射：请求指定 / 清单声明，二者取其一
    port = req.port
    if not port and isinstance(entry, dict):
        port = int(entry.get("port") or 0)
    if port:
        try:
            _appstore._set_env(services.get(entry_svc), "GRAW_ENTRY_PORT", str(port))
        except Exception:
            # 环境变量注入失败不致命（端口映射才是关键）
            logger.debug("注入 GRAW_ENTRY_PORT 失败：%s", pid)
        _apply_single_port(services.get(entry_svc), port)
        _open_firewall_port(port, pid)

    # 9. 重启策略
    try:
        restart = req.restart if req.restart in _appstore.VALID_RESTART else "always"
    except Exception:
        restart = "always"
    for svc in services.values():
        if isinstance(svc, dict):
            svc["restart"] = restart

    # 10. 写入项目目录
    project_dir = os.path.join(pp.DATA_DIR, "plugins", pid)
    os.makedirs(project_dir, exist_ok=True)
    compose_path = os.path.join(project_dir, "docker-compose.yml")
    with open(compose_path, "w", encoding="utf-8") as f:
        f.write(yaml.safe_dump(cdata, sort_keys=False, allow_unicode=True, default_flow_style=False))
    logger.info("插件 compose 已写入: %s", compose_path)

    # 11. 执行 docker compose up（复用 appstore 引擎发现与执行）
    try:
        prefix = _appstore._compose_runner()
    except HTTPException as e:
        raise HTTPException(status_code=503, detail=f"Docker/Podman 不可用，无法安装插件: {e.detail}")
    engine_path = _engine_visible_path(compose_path)
    if req.pull:
        _appstore._run_compose(prefix, engine_path, ["pull"], timeout=1800)
    _appstore._run_compose(prefix, engine_path, ["up", "-d", "--remove-orphans"], timeout=1800)

    # 12. 写入注册表（token 只存哈希）
    rec = pp.register_plugin(
        plugin_id=pid,
        manifest=manifest,
        token_hash=pp.hash_token(token),
        compose_file=compose_path,
        port=port or None,
    )
    rec = pp.update_plugin_status(pid, status="running", enabled=True, installed_at=time.strftime("%Y-%m-%dT%H:%M:%S"))
    auditlog.record("安装插件完成", "", "", f"插件 {pid} 安装成功")
    return {
        "ok": True,
        "plugin": _public_record(rec),
        "token": token,  # 明文仅本次返回，前端提示用户妥善保存
        "entry_service": entry_svc,
        "panel_url": panel_url,
        "compose_file": compose_path,
        "warnings": [],
    }


def _validate_manifest_http(data: dict) -> dict:
    """清单校验包装：ValueError → HTTPException。"""
    try:
        return pp.validate_manifest(data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


def _apply_single_port(svc: dict, external: int) -> None:
    """给服务添加 外部:外部 端口映射（插件未声明映射时兜底）。"""
    if not isinstance(svc, dict):
        return
    ports = svc.get("ports")
    if isinstance(ports, list):
        ports.append(f"{external}:{external}")
    else:
        svc["ports"] = [f"{external}:{external}"]


def _open_firewall_port(port: int, plugin_id: str) -> None:
    """为插件外部端口放行防火墙（失败仅告警，不阻塞安装）。"""
    rule = {
        "id": str(uuid.uuid4())[:8],
        "port": port,
        "protocol": "tcp",
        "action": "allow",
        "comment": f"插件 {plugin_id}",
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    try:
        _appstore.firewall._add_port_rule(rule)
        fw = _appstore.firewall._load_fw()
        fw["port_rules"].append(rule)
        _appstore.firewall._save_fw(fw)
        logger.info("已放行插件端口 %s (tcp) for %s", port, plugin_id)
    except Exception as e:
        logger.warning("放行插件端口 %s 失败: %s", port, e)


def _engine_visible_path(compose_path: str) -> str:
    """把 compose 路径转换成宿主 docker 可读的形式（参考 appstore 实现）。"""
    if hostfs.is_host_mounted():
        try:
            return hostfs.host_visible_path(compose_path, pp.DATA_DIR)
        except Exception:
            # /host 模式调用失败时回退到普通路径（容器内与宿主不是同一文件系统时由调用方兜底）
            logger.debug("host_visible_path 失败，回退原路径")
    if os.name == "nt":
        return _appstore._to_wsl_path(compose_path)
    return compose_path


# ------------------------------------------------------------
# 启停 / 卸载 / 轮换令牌
# ------------------------------------------------------------
def _require_installed(plugin_id: str) -> tuple:
    """返回 (记录, compose_path)；未安装抛 404。"""
    rec = pp.get_plugin(plugin_id)
    if not rec:
        raise HTTPException(status_code=404, detail=f"插件不存在: {plugin_id}")
    compose = rec.get("compose_file") or ""
    # 路径注入防护：compose_file 为持久化外部可控值——归一化 + 前缀检查
    # 通过后才允许访问（与 _public_record 同款守卫语义）。
    root = os.path.normpath(os.path.abspath(pp.DATA_DIR))
    cand = os.path.normpath(os.path.abspath(compose)) if compose else ""
    try:
        safe = bool(cand) and os.path.commonpath([cand, root]) == root
    except ValueError:
        safe = False
    if not safe or not os.path.exists(cand):
        raise HTTPException(status_code=400, detail=f"插件缺少 compose 文件: {plugin_id}")
    engine_path = _engine_visible_path(cand)
    return rec, engine_path


@router.post("/{plugin_id}/start")
async def start_plugin(plugin_id: str):
    _rec, engine_path = _require_installed(plugin_id)
    try:
        prefix = _appstore._compose_runner()
    except HTTPException as e:
        raise HTTPException(status_code=503, detail=f"Docker/Podman 不可用: {e.detail}")
    rc, out, err = _appstore._run_compose(prefix, engine_path, ["start"], timeout=300)
    if rc != 0:
        raise HTTPException(status_code=500, detail=(err or out or "compose start 失败")[-2000:])
    pp.update_plugin_status(plugin_id, status="running", enabled=True)
    return {"ok": True, "plugin_id": plugin_id}


@router.post("/{plugin_id}/stop")
async def stop_plugin(plugin_id: str):
    _rec, engine_path = _require_installed(plugin_id)
    try:
        prefix = _appstore._compose_runner()
    except HTTPException as e:
        raise HTTPException(status_code=503, detail=f"Docker/Podman 不可用: {e.detail}")
    rc, out, err = _appstore._run_compose(prefix, engine_path, ["stop"], timeout=300)
    if rc != 0:
        raise HTTPException(status_code=500, detail=(err or out or "compose stop 失败")[-2000:])
    pp.update_plugin_status(plugin_id, status="stopped", enabled=False)
    return {"ok": True, "plugin_id": plugin_id}


@router.post("/{plugin_id}/restart")
async def restart_plugin(plugin_id: str):
    _rec, engine_path = _require_installed(plugin_id)
    try:
        prefix = _appstore._compose_runner()
    except HTTPException as e:
        raise HTTPException(status_code=503, detail=f"Docker/Podman 不可用: {e.detail}")
    rc, out, err = _appstore._run_compose(prefix, engine_path, ["restart"], timeout=300)
    if rc != 0:
        raise HTTPException(status_code=500, detail=(err or out or "compose restart 失败")[-2000:])
    pp.update_plugin_status(plugin_id, status="running", enabled=True)
    return {"ok": True, "plugin_id": plugin_id}


@router.post("/{plugin_id}/uninstall")
async def uninstall_plugin(plugin_id: str):
    rec, engine_path = _require_installed(plugin_id)
    try:
        prefix = _appstore._compose_runner()
        _appstore._run_compose(prefix, engine_path, ["down", "--remove-orphans"], timeout=600)
    except HTTPException as e:
        if "不存在" not in e.detail and "no such" not in e.detail.lower():
            raise
    pp.unregister_plugin(plugin_id)
    return {"ok": True, "plugin_id": plugin_id}


@router.post("/{plugin_id}/rotate-token")
async def rotate_plugin_token(plugin_id: str):
    """轮换插件访问令牌：旧令牌立即失效，返回新令牌明文（仅此一次）。"""
    if not pp.get_plugin(plugin_id):
        raise HTTPException(status_code=404, detail=f"插件不存在: {plugin_id}")
    try:
        new_token, rec = pp.rotate_token(plugin_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"插件不存在: {plugin_id}")
    return {"ok": True, "plugin_id": plugin_id, "token": new_token, "plugin": _public_record(rec)}


@router.get("/{plugin_id}/config")
async def get_plugin_config(plugin_id: str):
    if not pp.get_plugin(plugin_id):
        raise HTTPException(status_code=404, detail=f"插件不存在: {plugin_id}")
    return {"plugin_id": plugin_id, "config": pp.load_config(plugin_id)}


# ------------------------------------------------------------
# 插件开放接口（/api/op/*，令牌鉴权）
# ------------------------------------------------------------
def _require_plugin_ctx(
    x_graw_plugin_id: str = Header(default=""),
    authorization: str = Header(default=""),
) -> dict:
    """开放接口鉴权依赖：校验 插件 ID + Bearer 令牌，返回插件公共记录。"""
    pid = (x_graw_plugin_id or "").strip()
    if not pid:
        raise HTTPException(status_code=401, detail="缺少 X-Graw-Plugin-Id 头")
    token = ""
    if authorization.lower().startswith("bearer "):
        token = authorization[7:].strip()
    if not token:
        raise HTTPException(status_code=401, detail="缺少 Bearer 令牌")
    if not pp.verify_token(pid, token):
        raise HTTPException(status_code=401, detail="插件令牌校验失败")
    rec = pp.get_plugin(pid)
    if not rec or not rec.get("enabled"):
        raise HTTPException(status_code=403, detail="插件未启用")
    return rec


def _require_capability(rec: dict, cap: str) -> None:
    """能力门控：清单未声明该能力则拒绝调用。"""
    caps = set(rec.get("capabilities") or [])
    if cap not in caps:
        raise HTTPException(status_code=403, detail=f"插件未声明 {cap} 能力")


@op_router.get("/protocol")
async def op_protocol():
    """协议握手信息（无需鉴权）：用于插件端确认兼容性。"""
    return {
        "api_version": pp.OPEN_API_VERSION,
        "capabilities": list(pp.CAPABILITIES),
        "name": "Graw Plugin Open Protocol",
    }


@op_router.get("/me")
async def op_me(request: Request, rec: dict = Depends(_require_plugin_ctx)):
    """插件查询自身信息 + 面板基本信息。"""
    _require_capability(rec, "panel_info")
    return {
        "plugin": _public_record(rec),
        "panel": {
            "panel_url": str(request.base_url).rstrip("/"),
            "server_time": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "timezone_utc_offset": time.strftime("%z"),
        },
    }


class NotifyBody(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    message: str = Field(default="", max_length=4000)
    level: str = Field(default="info", max_length=16)


@op_router.post("/notify")
async def op_notify(body: NotifyBody, rec: dict = Depends(_require_plugin_ctx)):
    """插件向面板通知中心推送一条消息（走已配置的渠道）。"""
    _require_capability(rec, "notify")
    title = body.title.strip()
    if not title:
        raise HTTPException(status_code=400, detail="title 不能为空")
    level = body.level if body.level in ("info", "warn", "error") else "info"
    message = f"[插件:{rec['id']}] {title}"
    if body.message.strip():
        message = f"{message} - {body.message.strip()}"
    sent = 0
    try:
        sent = _notify.push_all(message)[0]  # (成功渠道数, 失败渠道数)
    except Exception as e:
        logger.warning("插件 %s 推送通知失败: %s", repr(rec["id"]), e)
        raise HTTPException(status_code=500, detail=f"推送通知失败: {e}")
    auditlog.record(
        "插件通知", f"plugin:{rec['id']}", "",
        f"插件推送通知: {message[:200]}",
    )
    return {"ok": True, "level": level, "channels_sent": sent}


class AuditBody(BaseModel):
    action: str = Field(..., min_length=1, max_length=64)
    detail: str = Field(default="", max_length=2000)


@op_router.post("/audit")
async def op_audit(body: AuditBody, rec: dict = Depends(_require_plugin_ctx)):
    """插件写入面板操作审计日志。"""
    _require_capability(rec, "audit")
    action = body.action.strip()[:64] or "插件事件"
    detail = body.detail.strip()[:2000]
    auditlog.record(action, f"plugin:{rec['id']}", "", detail)
    return {"ok": True}


@op_router.get("/config")
async def op_get_config(rec: dict = Depends(_require_plugin_ctx)):
    """插件读取自己的持久化配置。"""
    _require_capability(rec, "config")
    return {"plugin_id": rec["id"], "config": pp.load_config(rec["id"])}


class ConfigBody(BaseModel):
    config: dict = Field(default_factory=dict)


@op_router.put("/config")
async def op_put_config(body: ConfigBody, rec: dict = Depends(_require_plugin_ctx)):
    """插件保存自己的持久化配置（限 64KB）。"""
    _require_capability(rec, "config")
    try:
        pp.save_config(rec["id"], body.config)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, "plugin_id": rec["id"], "config": pp.load_config(rec["id"])}