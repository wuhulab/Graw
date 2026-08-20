# -*- coding: utf-8 -*-
"""
certcheck.py - Graw 证书到期提醒路由

功能：
  1. 定期检查面板管理的 SSL 证书（backend/data/ssl.json 中的 cert_path），
     用 cryptography 解析 X.509 证书的到期时间，计算剩余天数。
  2. 按可配置的提醒阈值（默认剩余 30 天 / 7 天）在证书即将到期时推送通知；
     证书已过期也推送告警。每档阈值只提醒一次（记录已提醒档位，防重复轰炸）。
  3. 后台循环在 main.py lifespan 启动，默认每天检查一次。

说明：
  - 证书到期信息实时解析（不持久化状态），每次检查读取 ssl.json + 解析证书。
  - 推送复用通知中心 notify.push_all，自动广播到所有启用渠道。
  - 证书路径来自 ssl.json（面板自身生成的证书文件），读取前做存在性校验。

数据存储：
  backend/data/certcheck.json :
    { enabled, interval_seconds, remind_days: [30,7], reminded: {cert_id: [30,7,...]} }
"""
import asyncio
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.hostfs import host_path

logger = logging.getLogger("graw.certcheck")

router = APIRouter()

# ---------------------------------------------------------------------------
# 常量与全局状态
# ---------------------------------------------------------------------------
DATA_DIR = os.path.normpath(
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data"))
)
CERTCHECK_FILE = os.path.join(DATA_DIR, "certcheck.json")
SSL_FILE = os.path.join(DATA_DIR, "ssl.json")

# 默认配置：每天检查，剩余 30 天 / 7 天两档提醒
DEFAULT_CONFIG = {
    "enabled": True,
    "interval_seconds": 86400,
    "remind_days": [30, 7],
    "reminded": {},
}

_lock = threading.Lock()
_monitor_task = None


def _load() -> dict:
    if not os.path.exists(CERTCHECK_FILE):
        return dict(DEFAULT_CONFIG)
    try:
        with open(CERTCHECK_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning("读取 certcheck.json 失败，按默认处理: %s", e)
        return dict(DEFAULT_CONFIG)
    if not isinstance(data, dict):
        return dict(DEFAULT_CONFIG)
    for k, v in DEFAULT_CONFIG.items():
        data.setdefault(k, v)
    return data


def _save(data: dict):
    with _lock:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = CERTCHECK_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CERTCHECK_FILE)


def _load_ssl_certs() -> list:
    """读取 ssl.json 的证书列表；损坏返回空。"""
    if not os.path.exists(SSL_FILE):
        return []
    try:
        with open(SSL_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("certs", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])
    except Exception as e:
        logger.warning("读取 ssl.json 失败: %s", e)
        return []


# ---------------------------------------------------------------------------
# 证书到期解析
# ---------------------------------------------------------------------------
def _parse_not_after(cert_path: str) -> Optional[datetime]:
    """解析 PEM 证书文件的到期时间（宿主机路径 -> 容器内实际路径）。

    解析失败返回 None（文件不存在 / 非 PEM / 解析异常），调用方按「无法解析」处理。
    """
    real = host_path(cert_path)
    if not os.path.isfile(real):
        return None
    try:
        from cryptography import x509
        from cryptography.hazmat.backends import default_backend

        with open(real, "rb") as f:
            cert = x509.load_pem_x509_certificate(f.read(), default_backend())
        return cert.not_valid_after_utc
    except Exception as e:
        logger.debug("解析证书 %s 到期时间失败: %s", cert_path, e)
        return None


def _cert_status(cert: dict) -> dict:
    """计算单个证书的到期状态。"""
    cert_path = cert.get("cert_path") or ""
    not_after = _parse_not_after(cert_path) if cert_path else None
    if not_after is None:
        return {
            "id": cert.get("id", ""),
            "name": cert.get("name", ""),
            "domains": cert.get("domains", []),
            "cert_path": cert_path,
            "expiry": None,
            "days_left": None,
            "status": "unknown",  # unknown | ok | warn | expired
            "parse_error": not bool(cert_path),
        }
    now = datetime.now(timezone.utc)
    days_left = (not_after - now).total_seconds() / 86400
    if days_left < 0:
        status = "expired"
    elif days_left <= 7:
        status = "warn"
    elif days_left <= 30:
        status = "warn"
    else:
        status = "ok"
    return {
        "id": cert.get("id", ""),
        "name": cert.get("name", ""),
        "domains": cert.get("domains", []),
        "cert_path": cert_path,
        "expiry": not_after.isoformat(),
        "days_left": round(days_left, 1),
        "status": status,
    }


