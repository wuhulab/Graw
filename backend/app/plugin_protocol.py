# -*- coding: utf-8 -*-
"""
plugin_protocol.py - Graw 应用接口开放协议（Graw Plugin Open Protocol, 简称 GPOP）

背景：
  为了让第三方开发者可以为 Graw 开发「以插件形式运行的应用」（如自定义告警
  代理、面板增强组件、业务工具等），面板提供一个标准化的开放协议：
    1. 插件通过一份清单（plugin.yml）声明元数据与能力（manifest 协议 v1）；
    2. 面板负责插件的完整生命周期：安装 / 启停 / 卸载（底层仍走 docker compose）；
    3. 安装时面板向插件容器注入三个关键环境变量：
         GRAW_PLUGIN_ID     插件唯一 ID
         GRAW_PLUGIN_TOKEN  插件专属访问令牌（调用面板开放 API 用）
         GRAW_PANEL_URL     面板可供插件访问的地址
    4. 插件凭令牌调用面板开放 API（/api/op/*）获得受限能力的代理入口：
         /api/op/me        获取插件自身信息与面板基本信息
         /api/op/notify    向面板通知中心推送一条消息
         /api/op/audit     写入面板操作审计日志
         /api/op/config    读写插件自有持久化配置（data/plugins/<id>/config.json）

安全设计：
  - 令牌为每次安装时随机生成（secrets），持久化时只存 SHA-256 哈希，
    即使 data/plugins.json 泄露也无法直接回放令牌；
  - 开放 API 按「插件 ID + 令牌」鉴权，校验用 hmac.compare_digest 常量时间比较；
  - 插件能力受清单 capabilities 约束，开放接口会校验插件是否声明了对应能力；
  - 配置写入限制大小（64KB），防止插件把配置文件当成爆炸存储。

本模块只包含纯业务逻辑（无 FastAPI 依赖），便于单元测试；
路由封装见 routers/plugins.py。
"""
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import threading
from typing import Optional

logger = logging.getLogger("graw.plugin")

# 协议版本：插件清单 api_version 必须等于此值，双方演进时用于识别兼容性
OPEN_API_VERSION = 1

# 数据目录与文件
DATA_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "data")
)
PLUGINS_FILE = os.path.join(DATA_DIR, "plugins.json")

# 插件 ID 白名单：与 appstore 应用名一致（仅英文/数字/_/-/.，字母数字开头）
PLUGIN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")

# 插件能力清单：插件必须在 plugin.yml 里声明才可使用对应开放接口
CAPABILITIES = ("panel_info", "notify", "audit", "config")

# 配置写入大小上限（64KB），防止插件滥用配置存储
MAX_CONFIG_BYTES = 64 * 1024

# 权限收口：本模块对外暴露供业务调用。
# 使用可重入锁 RLock：register_plugin/unregister_plugin 会在持锁（_lock）状态
# 下调用 _load_registry()，后者内部也要加同一把锁；若用非重入 Lock，同线程
# 二次 acquire 会永久死锁（agent_cfg 曾因此踩坑，改为 RLock 后彻底消除）。
_plugins_lock = threading.RLock()
_plugins_cache: Optional[dict] = None


# ------------------------------------------------------------
# 注册表读写（data/plugins.json）
# ------------------------------------------------------------
def _default_registry() -> dict:
    """默认注册表结构：{ "enabled": true, "plugins": { <id>: {...} } }。

    enabled 为插件功能总开关（设置界面可关），关闭后 main.py 不注册插件
    业务路由（真正「不加载插件相关代码」）；settings 路由单独保留以便重启。
    """
    return {"api_version": OPEN_API_VERSION, "enabled": True, "plugins": {}}


