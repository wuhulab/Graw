# -*- coding: utf-8 -*-
"""
notify.py - Graw 通知中心路由

功能：
  1. 通知渠道管理：Webhook / Telegram / 钉钉 / 企业微信 / Server酱 / SMTP 邮件，
     支持增删改与「测试发送」。
  2. 资源阈值告警：CPU / 内存 / 磁盘 / 负载 超过阈值时触发告警（冷却去重），
     推送到所有已启用渠道，并写入告警记录。
  3. 后台监控：main.py lifespan 启动 asyncio 循环（默认 60s 检查一次），
     复用 psutil 读取系统指标，不依赖 system 路由。

数据存储：
  backend/data/notify.json    ：渠道 / 规则 / 冷却 / 总开关
  backend/data/notify_logs.json ：告警记录（环形截断，最多 200 条）

安全：
  - 渠道 URL 仅允许 http/https，SMTP 端口白名单 1-65535；
  - 渠道密码等敏感字段编辑时不回显（has_* 标记）。
"""
import asyncio
import json
import logging
import os
import platform
import threading
import time
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.hostfs import host_path

logger = logging.getLogger("graw.notify")

router = APIRouter()

# ---------------------------------------------------------------------------
# 常量与全局状态
# ---------------------------------------------------------------------------
DATA_DIR = os.path.normpath(
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data"))
)
NOTIFY_FILE = os.path.join(DATA_DIR, "notify.json")
LOG_FILE = os.path.join(DATA_DIR, "notify_logs.json")

IS_WINDOWS = platform.system() == "Windows"

# 支持的通知渠道类型
CHANNEL_TYPES = ("webhook", "telegram", "dingtalk", "wecom", "serverchan", "smtp")

# 支持的告警指标
METRICS = ("cpu", "mem", "disk", "load")

# 默认监控间隔（秒）与告警冷却（秒）
DEFAULT_INTERVAL = 60
DEFAULT_COOLDOWN = 300
# 告警记录最多保留条数（环形截断，防止 JSON 无限膨胀）
MAX_LOG_ENTRIES = 200

# 数据写锁
_notify_lock = threading.Lock()
# 各规则最近告警时间（冷却去重，内存态即可，重启后重新开始）
_last_alert = {}
# 后台监控协程
_monitor_task = None


def _default_config() -> dict:
    return {
        "enabled": False,
        "interval_seconds": DEFAULT_INTERVAL,
        "cooldown_seconds": DEFAULT_COOLDOWN,
        "channels": [],
        "rules": [],
    }


def _load() -> dict:
    if not os.path.exists(NOTIFY_FILE):
        return _default_config()
    try:
        with open(NOTIFY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning("读取 notify.json 失败，按默认配置处理: %s", e)
        return _default_config()
    if not isinstance(data, dict):
        return _default_config()
    for k, v in _default_config().items():
        data.setdefault(k, v)
    return data


def _save(data: dict):
    with _notify_lock:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = NOTIFY_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, NOTIFY_FILE)


def _load_logs() -> list:
    if not os.path.exists(LOG_FILE):
        return []
    try:
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            logs = json.load(f)
        return logs if isinstance(logs, list) else []
    except Exception:
        return []


def _save_logs(logs: list):
    with _notify_lock:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = LOG_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(logs, f, ensure_ascii=False, indent=2)
        os.replace(tmp, LOG_FILE)


def _append_log(entry: dict):
    logs = _load_logs()
    logs.append(entry)
    # 环形截断：只保留最新 MAX_LOG_ENTRIES 条，避免无限增长
    if len(logs) > MAX_LOG_ENTRIES:
        logs = logs[-MAX_LOG_ENTRIES:]
    _save_logs(logs)


# ---------------------------------------------------------------------------
# 渠道字段校验与脱敏
# ---------------------------------------------------------------------------
def _validate_url(url: str, field: str = "URL") -> str:
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail=f"{field}必须为 http/https URL")
    return url


