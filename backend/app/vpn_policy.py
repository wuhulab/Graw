# -*- coding: utf-8 -*-
"""
vpn_policy.py - VPN 转发策略核心库

背景：
  面板管理一批 VPN/代理节点，对外提供「兼容 Clash 订阅格式」的订阅链接
  （/api/vpn/sub?token=xxx，由 routers/vpn.py 的 sub_router 公开提供）；
  同时在面板所在主机上托管 mihomo/clash 核心（若已安装），按「转发策略」
  渲染核心配置并管理其进程生命周期：
    - 端口转发（portForward）：为每条转发条目建立本地监听端口（mihomo
      listeners/tunnel），把 127.0.0.1:LISTEN 的流量转发到 target（可经 VPN 组）；
      订阅侧同步生成 DST-PORT 分流规则（仅这些端口走 VPN）。
    - 全局代理（globalProxy）：核心开放 mixed-port（HTTP+SOCKS5）作代理网关；
      订阅侧生成 MATCH,VPN 兜底规则（全部流量走 VPN）。
    - 低占用模式（lowPower，默认开启）：后台看门狗探测核心活跃度
      （external-controller /connections + 流量增量），空闲超过 idleMinutes
      自动关闭核心进程；下次「初次请求」（拉取订阅 / /api/vpn/wake / 手动启动）
      自动重新拉起。

设计要点：
  - 配置持久化 data/vpn.json（原子写）；核心配置渲染到 data/vpn_core.yaml，
    日志 data/vpn_core.log。核心二进制自动探测 mihomo/clash-meta/clash，
    也可在界面指定路径（binPath）。
  - 订阅令牌 subToken 与核心 API secret 均为 secrets.token_urlsafe 随机值，
    令牌比对用 hmac.compare_digest（防时序侧信道）。
  - 核心仅监听本机（allow-lan: false、external-controller 127.0.0.1），不向
    局域网暴露代理面；监听端口与面板端口冲突在入库前校验。

安全：
  - 本模块面向「面板自身所在主机」运行（同 SSH 端口转发定位，请求不经
    Agent 代理转发到子节点）；节点/转发字段全部白名单校验，拒绝控制字符，
    防止向渲染出的 YAML / 进程参数注入。
  - 订阅接口不挂 Bearer 鉴权（Clash 客户端无法携带请求头），仅凭 32 字节
    随机 token 换取配置；未启用（enabled=false）时一律 404，不泄露数据。
"""

import asyncio
import hmac
import json
import logging
import os
import platform
import re
import secrets
import shutil
import subprocess
import threading
import time
from typing import Optional

import requests
import yaml

logger = logging.getLogger("graw.vpn")

DATA_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "data"))
VPN_FILE = os.path.join(DATA_DIR, "vpn.json")
CORE_CFG_FILE = os.path.join(DATA_DIR, "vpn_core.yaml")
CORE_LOG_FILE = os.path.join(DATA_DIR, "vpn_core.log")

IS_WIN = platform.system() == "Windows"

# 核心可执行文件探测顺序（PATH 中）
CORE_BIN_NAMES = ("mihomo", "clash-meta", "clash")

# 订阅配置中的策略组名（ASCII，兼容全部 Clash 客户端）
GROUP_MAIN = "VPN"
GROUP_AUTO = "自动选择"

# 主机名 / IPv4 / IPv6（宽松校验，拒绝空白与控制字符即可）
_HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.\-:]*[A-Za-z0-9])?$")
# 通用令牌（uuid / 密码 / 加密方式 / 名称等，拒绝控制字符）
_TOKEN_RE = re.compile(r"^[^\x00-\x1f\x7f]{1,256}$")
# 支持的节点类型（Clash / Clash.Meta 兼容）
NODE_TYPES = ("ss", "vmess", "vless", "trojan", "hysteria2", "socks5", "http")

# 各节点类型的必填字段（入库存前校验，缺了渲染出的配置不可用）
_NODE_REQUIRED = {
    "ss": ("password", "cipher"),
    "vmess": ("uuid",),
    "vless": ("uuid",),
    "trojan": ("password",),
    "hysteria2": ("password",),
    "socks5": (),
    "http": (),
}

