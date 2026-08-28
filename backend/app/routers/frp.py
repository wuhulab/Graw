# -*- coding: utf-8 -*-
"""
frp.py - Frp（内网穿透）管理

功能概述：
  面板假设宿主机上**已经安装妥当** frp（含可执行文件 frps / frpc），这里只负责
  让它变得「容易配置」：用一个 Web 窗口可视化编辑 frps（服务端）或 frpc
  （客户端）的 TOML 配置，维护 frpc 的代理（proxies）列表，并提供进程
  （启动 / 停止 / 重启 / 状态探测）。

设计约定：
  - 面板侧配置记忆在 backend/data/frp.json（mode、可执行文件路径、非代理项、
    代理列表）。frps.toml / frpc.toml 由后端根据该 json **渲染**后写到宿主机
    （默认 /etc/frp/frps.toml、/etc/frp/frpc.toml，可覆盖为自定义路径）。
  - 只渲染「已启用」的代理；禁用的代理从 toml 中剔除但保留在 json（方便随时
    重新启用）。代理增删改 / 配置保存后都会自动重写 toml，进程重启后生效。
  - 进程管理作用于「当前管理主机」（node_manager 的 host_cmd/host_shell 多节点
    感知）：Linux 优先用 systemd（frps/frpc unit），无 unit 时回退 nohup/pkill
    后台运行；Windows 用 tasklist / taskkill 与本机 Popen 后台启动。

安全：
  - 所有写接口挂在 ADMIN 权限（main.py 注册时依赖 ADMIN）。
  - 模式 / 端口 / 代理 / token / 域名等字段白名单校验，拒绝换行与控制字符，
    防止向最终 shell / systemd / toml 中注入额外配置或命令。
  - configPath 必须经过 _validate_config_path 约束在 FRP_CONFIG_DIR
    （/etc/frp）目录内，realpath 解析后禁止 ".." 穿越与绝对路径逃逸，
    杜绝已认证管理员借 configPath 对任意可写路径的任意文件写入。
"""

import json
import logging
import os
import asyncio
import platform
import re
import shlex
import subprocess
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app import node_manager

logger = logging.getLogger("graw.frp")

router = APIRouter()

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
FRP_FILE = os.path.join(DATA_DIR, "frp.json")
IS_WIN = platform.system() == "Windows"

# ---------- 常量与默认配置 ----------
# frp 可执行文件（当前节点 PATH 中探测）
SERVER_BIN_NAMES = ["frps"]
CLIENT_BIN_NAMES = ["frpc"]
# 默认配置文件路径（Linux 常用；Windows 无默认则落到用户自定义）
DEFAULT_SERVER_CONFIG = "/etc/frp/frps.toml"
DEFAULT_CLIENT_CONFIG = "/etc/frp/frpc.toml"
# 后台运行日志
DEFAULT_LOG_PATH = "/var/log/frp_{mode}.log"
# 配置文件允许写入的「基目录」：自定义 configPath 解析后必须落在此目录内，
# 防止任意文件写入（见 _validate_config_path）。与默认配置路径保持一致，
# 故 Linux / Windows 下默认行为不受影响。
FRP_CONFIG_DIR = "/etc/frp"

# 代理名：frp 要求代理名只能由字母数字 _ - 组成、不能包含空格与点
_PROXY_NAME_RE = re.compile(r"^[A-Za-z0-9_\-]{1,64}$")
# 域名白名单（允许 * 通配子域名，如 *.example.com）：逐段校验
_DOMAIN_SEG_RE = re.compile(r"^[A-Za-z0-9*]([A-Za-z0-9\-]*[A-Za-z0-9])?$")
# 允许的代理类型
VALID_PROXY_TYPES = ("tcp", "udp", "http", "https")

# frp.json 的默认结构（不含任何运行态信息）
_DEFAULT_STORE = {
    "mode": "server",  # "server" | "client"
    "serverBin": "",  # 空 → 自动探测 frps
    "clientBin": "",  # 空 → 自动探测 frpc
    "server": {
        "configPath": "",
        "bindAddr": "0.0.0.0",
        "bindPort": 7000,
        "token": "",
        "dashboardAddr": "127.0.0.1",
        "dashboardPort": 0,
        "dashboardUser": "admin",
        "dashboardPwd": "",
        "logLevel": "info",
    },
    "client": {
        "serverAddr": "",
        "serverPort": 7000,
        "token": "",
        "configPath": "",
        "loginFailExit": True,
        "logLevel": "info",
        "proxies": [],
    },
}


