# -*- coding: utf-8 -*-
"""
uptime.py - Graw 站点可用性检测路由

功能：
  1. 监控网站/服务可用性：定期 HTTP 探测指定 URL，校验预期状态码与响应时间。
  2. 状态机告警：网站从正常变为不可达时推送「宕机」通知；恢复时推送「恢复」
     通知（复用通知中心 notify.push_all，自动广播到所有启用渠道）。
  3. 手动探测：对单个监控项立即执行一次探测并返回结果。

设计说明：
  - 后台循环在 main.py lifespan 启动，每 10s tick 一次，遍历监控项，
    仅对「已启用且到达探测间隔」的项执行探测（探测用 asyncio.to_thread，
    不阻塞事件循环）。
  - 每个监控项附带最近 10 条探测历史（环形），前端可查看趋势。
  - 探测先 HEAD 后 GET 回退（部分服务器不支持 HEAD）。

数据存储：
  backend/data/uptime.json :
    { "items": [ {id/name/url/expect_status/timeout_seconds/interval_seconds/
                  enabled/created_at/last_status/last_code/last_latency_ms/
                  last_checked_at/down_since/history[]} ] }

安全：
  - URL 仅允许 http/https scheme；其余字段长度限制防脏数据。
"""
import asyncio
import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from app.ssrf_guard import assert_safe_http_url

logger = logging.getLogger("graw.uptime")

router = APIRouter()

# ---------------------------------------------------------------------------
# 常量与全局状态
# ---------------------------------------------------------------------------
DATA_DIR = os.path.normpath(
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data"))
)
UPTIME_FILE = os.path.join(DATA_DIR, "uptime.json")

# 后台 tick 间隔（秒）：用于调度各监控项的到期探测
TICK_SECONDS = 10
# 每个监控项保留的探测历史条数（环形）
MAX_HISTORY = 10

_lock = threading.Lock()
_monitor_task = None


def _default() -> dict:
    return {"items": []}


def _load() -> dict:
    if not os.path.exists(UPTIME_FILE):
        return _default()
    try:
        with open(UPTIME_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning("读取 uptime.json 失败，按默认处理: %s", e)
        return _default()
    if not isinstance(data, dict):
        return _default()
    data.setdefault("items", [])
    return data


def _save(data: dict):
    with _lock:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = UPTIME_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, UPTIME_FILE)