_PORT_MIN, _PORT_MAX = 1024, 65535
_IDLE_MIN, _IDLE_MAX = 1, 1440  # 空闲阈值（分钟）上下限

_store_lock = threading.Lock()

# 默认配置（vpn.json 缺失/损坏时回落）
_DEFAULT_STORE = {
    "version": 1,
    "enabled": False,     # VPN 转发总开关（关闭时核心停止、订阅 404）
    "portForward": True,  # 策略：端口转发
    "globalProxy": False,  # 策略：全局代理
    "lowPower": True,     # 低占用模式（默认勾选）
    "idleMinutes": 15,    # 低占用空闲阈值（分钟）
    "mixedPort": 7890,    # 全局代理 mixed 端口（HTTP+SOCKS5）
    "controllerPort": 9097,  # 核心 external-controller 端口（仅 127.0.0.1）
    "secret": "",         # 核心 API secret（自动生成）
    "subToken": "",       # 订阅令牌（自动生成）
    "binPath": "",        # 核心可执行文件（空 = 自动探测）
    "nodes": [],          # VPN 节点列表
    "forwards": [],       # 端口转发条目列表
}


# ---------------------------------------------------------------------------
# 配置存储（原子写 + 深合并默认值）
# ---------------------------------------------------------------------------
def _load() -> dict:
    if not os.path.exists(VPN_FILE):
        return json.loads(json.dumps(_DEFAULT_STORE))
    try:
        with open(VPN_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return json.loads(json.dumps(_DEFAULT_STORE))
    merged = json.loads(json.dumps(_DEFAULT_STORE))
    if isinstance(data, dict):
        for k, v in data.items():
            merged[k] = v
    return merged


def _save(data: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = VPN_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, VPN_FILE)


def get_store() -> dict:
    """读取完整配置（含节点 / 转发条目）。"""
    with _store_lock:
        return _load()


def _ensure_secrets(store: dict) -> dict:
    """首次使用时补齐订阅令牌与核心 API secret（32 字节 url-safe 随机值）。"""
    changed = False
    if not store.get("subToken"):
        store["subToken"] = secrets.token_urlsafe(32)
        changed = True
    if not store.get("secret"):
        store["secret"] = secrets.token_urlsafe(24)
        changed = True
    if changed:
        _save(store)
    return store


# ---------------------------------------------------------------------------
# 字段校验（白名单，防注入）
# ---------------------------------------------------------------------------
def _clean_text(v, field: str, max_len: int = 256, required: bool = False) -> str:
    s = str(v or "").strip()
    if required and not s:
        raise ValueError(f"{field} 不能为空")
    if s and (len(s) > max_len or not _TOKEN_RE.match(s)):
        raise ValueError(f"{field} 含非法字符或超长")
    return s


def _clean_node(raw: dict) -> dict:
    """校验并规范化一个 VPN 节点（渲染 Clash proxies 前调用）。"""
    if not isinstance(raw, dict):
        raise ValueError("节点数据非法")
    ntype = str(raw.get("type") or "").strip()
    if ntype not in NODE_TYPES:
        raise ValueError(f"不支持的节点类型：{ntype or '(空)'}")
    name = _clean_text(raw.get("name"), "节点名称", 64, required=True)
    server = str(raw.get("server") or "").strip()
    if not server or len(server) > 255 or not _HOST_RE.match(server):
        raise ValueError(f"节点「{name}」服务器地址非法")
    try:
        port = int(raw.get("port") or 0)
    except (TypeError, ValueError):
        raise ValueError(f"节点「{name}」端口非法")
    if not (1 <= port <= 65535):
        raise ValueError(f"节点「{name}」端口须在 1-65535")
    node = {
        "id": str(raw.get("id") or secrets.token_hex(5))[:32],
        "name": name,
        "type": ntype,
        "server": server,
        "port": port,
        "udp": bool(raw.get("udp")),
        "tls": bool(raw.get("tls")),
        "skipCertVerify": bool(raw.get("skipCertVerify")),
    }
    for field in ("uuid", "password", "cipher", "username", "sni", "flow"):
        v = _clean_text(raw.get(field), field)
        if v:
            node[field] = v
    for field in _NODE_REQUIRED[ntype]:
        if not node.get(field):
            raise ValueError(f"节点「{name}」缺少 {field}")
    return node


def _clean_forward(raw: dict, listen_ports: set) -> dict:
    """校验并规范化一条端口转发条目（渲染 listeners 前调用）。"""
    if not isinstance(raw, dict):
        raise ValueError("转发条目数据非法")
    name = _clean_text(raw.get("name"), "转发名称", 64)
    target = str(raw.get("targetHost") or "").strip()
    if not target or len(target) > 255 or not _HOST_RE.match(target):
        raise ValueError("转发目标地址非法")
    try:
        listen_port = int(raw.get("listenPort") or 0)
        target_port = int(raw.get("targetPort") or 0)
    except (TypeError, ValueError):
        raise ValueError("转发端口非法")
    if not (_PORT_MIN <= listen_port <= _PORT_MAX):
        raise ValueError(f"监听端口须在 {_PORT_MIN}-{_PORT_MAX}")
    if not (1 <= target_port <= 65535):
        raise ValueError("目标端口须在 1-65535")
    if listen_port in listen_ports:
        raise ValueError(f"监听端口 {listen_port} 重复")
    listen_ports.add(listen_port)
    return {
        "id": str(raw.get("id") or secrets.token_hex(5))[:32],
        "name": name or f"fw-{listen_port}",
        "listenPort": listen_port,
        "targetHost": target,
        "targetPort": target_port,
        "viaProxy": bool(raw.get("viaProxy")),
    }


def validate_store(store: dict) -> dict:
    """全量校验并返回规范化后的配置（节点 / 转发 / 端口冲突 / 阈值）。"""
    store["nodes"] = [_clean_node(n) for n in (store.get("nodes") or [])]
    # 节点名唯一（Clash 内按名称索引代理）
    seen = set()
    for n in store["nodes"]:
        if n["name"] in seen:
            raise ValueError(f"节点名称重复：{n['name']}")
        seen.add(n["name"])
    listen_ports = set()
    store["forwards"] = [_clean_forward(f, listen_ports) for f in (store.get("forwards") or [])]
    # 端口范围与冲突校验
    try:
        mixed = int(store.get("mixedPort") or 0)
        controller = int(store.get("controllerPort") or 0)
    except (TypeError, ValueError):
        raise ValueError("端口配置非法")
    if not (_PORT_MIN <= mixed <= _PORT_MAX) or not (_PORT_MIN <= controller <= _PORT_MAX):
        raise ValueError(f"mixed/controller 端口须在 {_PORT_MIN}-{_PORT_MAX}")
    if mixed == controller:
        raise ValueError("mixed 端口与 controller 端口不能相同")
    for f in store["forwards"]:
        if f["listenPort"] in (mixed, controller):
            raise ValueError(f"监听端口 {f['listenPort']} 与面板保留端口冲突")
    try:
        idle = int(store.get("idleMinutes") or 0)
    except (TypeError, ValueError):
        raise ValueError("空闲阈值非法")
    if not (_IDLE_MIN <= idle <= _IDLE_MAX):
        raise ValueError(f"空闲阈值须在 {_IDLE_MIN}-{_IDLE_MAX} 分钟")
    store["idleMinutes"] = idle
    store["mixedPort"] = mixed
    store["controllerPort"] = controller
    store["binPath"] = _clean_text(store.get("binPath"), "核心路径", 512)
    return store


def update_store(patch: dict) -> dict:
    """合并更新配置：校验通过才落盘；返回新配置。"""
    with _store_lock:
        store = _load()
        for k in ("enabled", "portForward", "globalProxy", "lowPower"):
            if k in patch:
                store[k] = bool(patch[k])
        for k in ("idleMinutes", "mixedPort", "controllerPort"):
            if k in patch:
                store[k] = patch[k]
        for k in ("nodes", "forwards"):
            if k in patch:
                store[k] = patch[k]
        if "binPath" in patch:
            store["binPath"] = str(patch.get("binPath") or "").strip()
        store = validate_store(store)
        _ensure_secrets(store)
        _save(store)
        return store


def rotate_sub_token() -> str:
    """重新生成订阅令牌（旧链接立即失效）。"""
    with _store_lock:
        store = _load()
        store["subToken"] = secrets.token_urlsafe(32)
        _save(store)
        return store["subToken"]


def check_sub_token(token: str) -> bool:
    """订阅令牌常量时间比对（防时序侧信道）。"""
    store = get_store()
    expect = store.get("subToken") or ""
    if not expect or not token:
        return False
    return hmac.compare_digest(expect, str(token)[:256])


# ---------------------------------------------------------------------------
# Clash 配置渲染（订阅 YAML 与核心配置共用同一套 proxies/rules）
# ---------------------------------------------------------------------------
def _node_to_clash(n: dict) -> dict:
    """节点记录 → Clash proxies 条目（按类型映射字段）。"""
    base = {"name": n["name"], "type": n["type"], "server": n["server"], "port": n["port"]}
    t = n["type"]
    if t == "ss":
        base["cipher"] = n.get("cipher") or "aes-256-gcm"
        base["password"] = n.get("password") or ""
        base["udp"] = bool(n.get("udp"))
    elif t == "vmess":
        base["uuid"] = n.get("uuid") or ""
        base["alterId"] = 0
        base["cipher"] = "auto"
        base["udp"] = bool(n.get("udp"))
        if n.get("tls"):
            base["tls"] = True
            base["servername"] = n.get("sni") or n["server"]
    elif t == "vless":
        base["uuid"] = n.get("uuid") or ""
        base["network"] = "tcp"
        base["udp"] = bool(n.get("udp"))
        if n.get("flow"):
            base["flow"] = n["flow"]
        if n.get("tls"):
            base["tls"] = True
            base["servername"] = n.get("sni") or n["server"]
    elif t == "trojan":
        base["password"] = n.get("password") or ""
        base["udp"] = bool(n.get("udp"))
        if n.get("sni"):
            base["sni"] = n["sni"]
    elif t == "hysteria2":
        base["password"] = n.get("password") or ""
        if n.get("sni"):
            base["sni"] = n["sni"]
    elif t == "socks5":
        if n.get("username"):
            base["username"] = n["username"]
        if n.get("password"):
            base["password"] = n["password"]
        base["udp"] = bool(n.get("udp"))
        if n.get("tls"):
            base["tls"] = True
    elif t == "http":
        if n.get("username"):
            base["username"] = n["username"]
        if n.get("password"):
            base["password"] = n["password"]
        if n.get("tls"):
            base["tls"] = True
    if n.get("skipCertVerify") and t in ("vmess", "vless", "trojan", "hysteria2", "socks5", "http"):
        base["skip-cert-verify"] = True
    return base


def build_subscription_config(store: dict) -> dict:
    """生成兼容 Clash 订阅格式的配置 dict（订阅接口 / 核心配置共用）。"""
    proxies = [_node_to_clash(n) for n in store.get("nodes", [])]
    names = [p["name"] for p in proxies]
    cfg = {
        "mixed-port": int(store.get("mixedPort") or 7890),
        "allow-lan": False,
        "mode": "rule",
        "log-level": "info",
        "proxies": proxies,
    }
    if names:
        cfg["proxy-groups"] = [
            {
                "name": GROUP_MAIN,
                "type": "select",
                "proxies": [GROUP_AUTO] + names + ["DIRECT"],
            },
            {
                "name": GROUP_AUTO,
                "type": "url-test",
                "proxies": names,
                "url": "http://www.gstatic.com/generate_204",
                "interval": 300,
                "tolerance": 50,
            },
        ]
    rules = []
    if store.get("portForward"):
        for f in store.get("forwards", []):
            # 端口转发策略：仅这些端口走 VPN（其余 MATCH 兜底决定）
            rules.append(f"DST-PORT,{f['listenPort']},{GROUP_MAIN if names else 'DIRECT'}")
    if store.get("globalProxy") and names:
        rules.append(f"MATCH,{GROUP_MAIN}")
    else:
        rules.append("MATCH,DIRECT")
    cfg["rules"] = rules
    return cfg


def build_core_config(store: dict) -> dict:
    """生成核心进程配置：订阅配置 + external-controller + listeners（端口转发）。"""
    cfg = build_subscription_config(store)
    cfg["external-controller"] = f"127.0.0.1:{int(store.get('controllerPort') or 9097)}"
    cfg["secret"] = store.get("secret") or ""
    listeners = []
    has_group = bool(cfg.get("proxy-groups"))
    for i, f in enumerate(store.get("forwards", [])):
        # viaProxy 且存在 VPN 组时经组转发，否则直连目标
        listeners.append({
            "name": f"graw-fw-{i}",
            "type": "tunnel",
            "port": f["listenPort"],
            "target": f"{f['targetHost']}:{f['targetPort']}",
            "proxy": GROUP_MAIN if (f.get("viaProxy") and has_group) else "DIRECT",
        })
    if listeners:
        cfg["listeners"] = listeners
    return cfg


def render_yaml(cfg: dict) -> str:
    """dict → Clash 兼容 YAML 文本。"""
    return yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False, default_flow_style=False)


