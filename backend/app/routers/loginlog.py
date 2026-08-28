# -*- coding: utf-8 -*-
"""
loginlog.py - 登录日志 / 异地登录提示路由

功能：
  1. 记录登录事件（成功/失败）：时间、用户名、来源 IP、设备（由 User-Agent 解析）、
     异常标记与原因。
  2. 异常登录检测：以「账号的历史 IP / 历史设备」为基准，首次出现在新 IP 或
     新设备上的登录标记为「异常」，并按通知中心配置推送到所有启用渠道。
  3. 提供查询接口：管理员可查看全部登录日志，普通用户可查看自己的登录历史。

数据存储：
  backend/data/login_logs.json     ：登录日志（环形截断，最多 MAX_LOG_ENTRIES 条）
  backend/data/login_known.json    ：各账号「已知 IP / 已知设备」指纹 + 告警开关配置

安全与健壮性：
  - record_login 由 auth.py 在登录流程中调用，任何异常都静默降级，绝不阻断登录。
  - 日志内容不包含密码 / token 等敏感信息（调用方负责脱敏）。
  - 异常提醒复用通知中心 notify.push_all，无可用渠道时静默跳过。
"""
import json
import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from ..auth import get_current_user, require_admin, get_client_ip

logger = logging.getLogger("graw.loginlog")

router = APIRouter()

# ---------------------------------------------------------------------------
# 常量与全局状态
# ---------------------------------------------------------------------------
DATA_DIR = os.path.normpath(
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data"))
)
LOG_FILE = os.path.join(DATA_DIR, "login_logs.json")
KNOWN_FILE = os.path.join(DATA_DIR, "login_known.json")

# 登录日志最多保留条数（环形截断，防止 JSON 无限膨胀）
MAX_LOG_ENTRIES = 500
# 单个账号指纹键空间上限（防止异常账号数据无限膨胀）
MAX_KNOWN_IPS = 200
MAX_KNOWN_DEVICES = 200

_write_lock = threading.Lock()


# ---------------------------------------------------------------------------
# 设备解析（User-Agent → 可读设备描述）
# ---------------------------------------------------------------------------
_OS_PATTERNS = [
    (re.compile(r"Windows NT 10\.0"), "Windows 10/11"),
    (re.compile(r"Windows NT 6\.3"), "Windows 8.1"),
    (re.compile(r"Windows NT 6\.[12]"), "Windows 7/8"),
    (re.compile(r"Windows"), "Windows"),
    (re.compile(r"Android"), "Android"),
    (re.compile(r"iPhone"), "iPhone"),
    (re.compile(r"iPad"), "iPad"),
    (re.compile(r"Mac OS X"), "macOS"),
    (re.compile(r"Macintosh"), "macOS"),
    (re.compile(r"Linux"), "Linux"),
    (re.compile(r"CrOS"), "ChromeOS"),
]

_BROWSER_PATTERNS = [
    (re.compile(r"Edg/"), "Edge"),
    (re.compile(r"OPR/|Opera"), "Opera"),
    (re.compile(r"Chrome/"), "Chrome"),
    (re.compile(r"Firefox/"), "Firefox"),
    (re.compile(r"Safari/"), "Safari"),
    (re.compile(r"curl/|wget/"), "命令行工具"),
    (re.compile(r"python-requests|PostmanRuntime|axios"), "API 客户端"),
]


def _match(patterns, ua: str):
    """按顺序匹配第一条命中的模式。"""
    for pat, name in patterns:
        if pat.search(ua):
            return name
    return None


def parse_device(user_agent: Optional[str]) -> str:
    """从 User-Agent 解析可读设备描述（浏览器 · 系统），无法识别返回「未知设备」。

    该描述仅用于日志展示与「新设备」判定，不参与鉴权。
    """
    ua = (user_agent or "").strip()
    if not ua:
        return "未知设备"
    browser = _match(_BROWSER_PATTERNS, ua) or "未知浏览器"
    os_name = _match(_OS_PATTERNS, ua) or "未知系统"
    return f"{browser} · {os_name}"