def _find_item(items: list, item_id: str) -> dict:
    item = next((i for i in items if i.get("id") == item_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="监控项不存在")
    return item


def _validate_url(url: str) -> str:
    url = (url or "").strip()
    if len(url) > 2048:
        raise HTTPException(status_code=400, detail="监控地址过长")
    # SSRF 防护：拒绝回环 / 链路本地（含云 metadata 169.254.169.254）/ 保留 /
    # 私网地址。内网监控可用环境变量 GRAW_UPTIME_ALLOW_PRIVATE_NET=1 显式放开，
    # 但回环 / 链路本地 / 保留地址始终拒绝。
    allow_private = os.environ.get("GRAW_UPTIME_ALLOW_PRIVATE_NET") == "1"
    try:
        assert_safe_http_url(url, allow_private=allow_private)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return url


# ---------------------------------------------------------------------------
# 探测与状态机
# ---------------------------------------------------------------------------
def _probe_url(url: str, timeout_seconds: int) -> dict:
    """对 URL 执行一次探测（HEAD 优先，失败回退 GET），返回结果摘要。

    返回：
      {status: "ok"|"down", code: int|None, latency_ms: float|None, error: str|None}
    """
    import requests

    timeout = max(1, min(int(timeout_seconds or 10), 60))
    start = time.time()
    # 每次探测重新校验目标 + 主机固定（第十四轮审计修复）：
    # 此前「校验解析」与「requests 实连解析」各自 DNS 解析一次，存在 DNS
    # rebinding TOCTOU 窗口（校验到公网 IP、实连解析到内网）。pin_http_url
    # 把 http URL 的 host 替换为已校验的解析 IP，校验与实际连接同源。
    allow_private = os.environ.get("GRAW_UPTIME_ALLOW_PRIVATE_NET") == "1"
    try:
        from app.ssrf_guard import pin_http_url
        url, host_hdr = pin_http_url(url, allow_private=allow_private)
    except ValueError as e:
        return {"status": "down", "code": None, "latency_ms": None, "error": str(e)[:200]}
    headers = {"Host": host_hdr} if host_hdr else None
    for method in ("HEAD", "GET"):
        try:
            r = requests.request(method, url, timeout=timeout, allow_redirects=False, headers=headers)
            latency = round((time.time() - start) * 1000, 1)
            return {"status": "ok", "code": r.status_code, "latency_ms": latency, "error": None}
        except requests.exceptions.Timeout:
            return {"status": "down", "code": None, "latency_ms": None, "error": "连接超时"}
        except requests.RequestException as e:
            # HEAD 失败（如服务器不支持）则尝试 GET；GET 也失败才判 down
            if method == "GET":
                latency = round((time.time() - start) * 1000, 1)
                return {"status": "down", "code": None, "latency_ms": latency, "error": str(e)[:200]}
            continue
    return {"status": "down", "code": None, "latency_ms": None, "error": "请求失败"}


def _probe_and_alert(item: dict) -> dict:
    """对单个监控项执行一次探测，更新状态与历史；状态变化时推送通知。

    返回探测结果摘要（供手动 test 端点直接返回）。
    """
    result = _probe_url(item["url"], item.get("timeout_seconds") or 10)
    now = datetime.now().isoformat()
    # 状态码与预期不符也视为 down
    expect = int(item.get("expect_status") or 200)
    is_ok = result["status"] == "ok" and result["code"] == expect
    current = "ok" if is_ok else "down"
    if not is_ok and result["status"] == "ok":
        result["error"] = f"状态码 {result['code']} 与预期 {expect} 不符"

    prev = item.get("last_status")  # 上次结果（"ok"/"down"/None）

    # 更新状态字段
    item["last_status"] = current
    item["last_code"] = result["code"]
    item["last_latency_ms"] = result["latency_ms"]
    item["last_checked_at"] = now
    if current == "down" and prev != "down":
        item["down_since"] = now
    elif current == "ok":
        item["down_since"] = ""

    # 写历史（环形）
    history = item.setdefault("history", [])
    history.append({
        "time": now,
        "status": current,
        "code": result["code"],
        "latency_ms": result["latency_ms"],
    })
    if len(history) > MAX_HISTORY:
        del history[: len(history) - MAX_HISTORY]

    # 状态变化时推送通知（首次探测 prev 为空 → 不推送，避免开机轰炸）
    if prev is not None and prev != current:
        _push_status_change(item, current, result)
    return {**result, "status": current, "checked_at": now}


def _push_status_change(item: dict, current: str, result: dict) -> None:
    """状态变化时推送到通知中心所有启用渠道。"""
    from app.routers.notify import push_all

    name = item.get("name") or item["id"]
    url = item["url"]
    if current == "down":
        detail = result.get("error") or f"HTTP {result.get('code')}"
        message = f"【Graw 站点告警】{name}（{url}）不可访问：{detail}"
    else:
        latency = result.get("latency_ms")
        latency_txt = f"{latency}ms" if latency is not None else "—"
        message = f"【Graw 站点恢复】{name}（{url}）已恢复正常：HTTP {result.get('code')}，延迟 {latency_txt}"
    sent, failed = push_all(message)
    logger.info("站点状态变化 %s：%s -> %s（推送 %d/%d）", name, current, message, sent, failed)


# ---------------------------------------------------------------------------
# 后台监控
# ---------------------------------------------------------------------------
def _tick_once() -> int:
    """执行一轮调度：探测所有已到期且启用的监控项，返回探测数。"""
    data = _load()
    now = time.time()
    probed = 0
    for item in data.get("items", []):
        if not item.get("enabled", True):
            continue
        last = item.get("last_checked_ts") or 0
        interval = max(10, int(item.get("interval_seconds") or 60))
        if now - last < interval:
            continue
        try:
            _probe_and_alert(item)
            item["last_checked_ts"] = time.time()
            probed += 1
        except Exception as e:
            logger.error("探测 %s 失败: %s", item.get("id"), e)
    if probed:
        _save(data)
    return probed


async def _monitor_loop():
    """后台监控循环：每 TICK_SECONDS 秒执行一轮到期探测。"""
    while True:
        try:
            await asyncio.to_thread(_tick_once)
        except Exception as e:
            logger.error("站点可用性检查失败: %s", e)
        await asyncio.sleep(TICK_SECONDS)


async def start_monitor():
    """启动后台监控（幂等）。"""
    global _monitor_task
    if _monitor_task is not None and not _monitor_task.done():
        return
    _monitor_task = asyncio.create_task(_monitor_loop())
    logger.info("站点可用性后台监控已启动")


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
class ItemRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    url: str
    expect_status: int = Field(200, ge=100, le=599)
    timeout_seconds: int = Field(10, ge=1, le=60)
    interval_seconds: int = Field(60, ge=10, le=86400)
    enabled: Optional[bool] = True


class ItemUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=64)
    url: Optional[str] = None
    expect_status: Optional[int] = Field(None, ge=100, le=599)
    timeout_seconds: Optional[int] = Field(None, ge=1, le=60)
    interval_seconds: Optional[int] = Field(None, ge=10, le=86400)
    enabled: Optional[bool] = None