# ---------------------------------------------------------------------------
# 核心进程管理（仅面板所在主机；同 frp：探测二进制 + 渲染配置 + 托管进程）
# ---------------------------------------------------------------------------
_proc: Optional[subprocess.Popen] = None
_proc_lock = threading.Lock()
_stop_reason = "manual"        # 最近一次关停原因：manual / idle / disabled / error
_last_active = time.time()     # 最近活跃时间（低占用看门狗用）
_last_start_ts = 0.0           # 最近一次启动尝试（防崩溃循环）
_last_totals = (0, 0)          # 核心累计上下行流量（探测增量活跃）


def detect_binary() -> str:
    """探测核心可执行文件：优先用户指定 binPath，其次 PATH 中的 mihomo/clash。"""
    store = get_store()
    custom = (store.get("binPath") or "").strip()
    if custom:
        return custom
    for name in CORE_BIN_NAMES:
        path = shutil.which(name)
        if path:
            return path
    return ""


def _controller_base() -> str:
    store = get_store()
    return f"http://127.0.0.1:{int(store.get('controllerPort') or 9097)}"


def _controller_get(path: str, timeout: float = 2.0):
    """调用核心 external-controller REST API（带 secret）。失败返回 None。"""
    store = get_store()
    try:
        r = requests.get(
            f"{_controller_base()}{path}",
            headers={"Authorization": f"Bearer {store.get('secret') or ''}"},
            timeout=timeout,
        )
        if r.status_code == 200:
            return r.json()
    except (requests.RequestException, ValueError) as e:
        logger.debug("核心控制接口请求失败: %s", e)
    return None