# ---------- 存储读写 ----------
def _load_store() -> dict:
    """读取 frp.json（面板侧配置记忆），损坏或缺失时回落默认。"""
    if not os.path.exists(FRP_FILE):
        return json.loads(json.dumps(_DEFAULT_STORE))
    try:
        with open(FRP_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        # 深合并默认结构，保证新增字段也有默认值
        merged = json.loads(json.dumps(_DEFAULT_STORE))
        if isinstance(data, dict):
            for k, v in data.items():
                if k in merged and isinstance(v, dict) and isinstance(merged[k], dict):
                    merged[k].update(v)
                else:
                    merged[k] = v
        return merged
    except Exception as exc:  # pragma: no cover
        logger.warning("frp.json 读取失败，回落默认配置: %s", exc)
        return json.loads(json.dumps(_DEFAULT_STORE))


def _save_store(data: dict) -> None:
    """原子写 frp.json。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = FRP_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, FRP_FILE)


# ---------- 校验工具 ----------
def _reject_ctrl(value: str, field: str) -> str:
    """拒绝控制字符（含换行 / 空字节），防止向 toml / shell / systemd 注入。"""
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise HTTPException(status_code=400, detail=f"{field} 不能包含控制字符或换行")
    return value.strip()


def _validate_config_path(value: str, field: str) -> str:
    """校验 frp 配置文件路径，禁止任意文件写入（CWE-73 / CWE-22）。

    此前 configPath 仅做长度与控制字符校验，已认证管理员可将其指向任意可写
    路径，使 _write_toml 把攻击者可控的 TOML 写到系统任意位置（如覆盖系统
    配置、植入内容等）。现要求：

      - 空字符串表示使用默认路径（DEFAULT_SERVER_CONFIG / DEFAULT_CLIENT_CONFIG），
        直接放行；
      - 非空时必须为绝对路径；
      - 拒绝 Windows 设备命名空间前缀（\\\\?\\\\ / \\\\.\\\\）；
      - 经 os.path.realpath 解析（消除符号链接与 ".." 穿越）后，必须仍位于
        FRP_CONFIG_DIR 目录内（含其自身），否则拒绝。

    这样即使传入 "/etc/frp/../../tmp/x.toml" 之类的逃逸路径，realpath 解析后
    也会落在 FRP_CONFIG_DIR 之外而被拦截；默认路径与合法子路径不受影响。
    """
    value = (value or "").strip()
    if not value:
        return ""
    value = _reject_ctrl(value, field)  # 先拒绝控制字符 / 换行
    if value.startswith("\\\\?\\") or value.startswith("\\\\.\\"):
        raise HTTPException(
            status_code=400, detail=f"{field} 包含非法的设备命名空间前缀"
        )
    if not os.path.isabs(value):
        raise HTTPException(status_code=400, detail=f"{field} 必须为绝对路径")
    try:
        base = os.path.realpath(FRP_CONFIG_DIR)
        real = os.path.realpath(value)
    except Exception as exc:  # pragma: no cover - 路径解析异常统一拒绝
        logger.warning("configPath realpath 失败 %s: %s", value, exc)
        raise HTTPException(status_code=400, detail=f"{field} 路径解析失败")
    if real != base and not real.startswith(base + os.sep):
        raise HTTPException(
            status_code=400,
            detail=f"{field} 必须位于 {FRP_CONFIG_DIR} 目录内（禁止路径穿越 / 逃逸）",
        )
    return value


def _check_port(value: int, field: str) -> int:
    """端口范围校验（1-65535）。"""
    if not isinstance(value, int) or value < 1 or value > 65535:
        raise HTTPException(status_code=400, detail=f"{field} 必须为 1-65535 的端口号")
    return value


def _check_proxy_name(value: str) -> str:
    name = _reject_ctrl(value or "", "代理名")
    if not _PROXY_NAME_RE.match(name):
        raise HTTPException(
            status_code=400,
            detail="代理名仅允许 1-64 位字母 / 数字 / 下划线 / 连字符",
        )
    return name


def _check_domains(domains: str) -> str:
    """校验 customDomains（逗号分隔，允许 * 通配子域名）。"""
    domains = _reject_ctrl(domains or "", "自定义域名")
    if not domains:
        return domains
    parts = [p.strip() for p in domains.split(",") if p.strip()]
    for p in parts:
        segments = p.split(".")
        for i, seg in enumerate(segments):
            if not seg:
                raise HTTPException(status_code=400, detail=f"域名格式非法: {p}")
            # 通配符 * 只允许作为域名最左段的整段（如 *.example.com）
            if "*" in seg:
                if seg != "*" or i != 0:
                    raise HTTPException(
                        status_code=400, detail=f"域名通配符 * 仅允许在最左段: {p}"
                    )
            if not _DOMAIN_SEG_RE.match(seg):
                raise HTTPException(status_code=400, detail=f"域名格式非法: {p}")
    return ",".join(parts)


# ---------- TOML 渲染 ----------
def _toml_str(value: str) -> str:
    """把字符串安全地写成 TOML 基本字符串（转义反斜杠与引号）。"""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _render_server(s: dict) -> str:
    """根据服务端配置渲染 frps.toml。"""
    lines = []
    lines.append('# generated by Graw frp manager')
    lines.append(f'bindAddr = {_toml_str(s.get("bindAddr") or "0.0.0.0")}')
    lines.append(f'bindPort = {int(s.get("bindPort") or 7000)}')
    token = s.get("token") or ""
    if token:
        lines.append('')
        lines.append('auth.method = "token"')
        lines.append(f'auth.token = {_toml_str(token)}')
    log_level = s.get("logLevel") or "info"
    log_to = DEFAULT_LOG_PATH.format(mode="server")

    dashboard_port = int(s.get("dashboardPort") or 0)
    if dashboard_port > 0:
        lines.append('')
        lines.append(f'webServer.addr = {_toml_str(s.get("dashboardAddr") or "127.0.0.1")}')
        lines.append(f'webServer.port = {dashboard_port}')
        if s.get("dashboardUser"):
            lines.append(f'webServer.user = {_toml_str(s["dashboardUser"])}')
        if s.get("dashboardPwd"):
            lines.append(f'webServer.password = {_toml_str(s["dashboardPwd"])}')

    lines.append('')
    lines.append(f'log.level = {_toml_str(log_level)}')
    lines.append(f'log.to = {_toml_str(log_to)}')
    return "\n".join(lines) + "\n"


def _render_proxy(p: dict) -> str:
    """渲染单个代理块 [[proxies]]。"""
    ptype = p.get("type") or "tcp"
    lines = [f'name = {_toml_str(p["name"])}', f'type = "{ptype}"']
    lines.append(f'localIP = {_toml_str(p.get("localIp") or "127.0.0.1")}')
    lines.append(f'localPort = {int(p.get("localPort") or 0)}')
    if ptype in ("tcp", "udp"):
        lines.append(f'remotePort = {int(p.get("remotePort") or 0)}')
    elif ptype in ("http", "https"):
        lines.append(f'customDomains = [{", ".join(_toml_str(d) for d in (p.get("customDomains") or "").split(",") if d.strip())}]')
    if p.get("useEncryption"):
        lines.append("transport.useEncryption = true")
    if p.get("useCompression"):
        lines.append("transport.useCompression = true")
    return "[[proxies]]\n" + "\n".join(lines)


def _render_client(c: dict) -> str:
    """根据客户端配置渲染 frpc.toml（仅含已启用的代理）。"""
    lines = []
    lines.append('# generated by Graw frp manager')
    lines.append(f'serverAddr = {_toml_str(c.get("serverAddr") or "")}')
    lines.append(f'serverPort = {int(c.get("serverPort") or 7000)}')
    token = c.get("token") or ""
    if token:
        lines.append('')
        lines.append('auth.method = "token"')
        lines.append(f'auth.token = {_toml_str(token)}')
    lines.append(f'loginFailExit = {str(bool(c.get("loginFailExit", True))).lower()}')
    lines.append(f'log.level = {_toml_str(c.get("logLevel") or "info")}')
    lines.append(f'log.to = {_toml_str(DEFAULT_LOG_PATH.format(mode="client"))}')
    enabled = [p for p in c.get("proxies", []) if p.get("enabled", True)]
    if enabled:
        lines.append('')
        for i, p in enumerate(enabled):
            if i:
                lines.append('')
            lines.append(_render_proxy(p))
    return "\n".join(lines) + "\n"


def _render(data: dict) -> str:
    """按当前模式渲染对应 toml 文本。"""
    if data.get("mode") == "client":
        return _render_client(data.get("client", {}))
    return _render_server(data.get("server", {}))


def _config_path(data: dict) -> str:
    """返回当前模式对应的配置文件路径（未设置则取默认）。"""
    if data.get("mode") == "client":
        return (data.get("client", {}).get("configPath") or "").strip() or DEFAULT_CLIENT_CONFIG
    return (data.get("server", {}).get("configPath") or "").strip() or DEFAULT_SERVER_CONFIG


def _bin_for(data: dict) -> str:
    """解析当前模式应使用的 frp 可执行文件路径（找不到则回退存储值/可执行名）。

    优先顺序：面板存储的 serverBin/clientBin（路径存在时）→ PATH（host_which，
    Windows 下会尝试带 / 不带 .exe）→ 可执行文件名。
    让启动层据此给出「未找到可执行文件」的明确报错，而不是直接 Popen 抛
    WinError 2。
    """
    mode = data.get("mode", "server")
    name = "frps" if mode == "server" else "frpc"
    stored = (data.get("serverBin") if mode == "server" else data.get("clientBin") or "").strip()

    # 1) 存储的路径本身有效（Windows 自动补 .exe）。
    #    容器 /host 挂载模式下存储的是宿主机视角路径（如 /usr/local/bin/frps），
    #    必须映射到容器内实际路径（host_path）后才能做存在性检查。
    for cand in (stored, stored + ".exe" if (IS_WIN and stored) else stored):
        if cand and os.path.exists(node_manager.host_path(cand)):
            return cand
    # 2) 通过 PATH 探测（Windows 尝试带 / 不带 .exe）。
    #    host_which 在容器模式返回容器内实际路径（/host/...），本机可存在性检查；
    #    命中后统一还原为宿主机视角路径返回（后续 host_shell/chroot 命令按宿主机
    #    视角解释）；SSH 远程路径在本机不存在时按旧逻辑回退。
    for base in (name, name + ".exe") if IS_WIN else (name,):
        found = node_manager.host_which(base)
        if found and os.path.exists(found):
            return node_manager.unhost_path(found)
    # 3) 兜底：回退存储值或可执行名（由启动层给出友好报错）
    return stored or name


def _write_toml(data: dict) -> str:
    """渲染并写盘 toml 配置（写入当前主机的指定配置文件路径）。"""
    cfg = _config_path(data)
    toml = _render(data)
    parent = os.path.dirname(cfg) or "."
    try:
        # 确保目录存在（容器模式下 host_path 映射到挂载根，宿主可见）
        os.makedirs(node_manager.host_path(parent), exist_ok=True)
        node_manager.write_text(cfg, toml)
    except Exception as exc:  # pragma: no cover
        logger.error("写 frp 配置文件失败 %s", cfg, exc_info=True)
        raise HTTPException(status_code=500, detail=f"写配置文件失败: {cfg}: {exc}")
    return toml


# ---------- 进程管理 ----------
def _unit_exists(mode: str) -> bool:
    """Linux：systemd 是否注册了 frps / frpc 服务（存在则优先用 systemctl）。"""
    unit = "frps" if mode == "server" else "frpc"
    try:
        r = node_manager.host_cmd(
            ["systemctl", "cat", unit], capture_output=True, timeout=8
        )
        return r.returncode == 0
    except Exception:
        return False


def _running(data: dict) -> bool:
    """探测当前模式 frp 是否运行。"""
    mode = data.get("mode", "server")
    if IS_WIN:
        exe = "frps.exe" if mode == "server" else "frpc.exe"
        # Windows 本机用 tasklist 按镜像名匹配
        try:
            r = subprocess.run(
                ["tasklist", "/NH", "/FI", f"IMAGENAME eq {exe}"],
                capture_output=True,
                text=True,
                timeout=8,
            )
            return r.returncode == 0 and exe.lower() in r.stdout.lower()
        except Exception:
            self_proc = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 f"Get-Process {exe} -ErrorAction SilentlyContinue"],
                capture_output=True, text=True, timeout=8,
            )
            return bool(self_proc.stdout.strip())
    # Linux：优先 systemd 状态；无 unit 时用 pgrep 匹配可执行文件名
    if _unit_exists(mode):
        unit = "frps" if mode == "server" else "frpc"
        try:
            r = node_manager.host_cmd(
                ["systemctl", "is-active", unit], capture_output=True, timeout=8
            )
            return r.stdout.strip() == "active"
        except Exception:
            return False
    bin_name = os.path.basename(_bin_for(data)) or ("frps" if mode == "server" else "frpc")
    try:
        r = node_manager.host_cmd(
            ["pgrep", "-f", bin_name], capture_output=True, timeout=8
        )
        return r.returncode == 0 if r.stdout.strip() else False
    except Exception:
        return False


def _do_start(data: dict) -> tuple:
    """启动当前模式 frp。返回 (ok, message)。"""
    mode = data.get("mode", "server")
    cfg = _config_path(data)
    bin_path = _bin_for(data)
    log_path = DEFAULT_LOG_PATH.format(mode=mode)

    # Linux 优先 systemd
    if not IS_WIN and _unit_exists(mode):
        unit = "frps" if mode == "server" else "frpc"
        try:
            r = node_manager.host_cmd(
                ["systemctl", "start", unit], capture_output=True, text=True, timeout=15
            )
            if r.returncode == 0:
                return True, f"systemctl start {unit}"
            return False, (r.stderr or r.stdout or "systemctl 启动失败").strip()
        except Exception as exc:
            return False, f"systemctl 启动异常: {exc}"

    if IS_WIN:
        # Windows：本机后台启动（用系统原始 Python 启动，不经 chroot/远程）
        if not os.path.exists(bin_path):
            return False, (
                f"未找到 frp 可执行文件「{bin_path}」：请在配置中填写正确的 "
                f"{bin_path.split(os.sep)[-1] or 'frp'} 路径，或将 frp 目录加入系统 PATH"
            )
        try:
            log_dir = os.path.dirname(log_path) or "."
            os.makedirs(log_dir, exist_ok=True)
            with open(log_path, "a", encoding="utf-8") as logf:
                subprocess.Popen(
                    [bin_path, "-c", cfg],
                    stdout=logf, stderr=logf, stdin=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                )
            return True, f"已后台启动 {bin_path}"
        except FileNotFoundError as e:
            return False, f"未找到 frp 可执行文件「{bin_path}」: {e}"
        except Exception as exc:
            return False, f"启动失败: {exc}"

    # Linux 无 systemd：nohup 后台运行（经 host_cmd 感知容器/远程节点）
    shell = (
        f"mkdir -p {shlex.quote(os.path.dirname(log_path) or '.')} && "
        f"nohup {shlex.quote(bin_path)} -c {shlex.quote(cfg)} > {shlex.quote(log_path)} "
        f"2>&1 &"
    )
    try:
        r = node_manager.host_shell(shell, timeout=10)
        if r.returncode == 0 or r.returncode == 127:
            return True, f"已后台启动 {bin_path}"
        return False, (r.stderr or r.stdout or "nohup 启动失败").strip()
    except Exception as exc:
        return False, f"启动异常: {exc}"


def _do_stop(data: dict) -> tuple:
    """停止当前模式 frp。返回 (ok, message)。"""
    mode = data.get("mode", "server")
    if IS_WIN:
        exe = "frps.exe" if mode == "server" else "frpc.exe"
        try:
            r = subprocess.run(
                ["taskkill", "/F", "/IM", exe],
                capture_output=True, text=True, timeout=8,
            )
            if r.returncode == 0:
                return True, f"已停止 {exe}"
            return (True, f"未在运行或已停止") if "not found" in str(r.stdout).lower() else (False, (r.stderr or r.stdout or "停止失败").strip())
        except Exception as exc:
            return False, f"停止异常: {exc}"
    if _unit_exists(mode):
        unit = "frps" if mode == "server" else "frpc"
        try:
            r = node_manager.host_cmd(
                ["systemctl", "stop", unit], capture_output=True, text=True, timeout=15
            )
            if r.returncode == 0:
                return True, f"systemctl stop {unit}"
            return False, (r.stderr or r.stdout or "systemctl 停止失败").strip()
        except Exception as exc:
            return False, f"systemctl 停止异常: {exc}"
    bin_name = os.path.basename(_bin_for(data)) or ("frps" if mode == "server" else "frpc")
    try:
        r = node_manager.host_cmd(
            ["pkill", "-f", bin_name], capture_output=True, text=True, timeout=8
        )
        if r.returncode == 0:
            return True, f"已停止 {bin_name}"
        return True, "未检测到运行中的进程"
    except Exception as exc:
        return False, f"停止异常: {exc}"


# ---------- HTTP 模型 ----------
class ProxyModel(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    type: str = Field(default="tcp", pattern="^(tcp|udp|http|https)$")
    localIp: str = Field(default="127.0.0.1", max_length=64)
    localPort: int = Field(..., ge=1, le=65535)
    remotePort: Optional[int] = Field(default=None, ge=1, le=65535)
    customDomains: Optional[str] = Field(default="", max_length=512)
    useEncryption: bool = False
    useCompression: bool = False
    enabled: bool = True
    remark: Optional[str] = Field(default="", max_length=200)


class ServerConfigModel(BaseModel):
    configPath: str = Field(default="", max_length=512)
    bindAddr: str = Field(default="0.0.0.0", max_length=64)
    bindPort: int = Field(default=7000, ge=1, le=65535)
    token: str = Field(default="", max_length=256)
    dashboardAddr: str = Field(default="127.0.0.1", max_length=64)
    dashboardPort: int = Field(default=0, ge=0, le=65535)
    dashboardUser: str = Field(default="admin", max_length=64)
    dashboardPwd: str = Field(default="", max_length=128)
    logLevel: str = Field(default="info", pattern="^(trace|debug|info|warn|error)$")


class ClientConfigModel(BaseModel):
    serverAddr: str = Field(default="", max_length=128)
    serverPort: int = Field(default=7000, ge=1, le=65535)
    token: str = Field(default="", max_length=256)
    configPath: str = Field(default="", max_length=512)
    loginFailExit: bool = True
    logLevel: str = Field(default="info", pattern="^(trace|debug|info|warn|error)$")


class FrpConfigModel(BaseModel):
    mode: str = Field(default="server", pattern="^(server|client)$")
    serverBin: str = Field(default="", max_length=512)
    clientBin: str = Field(default="", max_length=512)
    server: ServerConfigModel = ServerConfigModel()
    client: ClientConfigModel = ClientConfigModel()


# ---------- 路由 ----------
@router.get("/status")
async def frp_status():
    """探测 frp 安装与运行状态（不暴露 token）。

    _running / _unit_exists 内部执行 systemctl / tasklist / pgrep 等阻塞
    subprocess，放线程池避免卡事件循环。
    """
    return await asyncio.to_thread(_frp_status_sync)


def _frp_status_sync() -> dict:
    data = _load_store()
    mode = data.get("mode", "server")
    bin_path = _bin_for(data)
    custom = (data.get("serverBin") or data.get("clientBin") or "").strip()
    return {
        "mode": mode,
        "installed": os.path.exists(bin_path),
        "running": _running(data),
        "configPath": _config_path(data),
        "binPath": bin_path,
        "systemdActive": False if IS_WIN else _unit_exists(mode),
        "platform": "windows" if IS_WIN else "linux",
    }


@router.get("/config")
async def get_config():
    """读取完整面板配置（含代理列表）。"""
    return _load_store()


@router.get("/preview")
async def preview_config():
    """返回按当前配置渲染的 toml 文本（预览用，不写盘）。"""
    return {"toml": _render(_load_store())}


@router.put("/config")
async def save_config(req: FrpConfigModel):
    """保存面板配置（模式 / 服务端或客户端项），自动重写 toml。"""
    # 字段级安全校验：配置路径做基目录约束（防任意文件写入），
    # token 拒绝换行与控制字符。
    cfg_server = _validate_config_path(req.server.configPath, "服务端配置路径")
    cfg_client = _validate_config_path(req.client.configPath, "客户端配置路径")
    token = _reject_ctrl(req.server.token, "服务端 token")
    _reject_ctrl(req.client.token, "客户端 token")

    data = _load_store()
    old_mode = data.get("mode", "server")
    data["mode"] = req.mode
    data["serverBin"] = _reject_ctrl(req.serverBin, "frps 路径")
    data["clientBin"] = _reject_ctrl(req.clientBin, "frpc 路径")

    data["server"] = {
        "configPath": cfg_server,
        "bindAddr": _reject_ctrl(req.server.bindAddr, "监听地址"),
        "bindPort": _check_port(req.server.bindPort, "服务端端口"),
        "token": token,
        "dashboardAddr": _reject_ctrl(req.server.dashboardAddr, "控制台地址"),
        "dashboardPort": _check_port(req.server.dashboardPort, "控制台端口") if req.server.dashboardPort > 0 else 0,
        "dashboardUser": _reject_ctrl(req.server.dashboardUser, "控制台账号"),
        "dashboardPwd": _reject_ctrl(req.server.dashboardPwd, "控制台密码"),
        "logLevel": req.server.logLevel,
    }
    data["client"] = {
        "serverAddr": _reject_ctrl(req.client.serverAddr, "服务端地址"),
        "serverPort": _check_port(req.client.serverPort, "连接端口"),
        "token": _reject_ctrl(req.client.token, "客户端 token"),
        "configPath": cfg_client,
        "loginFailExit": bool(req.client.loginFailExit),
        "logLevel": req.client.logLevel,
        "proxies": data["client"].get("proxies", []),
    }
    _save_store(data)
    _write_toml(data)
    logger.info("保存 frp 配置，模式=%s", req.mode)
    return _load_store()


@router.post("/mode")
async def switch_mode(body: dict):
    """切换 server / client 模式并重写 toml。"""
    mode = (body.get("mode") or "").strip()
    if mode not in ("server", "client"):
        raise HTTPException(status_code=400, detail="mode 必须为 server 或 client")
    data = _load_store()
    data["mode"] = mode
    _save_store(data)
    _write_toml(data)
    logger.info("切换 frp 模式为 %s", mode)
    return _load_store()


@router.post("/proxies")
async def add_proxy(req: ProxyModel):
    """新增 frpc 代理。"""
    name = _check_proxy_name(req.name)
    if req.type not in VALID_PROXY_TYPES:
        raise HTTPException(status_code=400, detail="不支持的类型")
    local_ip = _reject_ctrl(req.localIp, "本地地址")
    _check_port(req.localPort, "本地端口")
    remote_port = _check_port(req.remotePort, "远程端口") if req.remotePort else None
    domains = _check_domains(req.customDomains or "")
    if req.type in ("tcp", "udp") and remote_port is None:
        raise HTTPException(status_code=400, detail="tcp/udp 代理必须填写远程端口")
    if req.type in ("http", "https") and not domains:
        raise HTTPException(status_code=400, detail="http/https 代理必须填写自定义域名")

    data = _load_store()
    proxies = data.get("client", {}).get("proxies", [])
    if any(p.get("name") == name for p in proxies):
        raise HTTPException(status_code=400, detail=f"代理名已存在: {name}")
    proxy = {
        "id": str(uuid.uuid4())[:8],
        "name": name,
        "type": req.type,
        "localIp": local_ip,
        "localPort": req.localPort,
        "remotePort": remote_port,
        "customDomains": domains,
        "useEncryption": req.useEncryption,
        "useCompression": req.useCompression,
        "enabled": bool(req.enabled),
        "remark": _reject_ctrl(req.remark or "", "备注"),
        "created_at": datetime.now().isoformat(),
    }
    proxies.append(proxy)
    data.setdefault("client", {})["proxies"] = proxies
    _save_store(data)
    _write_toml(data)
    logger.info("新增 frpc 代理 %s", name)
    return proxy


@router.put("/proxies/{proxy_id}")
async def update_proxy(proxy_id: str, req: ProxyModel):
    """更新 frpc 代理。"""
    name = _check_proxy_name(req.name)
    if req.type not in VALID_PROXY_TYPES:
        raise HTTPException(status_code=400, detail="不支持的类型")
    local_ip = _reject_ctrl(req.localIp, "本地地址")
    _check_port(req.localPort, "本地端口")
    remote_port = _check_port(req.remotePort, "远程端口") if req.remotePort else None
    domains = _check_domains(req.customDomains or "")
    if req.type in ("tcp", "udp") and remote_port is None:
        raise HTTPException(status_code=400, detail="tcp/udp 代理必须填写远程端口")
    if req.type in ("http", "https") and not domains:
        raise HTTPException(status_code=400, detail="http/https 代理必须填写自定义域名")

    data = _load_store()
    proxies = data.get("client", {}).get("proxies", [])
    idx = next((i for i, p in enumerate(proxies) if p.get("id") == proxy_id), None)
    if idx is None:
        raise HTTPException(status_code=404, detail="代理不存在")
    if any(p.get("name") == name and p.get("id") != proxy_id for p in proxies):
        raise HTTPException(status_code=400, detail=f"代理名已存在: {name}")
    proxies[idx].update({
        "name": name,
        "type": req.type,
        "localIp": local_ip,
        "localPort": req.localPort,
        "remotePort": remote_port,
        "customDomains": domains,
        "useEncryption": bool(req.useEncryption),
        "useCompression": bool(req.useCompression),
        "enabled": bool(req.enabled),
        "remark": _reject_ctrl(req.remark or "", "备注"),
    })
    _save_store(data)
    _write_toml(data)
    logger.info("更新 frpc 代理 %s", name)
    return proxies[idx]


@router.delete("/proxies/{proxy_id}")
async def delete_proxy(proxy_id: str):
    """删除 frpc 代理。"""
    data = _load_store()
    proxies = data.get("client", {}).get("proxies", [])
    new_proxies = [p for p in proxies if p.get("id") != proxy_id]
    if len(new_proxies) == len(proxies):
        raise HTTPException(status_code=404, detail="代理不存在")
    data["client"]["proxies"] = new_proxies
    _save_store(data)
    _write_toml(data)
    return {"ok": True}


@router.post("/toggle-proxy/{proxy_id}")
async def toggle_proxy(proxy_id: str, body: dict):
    """启用 / 禁用代理（仅改变 enabled 并重写 toml）。"""
    enabled = bool(body.get("enabled", True))
    data = _load_store()
    proxies = data.get("client", {}).get("proxies", [])
    proxy = next((p for p in proxies if p.get("id") == proxy_id), None)
    if not proxy:
        raise HTTPException(status_code=404, detail="代理不存在")
    proxy["enabled"] = enabled
    _save_store(data)
    _write_toml(data)
    return proxy


@router.post("/start")
async def start_frp():
    """启动当前模式 frp。

    systemctl / nohup / taskkill 等为阻塞 subprocess，放线程池避免卡事件循环。
    """
    return await asyncio.to_thread(_start_frp_sync)


def _start_frp_sync() -> dict:
    data = _load_store()
    ok, msg = _do_start(data)
    if not ok:
        raise HTTPException(status_code=500, detail=msg)
    return {"ok": True, "running": _running(data), "message": msg}


@router.post("/stop")
async def stop_frp():
    """停止当前模式 frp（阻塞 subprocess，放线程池执行）。"""
    return await asyncio.to_thread(_stop_frp_sync)


def _stop_frp_sync() -> dict:
    data = _load_store()
    ok, msg = _do_stop(data)
    if not ok:
        raise HTTPException(status_code=500, detail=msg)
    return {"ok": True, "running": _running(data), "message": msg}


@router.post("/restart")
async def restart_frp():
    """重启当前模式 frp（先写盘最新配置再重启，阻塞 subprocess 放线程池）。"""
    return await asyncio.to_thread(_restart_frp_sync)


def _restart_frp_sync() -> dict:
    data = _load_store()
    _write_toml(data)  # 确保进程以最新配置启动
    _do_stop(data)
    ok, msg = _do_start(data)
    if not ok:
        raise HTTPException(status_code=500, detail=msg)
    return {"ok": True, "running": _running(data), "message": msg}