# -*- coding: utf-8 -*-
"""
vip.py - Graw 主面板的 VIP（月卡/年卡用户）确认与状态管理

背景：
  Graw 通过独立的 graw_vip 授权码服务确认付费用户。本模块作为「客户端」，
  把授权码服务地址与各用户的 VIP 状态统一在面板侧管理：
    - 服务地址：固定写在后端常量 DEFAULT_SERVER_URL，『禁止从前端修改』，
      后续部署直接改后端这一处即可（可选环境变量 GRAW_VIP_SERVER 覆盖）
    - 状态：data/vip.json 按用户名记录 VIP 计划与截止时间

关键约定（与 graw_vip 对齐）：
  - 授权码为一次性凭证；激活后授予固定时长（月卡 +30 天 / 年卡 +365 天）。
  - 同一用户再次激活新码时采用「取更大截止」：新码授予的时长若晚于当前
    生效截止则顺延，否则保持现有截止，既不吞时长也避免刷时长。
落库逻辑见 activate_vip 与 _set_user_vip。"""
from __future__ import annotations

import json
import logging
import os
import threading
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger("graw.vip")

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
VIP_STATE_FILE = os.path.join(DATA_DIR, "vip.json")

_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

# 授权码服务地址：固定写在后端，前端不可修改。后续部署改这里即可。
DEFAULT_SERVER_URL = "https://graw-vip.shunx.top/"
# 执行器对服务的请求超时（秒）
_ACTIVATE_TIMEOUT = 10

# 使用可重入锁：_set_user_vip 等函数内部会再次调用 _load_state/_save_state/get_vip，
# 普通 Lock 会导致嵌套加锁死锁，故必须为 RLock。
_lock = threading.RLock()
_state_cache: Optional[dict] = None


def _now() -> str:
    """当前 UTC 时间 ISO 字符串。"""
    return datetime.now(timezone.utc).isoformat()


def _is_active(vip_until: Optional[str]) -> bool:
    """VIP 是否生效：截止时间存在且在现在之后。"""
    if not vip_until:
        return False
    try:
        return datetime.fromisoformat(vip_until) > datetime.now(timezone.utc)
    except (ValueError, TypeError):
        return False


# ------------------- 配置：授权码服务地址（后端固定，前端不可改） -------------------
def get_server_url() -> str:
    """返回授权码服务地址：部署时如需临时覆盖可用环境变量 GRAW_VIP_SERVER，
    否则始终使用后端常量 DEFAULT_SERVER_URL。前端无法改动此项。"""
    url = os.environ.get("GRAW_VIP_SERVER", "").strip()
    return (url or DEFAULT_SERVER_URL).rstrip("/")


# ------------------- 状态：各用户 VIP -------------------
def _load_state() -> dict:
    """读取 vip.json（带锁 + 内存缓存）。"""
    global _state_cache
    with _lock:
        if _state_cache is not None:
            return _state_cache
        state = {}
        if os.path.exists(VIP_STATE_FILE):
            try:
                with open(VIP_STATE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    state = data
            except (OSError, json.JSONDecodeError) as e:
                logger.warning("读取 vip 状态失败：%s", e)
        _state_cache = state
        return state


def _save_state() -> None:
    """持久化 vip.json（原子写）。"""
    global _state_cache
    with _lock:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = VIP_STATE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_state_cache or {}, f, ensure_ascii=False, indent=2)
        os.replace(tmp, VIP_STATE_FILE)


def get_vip(username: str) -> dict:
    """查询指定用户的 VIP 状态。未开通/已过期返回非生效状态。"""
    username = (username or "").strip()
    rec = (_load_state().get(username) or {}) if username else {}
    vip_until = rec.get("vip_until")
    active = _is_active(vip_until)
    return {
        "vip": active,
        "plan": rec.get("plan") or "",
        "activated_at": rec.get("activated_at") or "",
        "vip_until": vip_until or "",
        "is_vip": active,  # 语义别名，便于前端统一取用
    }


def _set_user_vip(username: str, plan: str, activated_until: str) -> dict:
    """落库某用户的 VIP 状态，并返回刷新后的状态字典。"""
    username = (username or "").strip()
    plan = "year" if plan == "year" else "month"
    with _lock:
        state = _load_state()
        existing = state.get(username) or {}
        # 生效期内再次激活采用「顺延累加」：以更大截止为准，避免吞时长/刷时长
        prev = existing.get("vip_until")
        base = datetime.now(timezone.utc)
        if _is_active(prev):
            try:
                base = datetime.fromisoformat(prev)
            except (ValueError, TypeError):
                base = datetime.now(timezone.utc)
        new = max(base, _parse_dt(activated_until))
        rec = {
            "plan": plan,
            "activated_at": _now(),
            "vip_until": new.isoformat(),
        }
        state[username] = rec
        _save_state()
    logger.info("更新用户 %s 的 VIP 状态：plan=%s until=%s", username, plan, rec["vip_until"])
    return get_vip(username)


def _parse_dt(value: str) -> datetime:
    """解析 ISO 时间，失败回退为当前 UTC 时间。"""
    try:
        return datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return datetime.now(timezone.utc)


# ------------------- 激活（调用 graw_vip 服务） -------------------
def activate_vip(username: str, code: str) -> dict:
    """携带授权码调用授权码服务激活，成功后落库用户 VIP 状态。

    返回结构固定含 vip/plan/vip_until；失败时抛出 ValueError，由路由层
    转为对应 HTTP 错误（授权码无效/已使用/服务不可达）。
    """
    code = (code or "").strip()
    if not code:
        raise ValueError("请输入授权码")
    resp = _call_license("POST", "/license/activate", {"code": code, "machine_id": (username or "").strip()})
    # 服务端拒绝：无效(404) / 已使用(403)
    if not resp.get("ok"):
        err = resp.get("detail") or resp.get("message") or "授权码无效"
        raise ValueError(err)
    plan = "year" if resp.get("type") == "year" else "month"
    until = resp.get("activated_until") or ""
    return _set_user_vip(username, plan, until)


def _call_license(method: str, path: str, payload: dict) -> dict:
    """调用授权码服务（服务器到服务器，使用纯标准库 urllib）。

    统一把网络/HTTP 错误转换为可读 ValueError，避免上层处理 HTTPException。
    """
    url = get_server_url() + path
    data = json.dumps(payload or {}).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method or "POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", _BROWSER_UA)
    try:
        with urllib.request.urlopen(req, timeout=_ACTIVATE_TIMEOUT) as r:
            body = r.read().decode("utf-8", errors="replace")
            return json.loads(body) if body.strip() else {}
    except urllib.error.HTTPError as e:
        # 读取错误体（授权码服务返回的 detail），尽量透传可读原因
        try:
            err_body = e.read().decode("utf-8", errors="replace")
            detail = json.loads(err_body).get("detail") if err_body.strip() else ""
        except Exception:
            detail = ""
        raise ValueError(detail or f"授权码服务返回错误（HTTP {e.code}）")
    except urllib.error.URLError as e:
        raise ValueError(f"无法连接授权码服务：{e.reason}")
    except (TimeoutError, OSError) as e:
        raise ValueError(f"授权码服务请求超时或异常：{e}")