def core_running() -> bool:
    with _proc_lock:
        return _proc is not None and _proc.poll() is None


def _kill_orphans() -> None:
    """清理历史遗留的核心进程（面板重启后句柄丢失但进程仍在跑旧配置）。

    依据：命令行中包含我们的核心配置文件路径。避免新核心因端口被占而启动失败。
    """
    me = _proc.pid if _proc is not None else None
    try:
        import psutil

        for p in psutil.process_iter(["pid", "cmdline"]):
            if me is not None and p.info["pid"] == me:
                continue
            cmdline = p.info.get("cmdline") or []
            if any(CORE_CFG_FILE in str(part) for part in cmdline):
                try:
                    p.terminate()
                    p.wait(timeout=5)
                    logger.info("已清理遗留 VPN 核心进程 pid=%s", p.info["pid"])
                except (psutil.NoSuchProcess, psutil.AccessDenied, subprocess.TimeoutExpired) as e:
                    logger.debug("结束遗留核心进程失败，尝试强制结束: %s", e)
                    try:
                        p.kill()
                    except (psutil.NoSuchProcess, psutil.AccessDenied) as e2:
                        logger.debug("强制结束遗留核心进程失败: %s", e2)
    except ImportError:
        logger.debug("psutil 不可用，跳过遗留核心进程清理")
    except Exception as e:  # 枚举进程失败不阻塞启动（lgtm[py/empty-except] 尽力而为）
        logger.debug("清理遗留核心进程失败: %s", e)