def _validate_channel(channel: dict) -> None:
    """按渠道类型校验必需字段，防止缺字段导致运行时报错。"""
    ctype = channel.get("type")
    cfg = channel.get("config") or {}
    if ctype == "webhook":
        cfg["url"] = _validate_url(cfg.get("url"), "Webhook 地址")
    elif ctype == "dingtalk":
        cfg["webhook"] = _validate_url(cfg.get("webhook"), "钉钉机器人地址")
    elif ctype == "wecom":
        cfg["webhook"] = _validate_url(cfg.get("webhook"), "企业微信机器人地址")
    elif ctype == "telegram":
        if not (cfg.get("bot_token") or "").strip():
            raise HTTPException(status_code=400, detail="Telegram 机器人 Token 不能为空")
        if not (cfg.get("chat_id") or "").strip():
            raise HTTPException(status_code=400, detail="Telegram 接收 chat_id 不能为空")
    elif ctype == "serverchan":
        if not (cfg.get("key") or "").strip():
            raise HTTPException(status_code=400, detail="Server酱 SendKey 不能为空")
    elif ctype == "smtp":
        if not (cfg.get("host") or "").strip():
            raise HTTPException(status_code=400, detail="SMTP 主机不能为空")
        port = int(cfg.get("port") or 0)
        if not (1 <= port <= 65535):
            raise HTTPException(status_code=400, detail="SMTP 端口必须在 1-65535")
        if not (cfg.get("from") or "").strip() or not (cfg.get("to") or "").strip():
            raise HTTPException(status_code=400, detail="SMTP 发件人/收件人不能为空")
    else:
        raise HTTPException(status_code=400, detail="不支持的通知渠道类型")


def _mask_channel(channel: dict) -> dict:
    """脱敏渠道：不返回 token/密码等敏感字段，只给 has_* 标记。"""
    cfg = channel.get("config") or {}
    secret_keys = ("password", "bot_token", "key")
    masked = {
        "id": channel.get("id", ""),
        "name": channel.get("name", ""),
        "type": channel.get("type", ""),
        "enabled": channel.get("enabled", True),
        "config": {k: v for k, v in cfg.items() if k not in secret_keys},
    }
    for k in secret_keys:
        masked["config"][f"has_{k}"] = bool(cfg.get(k))
    return masked


