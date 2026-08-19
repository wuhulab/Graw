# -*- coding: utf-8 -*-
"""
shunx.py - ShunX 安全入口保护机制路由

功能：
  1. 管理员可设置一个自定义安全入口路径（如 myadmin）。
  2. 设置了安全入口后，陌生设备必须通过 http://服务器地址/myadmin 才能看到登录页；
     直接访问首页不显示登录表单，且登录接口会校验请求来源（X-ShunX-Entry 头）。
  3. 未设置安全入口时允许正常登录，但登录后强制要求设置安全入口
     才能使用除「设置安全入口」以外的所有功能。
  4. 配置持久化在 backend/data/shunx.json。

安全设计：
  - 公开接口 GET /api/shunx/status 只返回 enabled 与「指定路径是否匹配」，
    绝不泄露已配置的入口路径本身，防止陌生设备通过接口探测到入口。
  - 登录接口在入口已配置时，校验请求头 X-ShunX-Entry（由前端根据浏览器
    实际地址栏路径设置），不匹配则拒绝登录（403）。
"""

import json
import logging
import os
import threading
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from ..auth import get_current_user, require_admin, get_client_ip

logger = logging.getLogger("graw.shunx")

router = APIRouter()

# ---------------------------------------------------------------------------
# 数据文件与锁
# ---------------------------------------------------------------------------
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
CONFIG_FILE = os.path.join(DATA_DIR, "shunx.json")
_file_lock = threading.Lock()


def _load_config() -> dict:
    """读取 ShunX 配置。文件不存在或损坏时返回默认配置。"""
    if not os.path.exists(CONFIG_FILE):
        return {"entry_path": None, "enabled": False}
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                return data
            return {"entry_path": None, "enabled": False}
    except Exception as exc:
        logger.warning("读取 shunx.json 失败: %s", exc)
        return {"entry_path": None, "enabled": False}


def _save_config(data: dict) -> None:
    """原子写入 ShunX 配置，避免并发写坏文件。"""
    with _file_lock:
        tmp = CONFIG_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_FILE)


def get_entry_path() -> Optional[str]:
    """获取当前配置的安全入口路径（不含前后斜杠），未配置返回 None。"""
    config = _load_config()
    entry = config.get("entry_path")
    if not entry:
        return None
    entry = entry.strip().strip("/")
    return entry or None


def is_entry_enabled() -> bool:
    """安全入口是否已启用（设置了有效路径）。"""
    return get_entry_path() is not None


# ---------------------------------------------------------------------------
# 公开接口：状态查询（不泄露入口路径本身）
# ---------------------------------------------------------------------------
# /status 的 matched 字段本质是一个「路径猜测是否命中」的公开预言机，
# 不限流时攻击者可高频字典枚举入口路径，故必须按 IP 限流抬高攻击成本。
_STATUS_MAX = 30        # 每个 IP 每窗口期内最大查询次数
_STATUS_WINDOW = 60     # 窗口期（秒）
_status_lock = threading.Lock()
_status_hits: dict = {}


def _status_throttle(request: Request) -> None:
    """/status 按 IP 滑动窗口限流，超出抛 429。"""
    # 与登录限流保持一致，使用 get_client_ip 兼容反代部署，
    # 避免客户端伪造 XFF 绕过限流（未配置可信代理时忽略 XFF）。
    ip = get_client_ip(request)
    now = time.time()
    with _status_lock:
        hits = _status_hits.setdefault(ip, [])
        # 淘汰窗口外的旧记录
        while hits and hits[0] <= now - _STATUS_WINDOW:
            hits.pop(0)
        if len(hits) >= _STATUS_MAX:
            raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")
        hits.append(now)
        # 防止键空间无限增长：顺带清理长时间无活动的 IP
        if len(_status_hits) > 10000:
            stale = [
                k
                for k, v in _status_hits.items()
                if not v or v[-1] <= now - _STATUS_WINDOW * 10
            ]
            for k in stale:
                _status_hits.pop(k, None)