def start_core(reason: str = "manual") -> dict:
    """启动（或重启）核心进程：渲染配置 → 清理遗留 → 拉起 → 健康探测。"""
    global _proc, _last_start_ts, _last_active, _stop_reason, _last_totals
    store = _ensure_secrets(get_store())
    if not store.get("enabled"):
        raise ValueError("VPN 转发未启用，请先在界面开启总开关")
    binary = detect_binary()
    if not binary:
        raise ValueError("未检测到 mihomo/clash 核心，请安装或在界面指定路径")
    # 幂等：已运行则先停（配置变更后的重启走同一路径）
    stop_core("restart", silent=True)
    _kill_orphans()
    cfg = build_core_config(store)
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = CORE_CFG_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(render_yaml(cfg))
    os.replace(tmp, CORE_CFG_FILE)
    log_f = open(CORE_LOG_FILE, "ab")
    try:
        kwargs = {}
        if IS_WIN:
            kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            kwargs["start_new_session"] = True
        _proc = subprocess.Popen(
            [binary, "-f", CORE_CFG_FILE, "-d", DATA_DIR],
            stdout=log_f, stderr=subprocess.STDOUT, **kwargs,
        )
    except OSError as e:
        log_f.close()
        raise ValueError(f"核心启动失败：{e}")
    finally:
        log_f.close()  # Popen 已继承句柄，父进程侧即刻释放
    _last_start_ts = time.time()
    _last_active = time.time()
    _stop_reason = ""
    _last_totals = (0, 0)
    # 健康探测：进程存活 + external-controller 就绪（最多 ~5s）
    ok = False
    for _ in range(10):
        if not core_running():
            break
        if _controller_get("/version", timeout=1.0) is not None:
            ok = True
            break
        time.sleep(0.5)
    if not core_running():
        _stop_reason = "error"
        code = _proc.poll() if _proc is not None else -1
        logger.error("VPN 核心异常退出（exit=%s，reason=%s），详情见 vpn_core.log", code, _stop_reason)
        raise ValueError(f"核心进程异常退出（exit={code}），详情见 vpn_core.log")
    logger.info("VPN 核心已启动（reason=%s, pid=%s, controller=%s）", reason, _proc.pid, ok)
    return get_status()