# ---------------------------------------------------------------------------
# API 端点
# ---------------------------------------------------------------------------
@router.get("/status")
async def status():
    """站点可用性监控状态摘要。"""
    data = _load()
    items = data.get("items", [])
    enabled = [i for i in items if i.get("enabled", True)]
    up = sum(1 for i in enabled if i.get("last_status") == "ok")
    down = sum(1 for i in enabled if i.get("last_status") == "down")
    return {
        "item_count": len(items),
        "enabled_count": len(enabled),
        "up_count": up,
        "down_count": down,
    }


@router.get("/items")
async def list_items():
    """返回监控项列表（含当前状态与探测历史）。"""
    data = _load()
    return {"items": data.get("items", [])}


@router.post("/items")
async def create_item(req: ItemRequest):
    """创建监控项。"""
    item = {
        "id": "up_" + uuid.uuid4().hex[:10],
        "name": (req.name or "").strip(),
        "url": _validate_url(req.url),
        "expect_status": req.expect_status,
        "timeout_seconds": req.timeout_seconds,
        "interval_seconds": req.interval_seconds,
        "enabled": bool(req.enabled if req.enabled is not None else True),
        "created_at": datetime.now().isoformat(),
        "last_status": None,
        "last_code": None,
        "last_latency_ms": None,
        "last_checked_at": "",
        "last_checked_ts": 0,
        "down_since": "",
        "history": [],
    }
    data = _load()
    data.setdefault("items", []).append(item)
    _save(data)
    logger.info("创建站点监控项：%s（%s）", item["name"], item["url"])
    return item


@router.put("/items/{item_id}")
async def update_item(item_id: str, req: ItemUpdateRequest):
    """更新监控项。"""
    data = _load()
    item = _find_item(data.get("items", []), item_id)
    if req.name is not None:
        item["name"] = (req.name or "").strip()
    if req.url is not None:
        item["url"] = _validate_url(req.url)
    if req.expect_status is not None:
        item["expect_status"] = req.expect_status
    if req.timeout_seconds is not None:
        item["timeout_seconds"] = req.timeout_seconds
    if req.interval_seconds is not None:
        item["interval_seconds"] = req.interval_seconds
    if req.enabled is not None:
        item["enabled"] = bool(req.enabled)
    _save(data)
    return item


@router.delete("/items/{item_id}")
async def delete_item(item_id: str):
    """删除监控项。"""
    data = _load()
    before = len(data.get("items", []))
    data["items"] = [i for i in data.get("items", []) if i.get("id") != item_id]
    if len(data["items"]) == before:
        raise HTTPException(status_code=404, detail="监控项不存在")
    _save(data)
    return {"ok": True}


@router.post("/items/{item_id}/test")
async def test_item(item_id: str):
    """手动立即探测一次监控项。"""
    import asyncio

    data = _load()
    item = _find_item(data.get("items", []), item_id)
    result = await asyncio.to_thread(_probe_and_alert, item)
    _save(data)
    return result