# ---------------------------------------------------------------------------
# 数据读写（JSON 文件，线程安全）
# ---------------------------------------------------------------------------
def _load_json(path: str, default):
    """读取 JSON 文件；文件缺失或损坏时返回 default。"""
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data
    except Exception as e:
        logger.warning("读取 %s 失败，按默认值处理: %s", os.path.basename(path), e)
        return default


def _save_json(path: str, data) -> None:
    """原子写 JSON 文件（先写临时文件再 rename，避免半写文件）。"""
    with _write_lock:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)


def _load_logs() -> list:
    return _load_json(LOG_FILE, [])


def _load_known() -> dict:
    data = _load_json(KNOWN_FILE, {})
    if not isinstance(data, dict):
        data = {}
    data.setdefault("_config", {"alert_enabled": True})
    return data


def _get_config() -> dict:
    return _load_known()["_config"]


# ---------------------------------------------------------------------------
# 登录记录核心（供 auth.py 调用）
# ---------------------------------------------------------------------------
def record_login(
    username: str,
    ip: str,
    user_agent: Optional[str],
    status: str = "success",
    detail: str = "",
) -> dict:
    """记录一条登录日志，并对成功登录做异常检测（新 IP / 新设备）。

    参数：
        username:  登录账号（失败时可能是攻击者伪造的不存在账号）
        ip:        来源 IP（get_client_ip 已兼容反代）
        user_agent: 客户端 User-Agent，可为空
        status:    "success" / "failed"
        detail:    失败原因等补充说明（调用方脱敏）

    返回：写入的日志条目（供单元测试断言）。任何异常均静默降级，绝不阻断登录。
    """
    try:
        device = parse_device(user_agent)
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        entry = {
            "id": uuid.uuid4().hex[:12],
            "username": username or "",
            "ip": ip or "",
            "ua": (user_agent or "")[:500],
            "device": device,
            "status": status,
            "abnormal": False,
            "abnormal_reason": "",
            "time": now,
            "detail": (detail or "")[:300],
        }
        if status == "success" and username:
            entry["abnormal"], entry["abnormal_reason"] = _check_abnormal(
                username, ip, device
            )
        _append_log(entry)
        # 异常成功登录 → 异步推送提醒（失败不推送，避免爆破噪音）
        if entry["abnormal"]:
            _notify_abnormal(entry)
        return entry
    except Exception as e:
        # 登录日志属于增强功能，任何异常不允许影响登录主流程
        logger.warning("记录登录日志失败（user=%s）: %s", username, e)
        return {}


def _check_abnormal(username: str, ip: str, device: str) -> tuple:
    """判定登录是否异常（新 IP 或新设备），并更新该账号的已知指纹。

    基准：该账号历史成功登录出现过的 IP 集合与设备集合。
    返回 (is_abnormal, reason)。
    """
    known = _load_known()
    user_known = known.setdefault(username, {"ips": [], "devices": []})
    ips = set(user_known.get("ips") or [])
    devices = set(user_known.get("devices") or [])
    reasons = []
    if ip and ip not in ips:
        reasons.append("新IP")
    if device and device not in devices:
        reasons.append("新设备")
    # 无论是否异常都更新指纹：首次成功登录视为建立基线
    if ip:
        ips.add(ip)
    if device:
        devices.add(device)
    user_known["ips"] = sorted(list(ips))[-MAX_KNOWN_IPS:]
    user_known["devices"] = sorted(list(devices))[-MAX_KNOWN_DEVICES:]
    _save_json(KNOWN_FILE, known)
    return (True, " / ".join(reasons)) if reasons else (False, "")


def _append_log(entry: dict) -> None:
    """追加一条日志并按上限环形截断（最新在前）。"""
    logs = _load_logs()
    logs.insert(0, entry)
    del logs[MAX_LOG_ENTRIES:]
    _save_json(LOG_FILE, logs)