def stop_core(reason: str = "manual", silent: bool = False) -> bool:
    """停止核心进程（含遗留进程兜底清理）。"""
    global _proc, _stop_reason
    stopped = False
    with _proc_lock:
        p = _proc
        _proc = None
    if p is not None and p.poll() is None:
        try:
            import psutil

            proc = psutil.Process(p.pid)
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            stopped = True
        except ImportError:
            p.terminate()
            stopped = True
        except Exception:  # 进程已自行退出等场景：以 stop 后的 poll 为准
            stopped = p.poll() is None
    _kill_orphans()
    _stop_reason = reason
    if not silent:
        logger.info("VPN 核心已停止（reason=%s）", reason)
    return stopped


def touch_activity() -> None:
    """记录一次 VPN 活跃（订阅拉取 / wake / 启动等），重置低占用空闲计时。"""
    global _last_active
    _last_active = time.time()


def probe_activity() -> dict:
    """探测核心活跃度：active = 有并发连接 或 流量较上次有增量。"""
    global _last_totals
    data = _controller_get("/connections", timeout=2.0)
    if not isinstance(data, dict):
        return {"reachable": False, "active": False, "conns": 0}
    conns = data.get("connections") or []
    totals = (int(data.get("downloadTotal") or 0), int(data.get("uploadTotal") or 0))
    active = bool(conns) or totals != _last_totals
    _last_totals = totals
    logger.debug("VPN 核心累计流量已更新: %s", _last_totals)
    return {"reachable": True, "active": active, "conns": len(conns)}


