# -*- coding: utf-8 -*-
"""
webserver.py - 宿主机 Web 服务器引擎（NGINX / OpenResty）配置适配层

背景：
  Graw 的站点 / WAF / stream 等功能生成的都是「nginx 配置格式」。OpenResty
  是基于 nginx 的发行版（内置 Lua），其二进名为 ``openresty``、默认配置前缀
  为 ``/usr/local/openresty/nginx/conf``，与原生 nginx（二进制 ``nginx``、
  配置前缀 ``/etc/nginx``）存在差异。

  本模块把「引擎选择」集中到一处：读取持久化模式（NGINX / OpenResty），并把
  二进制名、可用性探测、reload 命令、各级配置目录等差异统一成一套接口，供
  sites / waf 等路由复用。开启 OpenResty 模式后，无需改动各路由内部逻辑。

配置存储：backend/data/webserver.json（{"mode": "nginx" | "openresty"}）。
"""
import json
import os

from app.hostfs import host_cmd, host_which, host_path

# 配置目录与文件（backend/data/webserver.json）
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
CONFIG_FILE = os.path.join(DATA_DIR, "webserver.json")

# 支持的引擎
MODE_NGINX = "nginx"
MODE_OPENRESTY = "openresty"
_MODES = {MODE_NGINX, MODE_OPENRESTY}

# 各引擎「宿主机视角」配置根目录（写入时经 host_path 映射到容器 /host）
_NGINX_BASE = "/etc/nginx"
_OPENRESTY_BASE = "/usr/local/openresty/nginx/conf"


def _load() -> dict:
    """读取引擎配置；文件缺失/损坏时返回空（使用默认 nginx）。"""
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
    except Exception:
        # 配置不可读时不阻断：退回默认（日志/业务侧均安全失败）
        return {}
    return {}


def _save(data: dict) -> None:
    """持久化引擎配置；目录不存在时自动创建。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_mode() -> str:
    """返回当前引擎模式（nginx / openresty），默认 nginx。"""
    return _load().get("mode", MODE_NGINX)


def set_mode(mode: str) -> str:
    """设置引擎模式并持久化；非法模式抛 ValueError。"""
    mode = (mode or "").strip().lower()
    if mode not in _MODES:
        raise ValueError(
            f"不支持的 Web 服务器引擎: {mode!r}（仅支持 nginx/openresty）"
        )
    _save({"mode": mode})
    return mode


def is_openresty() -> bool:
    """当前是否 OpenResty 模式。"""
    return get_mode() == MODE_OPENRESTY


def binary() -> str:
    """当前引擎对应的二进制名。"""
    return MODE_OPENRESTY if is_openresty() else MODE_NGINX


# ---------------------------------------------------------------------------
# 可用性 / reload
# ---------------------------------------------------------------------------
def available(engine: str = None) -> bool:
    """检测给定（或缺省当前）引擎二进制在宿主机是否可用。

    优先 host_which（容器模式在 /host 常见 bin 目录探测），再用 ``-v`` 兜底
    执行探测。任何异常都按不可用处理（不抛错，供 UI 与 reload 决策）。
    """
    cmd = (engine or binary())
    try:
        if host_which(cmd):
            return True
        r = host_cmd([cmd, "-v"], capture_output=True, timeout=5)
        return r.returncode == 0
    except Exception:
        return False


def nginx_like_available() -> bool:
    """是否有任一 nginx 系引擎可用（nginx 或 openresty）。

    sites 的 web_server_type 用它判断「能否生成 nginx 格式配置」：
    只要安装了 openresty 或 nginx 之一，即视为 nginx 系。
    """
    return available(MODE_NGINX) or available(MODE_OPENRESTY)


def reload() -> bool:
    """让当前引擎重新加载配置；成功返回 True，失败 False（不抛异常）。"""
    try:
        r = host_cmd([binary(), "-s", "reload"], capture_output=True, timeout=10)
        return r.returncode == 0
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 配置目录（宿主机视角，写入时经 host_path 映射）
# ---------------------------------------------------------------------------
def base_dir() -> str:
    """当前引擎配置根目录。"""
    return _OPENRESTY_BASE if is_openresty() else _NGINX_BASE


def available_dir() -> str:
    """site 配置「可用」目录。"""
    return base_dir() + "/sites-available"


def enabled_dir() -> str:
    """site 配置「启用」目录。"""
    return base_dir() + "/sites-enabled"


def conf_path() -> str:
    """主 nginx.conf 路径（用于注入 stream include）。"""
    return base_dir() + "/nginx.conf"


def stream_dir() -> str:
    """TCP/UDP 代理 stream 配置目录。"""
    return base_dir() + "/stream-enabled"


def stream_include() -> str:
    """需要注入到 nginx.conf 的 stream include 行。"""
    return f"include {stream_dir()}/*.conf;"


def waf_dir() -> str:
    """WAF include 片段目录。"""
    return base_dir() + "/waf"


def status() -> dict:
    """供设置/状态接口展示的整体信息。"""
    return {
        "mode": get_mode(),
        "binary": binary(),
        "available": available(),
        "nginx_available": available(MODE_NGINX),
        "openresty_available": available(MODE_OPENRESTY),
        "conf_base": base_dir(),
        "config_file": CONFIG_FILE,
    }