def _notify_abnormal(entry: dict) -> None:
    """异常登录时按通知中心配置推送提醒；未配置渠道则静默跳过。"""
    try:
        if not _get_config().get("alert_enabled", True):
            return
        message = (
            f"【Graw 异常登录提醒】账号「{entry['username']}」于 {entry['time']} "
            f"从 {entry['ip']}（{entry['device']}）登录面板。\n"
            f"原因：该账号首次出现在{entry['abnormal_reason']}。"
            f"如非本人操作，请立即修改密码！"
        )
        # 延迟导入避免与 auth 的循环依赖；无渠道时 push_all 自然返回 0/0
        from app.routers.notify import push_all

        sent, failed = push_all(message)
        if sent:
            logger.info(
                "异常登录提醒已推送（user=%s, sent=%d）", entry["username"], sent
            )
    except Exception as e:
        # 推送失败不影响登录主流程
        logger.warning("异常登录提醒推送失败: %s", e)


# ---------------------------------------------------------------------------
# API：状态 / 列表 / 清空 / 配置 / 测试推送
# ---------------------------------------------------------------------------
@router.get("/status")
async def status(_: dict = Depends(get_current_user)):
    """登录日志功能状态（登录即可读取，供窗口展示）。"""
    logs = _load_logs()
    return {
        "alert_enabled": bool(_get_config().get("alert_enabled", True)),
        "total": len(logs),
        "success": sum(1 for x in logs if x.get("status") == "success"),
        "failed": sum(1 for x in logs if x.get("status") == "failed"),
        "abnormal": sum(1 for x in logs if x.get("abnormal")),
    }


@router.get("/list")
async def list_logs(
    limit: int = 100,
    username: str = "",
    status: str = "",
    _: dict = Depends(require_admin),
):
    """查询全部登录日志（管理员）：支持按账号 / 状态过滤。"""
    limit = max(1, min(limit, 500))
    logs = _load_logs()
    if username:
        logs = [x for x in logs if username in (x.get("username") or "")]
    if status in ("success", "failed"):
        logs = [x for x in logs if x.get("status") == status]
    return {"logs": logs[:limit]}


@router.get("/mine")
async def my_logs(
    limit: int = 100, user: dict = Depends(get_current_user)
):
    """查询当前用户自己的登录历史（登录即可读取）。"""
    limit = max(1, min(limit, 500))
    name = user.get("username", "")
    logs = [x for x in _load_logs() if x.get("username") == name]
    return {"logs": logs[:limit]}


@router.post("/clear")
async def clear_logs(_: dict = Depends(require_admin)):
    """清空全部登录日志（管理员）。"""
    _save_json(LOG_FILE, [])
    logger.info("登录日志已由管理员清空")
    return {"ok": True}


class ConfigRequest(BaseModel):
    alert_enabled: bool = Field(default=True)


@router.put("/config")
async def update_config(req: ConfigRequest, _: dict = Depends(require_admin)):
    """开关「异常登录提醒」推送（管理员）。"""
    known = _load_known()
    known["_config"]["alert_enabled"] = req.alert_enabled
    _save_json(KNOWN_FILE, known)
    logger.info("异常登录提醒开关已设置为 %s", req.alert_enabled)
    return {"ok": True}


@router.post("/test-alert")
async def test_alert(request: Request, _: dict = Depends(require_admin)):
    """发送一条测试用异常登录提醒（管理员，用于验证通知渠道）。"""
    entry = {
        "username": "admin(测试)",
        "ip": get_client_ip(request),
        "device": "浏览器(测试)",
        "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "abnormal_reason": "新IP / 新设备(测试)",
    }
    try:
        from app.routers.notify import push_all

        sent, failed = push_all(
            f"【Graw 异常登录提醒(测试)】账号「{entry['username']}」于 {entry['time']} "
            f"从 {entry['ip']}（{entry['device']}）登录面板。"
            f"这是用于验证通知渠道的测试消息，请忽略。"
        )
        return {"ok": True, "sent": sent, "failed": failed}
    except Exception as e:
        logger.warning("测试异常登录提醒推送失败: %s", e)
        # 安全：错误详情仅记日志，不回传（code-scanning py/stack-trace-exposure）
        return {"ok": False, "sent": 0, "failed": 0, "error": "推送失败"}