def get_status() -> dict:
    """对外可见的运行状态（窗口轮询用）。"""
    store = get_store()
    binary = detect_binary()
    running = core_running()
    idle_seconds = max(0, int(time.time() - _last_active))
    status = "stopped"
    if not store.get("enabled"):
        status = "disabled"
    elif running:
        status = "running"
    elif _stop_reason == "idle":
        status = "sleeping"   # 低占用休眠：下次初次请求自动唤醒
    return {
        "status": status,
        "running": running,
        "stopReason": _stop_reason,
        "pid": _proc.pid if (_proc is not None and running) else None,
        "binary": binary,
        "lastActive": _last_active,
        "idleSeconds": idle_seconds,
        "idleMinutes": store.get("idleMinutes"),
        "lowPower": bool(store.get("lowPower")),
        "enabled": bool(store.get("enabled")),
        "portForward": bool(store.get("portForward")),
        "globalProxy": bool(store.get("globalProxy")),
        "mixedPort": store.get("mixedPort"),
        "nodeCount": len(store.get("nodes", [])),
        "forwardCount": len(store.get("forwards", [])),
    }


# ---------------------------------------------------------------------------
# 低占用看门狗 + 启动恢复（lifespan 配对启停）
# ---------------------------------------------------------------------------
_wd_task: Optional[asyncio.Task] = None


def _watchdog_tick() -> None:
    """单次看门狗检查：活跃刷新计时；空闲超阈值关停；非低占用自愈拉起。"""
    global _last_active
    store = get_store()
    if not store.get("enabled"):
        return
    low_power = bool(store.get("lowPower"))
    idle_limit = max(_IDLE_MIN, int(store.get("idleMinutes") or 15)) * 60
    if core_running():
        act = probe_activity()
        if act.get("active"):
            _last_active = time.time()
            return
        if low_power and (time.time() - _last_active) > idle_limit:
            logger.info("VPN 核心空闲超过 %s 分钟，低占用模式自动关闭", store.get("idleMinutes"))
            stop_core("idle")
        return
    # 未运行：低占用下保持休眠等待初次请求；非低占用常驻 → 自愈拉起（限频防崩溃循环）
    if not low_power and (time.time() - _last_start_ts) > 60:
        try:
            start_core(reason="keepalive")
        except (ValueError, OSError) as e:
            logger.warning("VPN 核心自愈启动失败: %s", e)


async def _watchdog_loop() -> None:
    while True:
        loop = asyncio.get_running_loop()
        await asyncio.sleep(30)
        try:
            await loop.run_in_executor(None, _watchdog_tick)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # 单轮失败仅记录，不影响下轮（lgtm[py/empty-except]）
            logger.debug("VPN 看门狗异常: %s", e)


async def start_monitor() -> None:
    """启动低占用看门狗协程（lifespan 调用）。"""
    global _wd_task, _last_active
    _last_active = time.time()
    if _wd_task is None or _wd_task.done():
        _wd_task = asyncio.create_task(_watchdog_loop())


async def stop_monitor() -> None:
    """停止看门狗；面板退出时停掉核心进程，避免留下孤儿进程。"""
    global _wd_task
    if _wd_task is not None:
        _wd_task.cancel()
        try:
            await _wd_task
        except (asyncio.CancelledError, Exception) as e:
            logger.debug("VPN 看门狗任务已结束: %s", e)
        _wd_task = None
    stop_core("shutdown", silent=True)


def restore() -> None:
    """面板启动时恢复核心：enabled 且非常驻低占用 → 直接拉起。

    低占用模式下不随面板启动（等待首次请求唤醒），体现「空闲即关停」语义。
    """
    store = _ensure_secrets(get_store())
    if not store.get("enabled"):
        return
    if store.get("lowPower"):
        logger.info("VPN 转发为低占用模式，核心待首次请求唤醒")
        return
    try:
        start_core(reason="restore")
    except (ValueError, OSError) as e:
        logger.warning("VPN 核心启动恢复失败: %s", e)