# ---------------------------------------------------------------------------
# 通知推送
# ---------------------------------------------------------------------------
def _alert_message(metric: str, value: float, threshold: float) -> str:
    labels = {"cpu": "CPU 使用率", "mem": "内存使用率", "disk": "磁盘使用率", "load": "系统负载"}
    return (
        f"【Graw 资源告警】{labels.get(metric, metric)} {value:.1f}%"
        f"（阈值 {threshold:.0f}%）{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )


def _send_webhook(cfg: dict, message: str) -> None:
    import requests

    r = requests.post(cfg["url"], json={"text": message, "msgtype": "text"}, timeout=15)
    r.raise_for_status()


def _send_dingtalk(cfg: dict, message: str) -> None:
    import requests

    r = requests.post(cfg["webhook"], json={"msgtype": "text", "text": {"content": message}}, timeout=15)
    r.raise_for_status()


def _send_wecom(cfg: dict, message: str) -> None:
    import requests

    r = requests.post(cfg["webhook"], json={"msgtype": "text", "text": {"content": message}}, timeout=15)
    r.raise_for_status()


def _send_telegram(cfg: dict, message: str) -> None:
    import requests

    url = f"https://api.telegram.org/bot{cfg['bot_token']}/sendMessage"
    r = requests.post(url, json={"chat_id": cfg["chat_id"], "text": message}, timeout=15)
    r.raise_for_status()


def _send_serverchan(cfg: dict, message: str) -> None:
    import requests

    r = requests.post(
        f"https://sctapi.ftqq.com/{cfg['key']}.send",
        data={"title": "Graw 资源告警", "desp": message},
        timeout=15,
    )
    r.raise_for_status()


def _send_smtp(cfg: dict, message: str) -> None:
    import smtplib
    from email.mime.text import MIMEText

    msg = MIMEText(message, "plain", "utf-8")
    msg["Subject"] = (cfg.get("subject") or "").strip() or "Graw 资源告警"
    msg["From"] = cfg["from"]
    msg["To"] = cfg["to"]
    port = int(cfg.get("port") or 25)
    if cfg.get("ssl"):
        server = smtplib.SMTP_SSL(cfg["host"], port, timeout=15)
    else:
        server = smtplib.SMTP(cfg["host"], port, timeout=15)
    try:
        if not cfg.get("ssl") and cfg.get("starttls"):
            server.starttls()
        if cfg.get("username"):
            server.login(cfg["username"], cfg.get("password") or "")
        server.sendmail(cfg["from"], cfg["to"].split(","), msg.as_string())
    finally:
        try:
            server.quit()
        except Exception:
            pass


def _send_to_channel(channel: dict, message: str) -> None:
    """按渠道类型推送一条消息；网络/认证失败抛异常由调用方记录。"""
    cfg = channel.get("config") or {}
    sender = {
        "webhook": _send_webhook,
        "dingtalk": _send_dingtalk,
        "wecom": _send_wecom,
        "telegram": _send_telegram,
        "serverchan": _send_serverchan,
        "smtp": _send_smtp,
    }.get(channel.get("type"))
    if not sender:
        raise RuntimeError("不支持的渠道类型")
    sender(cfg, message)


# ---------------------------------------------------------------------------
# 指标读取与监控
# ---------------------------------------------------------------------------
def _read_metrics() -> dict:
    """读取系统指标（复用 psutil，与 system 路由口径一致）。"""
    import psutil

    cpu = psutil.cpu_percent(interval=None)
    mem = psutil.virtual_memory().percent
    disk_path = host_path("/") if not IS_WINDOWS else "C:\\"
    try:
        disk = psutil.disk_usage(disk_path).percent
    except OSError:
        disk = 0.0
    try:
        load1, _l5, _l15 = psutil.getloadavg()
    except Exception:
        load1 = cpu / 100 * psutil.cpu_count()
    load_percent = min(100.0, (load1 / max(1, psutil.cpu_count())) * 100)
    return {"cpu": round(cpu, 1), "mem": round(mem, 1), "disk": round(disk, 1), "load": round(load_percent, 1)}


def _check_once() -> int:
    """执行一次阈值检查，返回本次触发告警数。

    规则命中且在冷却期外 → 写记录 + 推送所有启用渠道。冷却去重防止
    高频重复轰炸（同一规则在 cooldown_seconds 内只告警一次）。
    """
    data = _load()
    if not data.get("enabled"):
        return 0
    metrics = _read_metrics()
    now = time.time()
    cooldown = int(data.get("cooldown_seconds") or DEFAULT_COOLDOWN)
    triggered = 0
    for rule in data.get("rules", []):
        if not rule.get("enabled"):
            continue
        metric = rule.get("metric")
        threshold = float(rule.get("threshold") or 0)
        value = metrics.get(metric)
        if value is None or value < threshold:
            continue
        # 冷却去重
        last = _last_alert.get(rule["id"], 0)
        if now - last < cooldown:
            continue
        _last_alert[rule["id"]] = now
        message = _alert_message(metric, value, threshold)
        sent, failed = 0, 0
        for ch in data.get("channels", []):
            if not ch.get("enabled"):
                continue
            try:
                _send_to_channel(ch, message)
                sent += 1
            except Exception as e:
                failed += 1
                logger.warning("通知渠道 %s 发送失败: %s", ch.get("name"), e)
        _append_log({
            "id": uuid.uuid4().hex[:12],
            "time": datetime.now().isoformat(),
            "metric": metric,
            "value": value,
            "threshold": threshold,
            "message": message,
            "sent_channels": sent,
            "failed_channels": failed,
        })
        triggered += 1
        logger.info("资源告警触发：%s=%.1f%%（阈值 %.0f%%）", metric, value, threshold)
    return triggered


async def _monitor_loop():
    """后台监控循环：按配置间隔周期性检查阈值。"""
    while True:
        try:
            await asyncio.to_thread(_check_once)
        except Exception as e:
            logger.error("阈值检查失败: %s", e)
        interval = max(10, int(_load().get("interval_seconds") or DEFAULT_INTERVAL))
        await asyncio.sleep(interval)


async def start_monitor():
    """启动后台监控协程（幂等，避免重复启动）。"""
    global _monitor_task
    if _monitor_task is not None and not _monitor_task.done():
        return
    _monitor_task = asyncio.create_task(_monitor_loop())
    logger.info("通知中心后台监控已启动")


async def stop_monitor():
    """停止后台监控协程。"""
    global _monitor_task
    if _monitor_task is not None:
        _monitor_task.cancel()
        try:
            await _monitor_task
        except asyncio.CancelledError:
            pass
        _monitor_task = None


# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------
class ChannelRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    type: str
    config: dict = Field(default_factory=dict)
    enabled: Optional[bool] = True


class RuleRequest(BaseModel):
    metric: str
    threshold: float = Field(..., ge=0, le=10000)
    enabled: Optional[bool] = True


class ConfigRequest(BaseModel):
    enabled: Optional[bool] = None
    interval_seconds: Optional[int] = Field(None, ge=10, le=86400)
    cooldown_seconds: Optional[int] = Field(None, ge=30, le=86400)


# ---------------------------------------------------------------------------
# API 端点
# ---------------------------------------------------------------------------
@router.get("/status")
async def status():
    """通知中心状态摘要。"""
    data = _load()
    return {
        "enabled": data.get("enabled", False),
        "interval_seconds": data.get("interval_seconds", DEFAULT_INTERVAL),
        "cooldown_seconds": data.get("cooldown_seconds", DEFAULT_COOLDOWN),
        "channel_count": len(data.get("channels", [])),
        "rule_count": len(data.get("rules", [])),
        "log_count": len(_load_logs()),
    }


@router.get("/channels")
async def list_channels():
    """返回通知渠道列表（敏感字段脱敏）。"""
    data = _load()
    return {"channels": [_mask_channel(c) for c in data.get("channels", [])]}


@router.post("/channels")
async def create_channel(req: ChannelRequest):
    """创建通知渠道。"""
    if req.type not in CHANNEL_TYPES:
        raise HTTPException(status_code=400, detail="不支持的渠道类型")
    channel = {
        "id": "ch_" + uuid.uuid4().hex[:10],
        "name": (req.name or "").strip(),
        "type": req.type,
        "config": dict(req.config or {}),
        "enabled": bool(req.enabled if req.enabled is not None else True),
        "created_at": datetime.now().isoformat(),
    }
    _validate_channel(channel)
    data = _load()
    data.setdefault("channels", []).append(channel)
    _save(data)
    logger.info("创建通知渠道：%s（%s）", channel["name"], req.type)
    return _mask_channel(channel)


@router.put("/channels/{channel_id}")
async def update_channel(channel_id: str, req: ChannelRequest):
    """更新通知渠道（密码/token 留空表示保持原值）。"""
    data = _load()
    channel = next((c for c in data.get("channels", []) if c.get("id") == channel_id), None)
    if not channel:
        raise HTTPException(status_code=404, detail="通知渠道不存在")
    new_cfg = dict(req.config or {})
    # 敏感字段留空 = 保持原值
    for k in ("password", "bot_token", "key"):
        if not new_cfg.get(k):
            new_cfg[k] = (channel.get("config") or {}).get(k, "")
    channel["name"] = (req.name or "").strip()
    channel["type"] = req.type if req.type in CHANNEL_TYPES else channel["type"]
    channel["config"] = new_cfg
    channel["enabled"] = bool(req.enabled if req.enabled is not None else channel.get("enabled", True))
    _validate_channel(channel)
    _save(data)
    logger.info("更新通知渠道：%s", channel["name"])
    return _mask_channel(channel)


@router.delete("/channels/{channel_id}")
async def delete_channel(channel_id: str):
    """删除通知渠道。"""
    data = _load()
    before = len(data.get("channels", []))
    data["channels"] = [c for c in data.get("channels", []) if c.get("id") != channel_id]
    if len(data["channels"]) == before:
        raise HTTPException(status_code=404, detail="通知渠道不存在")
    _save(data)
    logger.info("删除通知渠道：%s", channel_id)
    return {"ok": True}


@router.post("/channels/{channel_id}/test")
async def test_channel(channel_id: str):
    """测试发送一条渠道通知。"""
    import asyncio

    data = _load()
    channel = next((c for c in data.get("channels", []) if c.get("id") == channel_id), None)
    if not channel:
        raise HTTPException(status_code=404, detail="通知渠道不存在")
    message = (
        f"【Graw 通知测试】渠道 {channel.get('name')} 配置正常"
        f"（{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}）"
    )
    try:
        await asyncio.to_thread(_send_to_channel, channel, message)
    except Exception as e:
        logger.warning("通知测试发送失败 %s: %s", channel.get("name"), e)
        raise HTTPException(status_code=502, detail=f"发送失败：{e}")
    return {"ok": True, "name": channel.get("name", "")}


@router.get("/rules")
async def list_rules():
    """返回告警规则列表。"""
    data = _load()
    return {"rules": data.get("rules", [])}


@router.post("/rules")
async def create_rule(req: RuleRequest):
    """创建告警规则。"""
    if req.metric not in METRICS:
        raise HTTPException(status_code=400, detail="不支持的指标类型")
    rule = {
        "id": "rule_" + uuid.uuid4().hex[:10],
        "metric": req.metric,
        "threshold": req.threshold,
        "enabled": bool(req.enabled if req.enabled is not None else True),
        "created_at": datetime.now().isoformat(),
    }
    data = _load()
    data.setdefault("rules", []).append(rule)
    _save(data)
    logger.info("创建告警规则：%s >= %.0f%%", req.metric, req.threshold)
    return rule


@router.put("/rules/{rule_id}")
async def update_rule(rule_id: str, req: RuleRequest):
    """更新告警规则。"""
    data = _load()
    rule = next((r for r in data.get("rules", []) if r.get("id") == rule_id), None)
    if not rule:
        raise HTTPException(status_code=404, detail="告警规则不存在")
    if req.metric not in METRICS:
        raise HTTPException(status_code=400, detail="不支持的指标类型")
    rule["metric"] = req.metric
    rule["threshold"] = req.threshold
    rule["enabled"] = bool(req.enabled if req.enabled is not None else rule.get("enabled", True))
    _save(data)
    return rule


@router.delete("/rules/{rule_id}")
async def delete_rule(rule_id: str):
    """删除告警规则。"""
    data = _load()
    before = len(data.get("rules", []))
    data["rules"] = [r for r in data.get("rules", []) if r.get("id") != rule_id]
    if len(data["rules"]) == before:
        raise HTTPException(status_code=404, detail="告警规则不存在")
    _save(data)
    return {"ok": True}


@router.put("/config")
async def update_config(req: ConfigRequest):
    """更新通知中心总配置（开关 / 检查间隔 / 冷却时间）。"""
    data = _load()
    if req.enabled is not None:
        data["enabled"] = bool(req.enabled)
    if req.interval_seconds is not None:
        data["interval_seconds"] = req.interval_seconds
    if req.cooldown_seconds is not None:
        data["cooldown_seconds"] = req.cooldown_seconds
    _save(data)
    logger.info("更新通知中心配置：%s", data)
    return {
        "enabled": data["enabled"],
        "interval_seconds": data["interval_seconds"],
        "cooldown_seconds": data["cooldown_seconds"],
    }


@router.post("/test-alert")
async def trigger_test_alert():
    """手动触发一次测试告警（验证规则→通知全链路）。"""
    import asyncio

    await asyncio.to_thread(_check_once)
    return {"ok": True}


@router.get("/logs")
async def list_logs(limit: int = 100):
    """返回告警记录（按时间倒序）。"""
    logs = _load_logs()
    logs = sorted(logs, key=lambda x: x.get("time", ""), reverse=True)
    return {"logs": logs[: max(1, min(int(limit or 100), MAX_LOG_ENTRIES))]}


@router.post("/logs/clear")
async def clear_logs():
    """清空告警记录。"""
    _save_logs([])
    return {"ok": True}