def _check_once() -> int:
    """执行一次到期检查：对每档阈值/过期只提醒一次，返回提醒数。

    去重机制：certcheck.json 的 reminded[证书id] 记录已提醒过的档位
    （"expired" 或阈值天数）。同一档位不重复推送，避免每天轰炸。
    """
    cfg = _load()
    if not cfg.get("enabled", True):
        return 0
    remind_days = sorted({int(d) for d in cfg.get("remind_days", [30, 7]) if d and d > 0}, reverse=True)
    reminded = cfg.setdefault("reminded", {})
    triggered = 0
    for cert in _load_ssl_certs():
        cid = cert.get("id", "")
        st = _cert_status(cert)
        done_levels = set(reminded.get(cid, []))
        message = None
        level = None
        if st["status"] == "unknown":
            continue
        if st["status"] == "expired":
            if "expired" not in done_levels:
                level = "expired"
                message = (
                    f"【Graw 证书告警】证书 {st['name'] or '/'.join(st['domains'])}"
                    f" 已于 {st['expiry'][:10]} 过期，请立即续期"
                )
        else:
            days = st["days_left"]
            for t in remind_days:
                if days <= t and t not in done_levels:
                    level = t
                    message = (
                        f"【Graw 证书提醒】证书 {st['name'] or '/'.join(st['domains'])}"
                        f" 将于 {t} 天内到期（{st['expiry'][:10]}），剩余 {max(0, int(days))} 天，请及时续期"
                    )
                    break
        if message and level is not None:
            from app.routers.notify import push_all

            sent, failed = push_all(message)
            reminded.setdefault(cid, []).append(level)
            triggered += 1
            logger.info("证书到期提醒：%s（档位 %s，推送 %d/%d）", cid, level, sent, failed)
    if triggered:
        _save(cfg)
    return triggered


async def _monitor_loop():
    """后台循环：按配置间隔周期性检查。"""
    while True:
        try:
            await asyncio.to_thread(_check_once)
        except Exception as e:
            logger.error("证书到期检查失败: %s", e)
        interval = max(3600, int(_load().get("interval_seconds") or 86400))
        await asyncio.sleep(interval)


async def start_monitor():
    """启动后台监控（幂等）。"""
    global _monitor_task
    if _monitor_task is not None and not _monitor_task.done():
        return
    _monitor_task = asyncio.create_task(_monitor_loop())
    logger.info("证书到期后台监控已启动")


async def stop_monitor():
    """停止后台监控。"""
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
class ConfigRequest(BaseModel):
    enabled: Optional[bool] = None
    interval_seconds: Optional[int] = Field(None, ge=3600, le=604800)
    remind_days: Optional[List[int]] = Field(None, max_length=10)


# ---------------------------------------------------------------------------
# API 端点
# ---------------------------------------------------------------------------
@router.get("/status")
async def status():
    """证书到期提醒状态摘要。"""
    import asyncio

    cfg = _load()
    certs = await asyncio.to_thread(_check_certs_sync)
    enabled = [c for c in certs if c["status"] != "unknown"]
    return {
        "enabled": cfg.get("enabled", True),
        "interval_seconds": cfg.get("interval_seconds", 86400),
        "remind_days": cfg.get("remind_days", [30, 7]),
        "cert_count": len(enabled),
        "warn_count": sum(1 for c in enabled if c["status"] == "warn"),
        "expired_count": sum(1 for c in enabled if c["status"] == "expired"),
    }


def _check_certs_sync() -> list:
    return [_cert_status(c) for c in _load_ssl_certs()]


@router.get("/certs")
async def certs():
    """返回证书到期状态列表。"""
    import asyncio

    return {"certs": await asyncio.to_thread(_check_certs_sync)}


@router.post("/test")
async def trigger_check():
    """手动触发一次到期检查（推送未提醒过的阈值档）。"""
    import asyncio

    n = await asyncio.to_thread(_check_once)
    return {"ok": True, "triggered": n}


@router.put("/config")
async def update_config(req: ConfigRequest):
    """更新证书到期提醒配置。"""
    cfg = _load()
    if req.enabled is not None:
        cfg["enabled"] = bool(req.enabled)
    if req.interval_seconds is not None:
        cfg["interval_seconds"] = req.interval_seconds
    if req.remind_days is not None:
        days = sorted({int(d) for d in req.remind_days if d and d > 0}, reverse=True)
        cfg["remind_days"] = days or [30]
    _save(cfg)
    return {
        "enabled": cfg["enabled"],
        "interval_seconds": cfg["interval_seconds"],
        "remind_days": cfg["remind_days"],
    }