def _load_registry() -> dict:
    """读取插件注册表（带内存缓存与锁）。"""
    global _plugins_cache
    with _plugins_lock:
        if _plugins_cache is not None:
            return _plugins_cache
        reg = _default_registry()
        if os.path.exists(PLUGINS_FILE):
            try:
                with open(PLUGINS_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    reg.update(data)
                    if not isinstance(reg.get("plugins"), dict):
                        reg["plugins"] = {}
            except (OSError, json.JSONDecodeError) as e:
                logger.warning("读取插件注册表失败：%s", e)
        else:
            # 首次访问时创建缺省文件，保证目录结构就绪
            _persist(reg)
        _plugins_cache = reg
        return _plugins_cache


def _persist(reg: dict) -> None:
    """原子写注册表：临时文件 + os.replace，避免写坏中断残留。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = PLUGINS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(reg, f, ensure_ascii=False, indent=2)
    os.replace(tmp, PLUGINS_FILE)


def reload() -> None:
    """强制重新读取注册表（外部修改后刷新缓存）。"""
    global _plugins_cache
    with _plugins_lock:
        _plugins_cache = None


# ------------------------------------------------------------
# 插件功能总开关（设置界面可关，关闭后「不加载插件相关代码」）
# ------------------------------------------------------------
def is_enabled() -> bool:
    """插件功能总开关是否开启。

    main.py 在启动时据此决定是否注册插件业务路由（/api/plugins、/api/op）；
    关闭后路由整体不注册，达到「不加载插件相关代码」的效果。
    settings 开关路由本身始终注册，便于管理员重新打开。
    """
    reg = _load_registry()
    return bool(reg.get("enabled", True))


def set_enabled(value: bool) -> bool:
    """写入插件功能总开关，返回生效后的值。

    说明：路由的注册/注销发生在启动时（main.py include_router），
    因此本次修改需重启面板后完全生效；关闭后旧插件代码不会继续运行。
    """
    with _plugins_lock:
        reg = _load_registry()
        reg["enabled"] = bool(value)
        _persist(reg)
    reload()
    return bool(value)


def _validate_plugin_id(plugin_id: str) -> str:
    """校验插件 ID 合法性；非法时抛 ValueError。"""
    pid = (plugin_id or "").strip()
    if not PLUGIN_ID_RE.match(pid):
        raise ValueError(
            "插件 ID 只能包含英文字母 / 数字 / _ / - / .，且必须以字母或数字开头"
        )
    return pid


def load_config(plugin_id: str) -> dict:
    """读取插件的持久化配置（不存在返回空 dict）。"""
    try:
        pid = _validate_plugin_id(plugin_id)
    except ValueError:
        return {}
    # 路径注入防护（py/path-injection）：插件 ID 为外部可控值——先经
    # normpath 归一化（CodeQL PathNormalization），再用 startswith 前缀
    # 检查（CodeQL SafeAccessCheck，仅 true 分支阻断）确认路径落在
    # data/plugins 根内；文件访问仅在检查通过的正分支执行。
    root = os.path.normpath(os.path.abspath(os.path.join(DATA_DIR, "plugins")))
    path = os.path.normpath(os.path.join(root, pid, "config.json"))
    if path.startswith(root):
        try:
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                return data if isinstance(data, dict) else {}
        except (OSError, json.JSONDecodeError) as e:
            logger.warning("读取插件 %s 配置失败：%s", repr(plugin_id), e)
    return {}


def save_config(plugin_id: str, data: dict) -> None:
    """保存插件配置（限大小 64KB，原子写）。"""
    if not isinstance(data, dict):
        raise ValueError("配置必须是 JSON 对象")
    pid = _validate_plugin_id(plugin_id)
    root = os.path.normpath(os.path.abspath(os.path.join(DATA_DIR, "plugins")))
    path = os.path.normpath(os.path.join(root, pid, "config.json"))
    if path.startswith(root):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        payload = json.dumps(data, ensure_ascii=False)
        if len(payload.encode("utf-8")) > MAX_CONFIG_BYTES:
            raise ValueError(f"配置过大（上限 {MAX_CONFIG_BYTES // 1024}KB）")
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp, path)
    else:
        raise ValueError("插件配置路径非法（超出插件目录）")


def get_plugin(plugin_id: str) -> Optional[dict]:
    """按 ID 读取插件注册记录（无则返回 None）。"""
    reg = _load_registry()
    return reg.get("plugins", {}).get(plugin_id)


def list_plugins() -> list:
    """返回全部已注册插件（按 ID 排序）。"""
    reg = _load_registry()
    plugins = reg.get("plugins", {})
    return [plugins[k] for k in sorted(plugins)]


def register_plugin(
    plugin_id: str,
    manifest: dict,
    token_hash: str,
    compose_file: str = "",
    port: Optional[int] = None,
) -> dict:
    """写入（或覆盖）一条插件注册记录。返回记录副本。

    manifest 为已通过校验的清单字段；token_hash 为令牌 SHA-256（不回存明文）。
    """
    pid = _validate_plugin_id(plugin_id)
    record = {
        "id": pid,
        "name": manifest.get("name", pid),
        "version": manifest.get("version", ""),
        "api_version": manifest.get("api_version", OPEN_API_VERSION),
        "author": manifest.get("author", ""),
        "description": manifest.get("description", ""),
        "category": manifest.get("category", ""),
        "capabilities": list(manifest.get("capabilities") or []),
        "manifest": manifest,
        "token_hash": token_hash,
        "compose_file": compose_file,
        "port": port,
        "enabled": True,
        "status": "installed",  # installed / running / stopped / error
        "installed_at": None,
    }
    with _plugins_lock:
        reg = _load_registry()
        reg.setdefault("plugins", {})[pid] = record
        _persist(reg)
    reload()
    return record


def unregister_plugin(plugin_id: str) -> None:
    """从注册表移除插件记录（卸载后调用）。不存在时静默。"""
    pid = _validate_plugin_id(plugin_id)
    with _plugins_lock:
        reg = _load_registry()
        reg.get("plugins", {}).pop(pid, None)
        _persist(reg)
    reload()


def update_plugin_status(plugin_id: str, **fields) -> Optional[dict]:
    """更新插件记录中的若干字段（如 status / enabled / port）。无此插件返回 None。"""
    pid = _validate_plugin_id(plugin_id)
    with _plugins_lock:
        reg = _load_registry()
        rec = reg.get("plugins", {}).get(pid)
        if rec is None:
            return None
        for k, v in fields.items():
            if k in ("id", "token_hash"):
                continue  # 关键字段禁止覆盖
            rec[k] = v
        _persist(reg)
    reload()
    return dict(rec) if rec else None


# ------------------------------------------------------------
# 令牌：生成 / 哈希 / 校验
# ------------------------------------------------------------
def generate_token() -> str:
    """生成插件访问令牌（随机 32 字节 url-safe）。"""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """计算令牌哈希（SHA-256 hex），用于持久化与比对。"""
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def verify_token(plugin_id: str, token: str) -> bool:
    """校验插件令牌：与注册表中的哈希常量时间比对。"""
    rec = get_plugin(plugin_id)
    if not rec:
        return False
    if not token or len(token) > 256:
        return False
    expect = rec.get("token_hash") or ""
    return hmac.compare_digest(hash_token(token), expect)


def rotate_token(plugin_id: str) -> tuple:
    """为插件轮换令牌：返回 (新令牌明文, 新记录)。

    令牌属于核心秘密，明文只在轮换/首次安装时返回一次；
    token_hash 由本函数直接改注册表（绕过 update_plugin_status 的关键字段保护）。
    """
    rec = get_plugin(plugin_id)
    if not rec:
        raise KeyError(plugin_id)
    new_token = generate_token()
    with _plugins_lock:
        reg = _load_registry()
        reg["plugins"][plugin_id]["token_hash"] = hash_token(new_token)
        _persist(reg)
    reload()
    return new_token, get_plugin(plugin_id)


# ------------------------------------------------------------
# 清单（plugin.yml / manifest）校验
# ------------------------------------------------------------
REQUIRED_FIELDS = ("id", "name", "version", "description")
# 允许出现在清单中的顶层字段（未知字段将被忽略，避免歧义）
ALLOWED_MANIFEST_FIELDS = {
    "api_version",
    "id",
    "name",
    "version",
    "author",
    "description",
    "category",
    "homepage",
    "icon",
    "capabilities",
    "entry",
    "env",
    "tags",
    "compose_url",  # 远程模式下可显式指定 docker-compose.yml 的 URL
}
# 版本号宽松校验：数字 . 字母 - _ +
_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")


def validate_manifest(raw: dict) -> dict:
    """解析并校验插件清单，返回规范化后的字段字典；非法时抛 ValueError。

    只要清单会写入磁盘 / 发往前端即可信度有限（来源可能被投毒），因此：
      - 未知字段直接丢弃（不允许透传）；
      - capabilities 只保留协议已知的能力白名单；
      - 所有字符串限制长度，防止大对象注入。
    """
    if not isinstance(raw, dict):
        raise ValueError("插件清单必须是 YAML/JSON 对象")

    # 协议版本：兼容未来 1.x，超过当前主版本则拒绝
    api_version = raw.get("api_version", OPEN_API_VERSION)
    if not isinstance(api_version, int) or isinstance(api_version, bool):
        raise ValueError("api_version 必须是整数")
    if api_version > OPEN_API_VERSION:
        raise ValueError(f"插件协议版本过新（清单 {api_version} > 面板支持 {OPEN_API_VERSION}）")

    manifest = {}

    # 必填字段
    for f in REQUIRED_FIELDS:
        val = raw.get(f)
        if val is None or not str(val).strip():
            raise ValueError(f"插件清单缺少必填字段: {f}")
        if isinstance(val, str) and len(val) > 2000:
            raise ValueError(f"字段 {f} 过长")
        manifest[f] = str(val).strip()

    # id 白名单
    pid = _validate_plugin_id(manifest["id"])

    # 版本号格式
    if not _VERSION_RE.match(manifest["version"]):
        raise ValueError("version 只能包含英文字母 / 数字 / . / _ / + / -")

    # 可选标量与链接
    for f in ("author", "category", "homepage"):
        val = raw.get(f)
        if isinstance(val, str) and val.strip():
            manifest[f] = val.strip()[:2000]

    # 远程 compose 地址：仅放行 http/https，安装侧还有 SSRF 防护二次校验
    compose_url = raw.get("compose_url")
    if isinstance(compose_url, str) and compose_url.strip().startswith(("http://", "https://")):
        manifest["compose_url"] = compose_url.strip()[:2048]

    # 能力白名单
    caps = raw.get("capabilities")
    if caps is not None:
        if not isinstance(caps, list):
            raise ValueError("capabilities 必须是字符串列表")
        clean = []
        for c in caps:
            if isinstance(c, str) and c in CAPABILITIES:
                clean.append(c)
        manifest["capabilities"] = clean

    # 入口声明（entry）：{ service, port, path }，供面板展示与端口注入
    entry = raw.get("entry")
    if isinstance(entry, dict):
        cleaned = {}
        svc = entry.get("service")
        if isinstance(svc, str) and svc.strip():
            cleaned["service"] = svc.strip()[:128]
        p = entry.get("port")
        if isinstance(p, int) and not isinstance(p, bool) and 1 <= p <= 65535:
            cleaned["port"] = p
        path = entry.get("path")
        if isinstance(path, str) and path.startswith("/"):
            cleaned["path"] = path[:512]
        if cleaned:
            manifest["entry"] = cleaned

    # 环境变量声明（env）：仅透传结构最简单的 { name, default, desc }
    envs = raw.get("env")
    if isinstance(envs, list):
        cleaned_envs = []
        for e in envs:
            if not isinstance(e, dict):
                continue
            name = e.get("name")
            if not isinstance(name, str) or not name.strip() or len(name) > 128:
                continue
            cleaned_envs.append(
                {
                    "name": name.strip(),
                    "default": str(e.get("default", ""))[:512],
                    "desc": str(e.get("desc", ""))[:512],
                }
            )
        if cleaned_envs:
            manifest["env"] = cleaned_envs

    if not manifest.get("capabilities"):
        manifest["capabilities"] = []
    manifest["api_version"] = api_version
    # entry.id 冗余一次便于前端直接读取（与 manifest.id 一致）
    if "id" not in manifest:
        manifest["id"] = pid
    return manifest


# ------------------------------------------------------------
# 插件自有配置（config）持久化
# （load_config / save_config 定义见文件前部「配置持久化」小节，
#  其中内联了归一化 + 前缀检查的路径注入防护）
# ------------------------------------------------------------