@router.get("/status")
async def get_status(request: Request, path: str = ""):
    """查询安全入口状态及「给定路径是否匹配入口」。

    公开可访问。只返回 enabled 与 matched，不返回 entry_path，
    防止陌生设备通过接口探测出入口路径；按 IP 限流防高频枚举。
    """
    _status_throttle(request)
    entry = get_entry_path()
    enabled = entry is not None
    # 归一化比较：去掉首尾斜杠、忽略大小写，提升使用便利性
    normalized = path.strip().strip("/").lower()
    matched = enabled and normalized == entry.lower()
    return {"enabled": enabled, "matched": matched}


# ---------------------------------------------------------------------------
# 受保护接口：配置管理（查询需登录，修改仅管理员）
# ---------------------------------------------------------------------------
@router.get("/config")
async def get_config(user: dict = Depends(get_current_user)):
    """获取 ShunX 安全入口配置（需登录）。

    角色脱敏（第七轮审计修复）：entry_path 是安全入口的核心秘密，
    仅管理员（设置窗口需要展示/回填当前入口）可见；普通用户只返回
    enabled 标记（前端仅用于判断「是否已配置」），防止低权限账号
    被攻破后直接泄露入口路径，瓦解 ShunX 的核心承诺。
    """
    config = _load_config()
    entry = config.get("entry_path")
    # 与 verify_entry / is_entry_enabled 的实际生效语义保持一致：
    # 配置了有效 entry_path 即视为启用（enabled 存储字段仅作展示参考）
    enabled = get_entry_path() is not None
    if user.get("role") != "admin":
        # 普通用户：不回传入口路径本身，仅告知是否已启用
        return {"entry_path": None, "enabled": enabled}
    return {
        "entry_path": entry,
        "enabled": enabled,
    }


class SetConfigRequest(BaseModel):
    entry_path: str = Field(default="", max_length=128, description="安全入口路径，留空则清除")
    enabled: bool = True


@router.put("/config")
async def set_config(req: SetConfigRequest, _: dict = Depends(require_admin)):
    """设置 ShunX 安全入口配置（仅管理员）。

    - entry_path: 自定义路径，如 myadmin；留空则清除安全入口。
    - enabled: 是否启用入口检查（仅 entry_path 非空时有效）。
    """
    entry_path = req.entry_path.strip().strip("/") if req.entry_path else ""
    # 校验：路径仅允许字母/数字/下划线/中划线，避免非法字符进入 URL
    if entry_path and not all(ch.isalnum() or ch in "_-" for ch in entry_path):
        raise HTTPException(
            status_code=400,
            detail="入口路径仅允许字母、数字、下划线和中划线",
        )
    config = _load_config()
    config["entry_path"] = entry_path if entry_path else None
    config["enabled"] = req.enabled and bool(entry_path)
    _save_config(config)
    logger.info("ShunX 安全入口已更新: %s", config)
    return {"ok": True, "config": config}


# ---------------------------------------------------------------------------
# 登录入口校验（供 auth.py 登录接口调用）
# ---------------------------------------------------------------------------
def verify_entry(request: Request) -> None:
    """验证登录请求是否来自安全入口。

    入口已配置时，要求请求头 X-ShunX-Entry 与配置的 entry_path 一致。
    未配置入口时跳过检查，保证旧行为兼容。
    """
    entry = get_entry_path()
    if not entry:
        return  # 未配置入口，不检查

    entry_header = (request.headers.get("X-ShunX-Entry") or "").strip().strip("/").lower()
    if entry_header != entry.lower():
        logger.warning("安全入口校验失败: 期望=%s, 收到=%s", entry, entry_header)
        raise HTTPException(
            status_code=403,
            detail="请通过安全入口访问（当前访问路径不是已配置的安全入口）",
        )