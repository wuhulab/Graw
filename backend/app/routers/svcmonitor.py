# -*- coding: utf-8 -*-
"""
svcmonitor.py - 服务/端口监控路由

功能：
  1. 自定义监控项：支持「TCP 端口」「进程」「systemd 服务」三类目标。
  2. 后台循环按各自探测间隔检查：端口连通性 / 进程是否存在 / 服务是否 active。
  3. 状态机告警：正常 -> 异常推送「故障」通知；恢复推送「恢复」通知
     （复用通知中心 notify.push_all，自动广播到所有启用渠道）。
  4. 手动探测：对单个监控项立即执行一次检查并返回结果。

探测实现：
  - port：socket.create_connection 探测 host:port（默认 127.0.0.1）。
  - process：psutil 进程名/命令行匹配。
  - service：systemctl is-active 判断（仅 Linux 有效；Windows 返回 unknown）。

数据存储：
  backend/data/svcmonitor.json :
    { "items": [ {id/name/kind/target/interval_seconds/enabled/created_at/
                  last_status/last_detail/last_checked_at/down_since/history[]} ] }

安全：
  - target 仅允许安全字符集与长度限制；端口范围校验 1-65535。
  - 探测用 asyncio.to_thread 放入线程池，不阻塞事件循环。
"""
import asyncio
import json
import logging
import os
import re
import socket
import threading
import time
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app import node_manager

logger = logging.getLogger("graw.svcmonitor")

router = APIRouter()

# ---------------------------------------------------------------------------
# 常量与全局状态
# ---------------------------------------------------------------------------
DATA_DIR = os.path.normpath(
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data"))
)
SVC_FILE = os.path.join(DATA_DIR, "svcmonitor.json")

# 后台 tick 间隔（秒）：用于调度各监控项的到期探测
TICK_SECONDS = 10
# 每个监控项保留的探测历史条数（环形）
MAX_HISTORY = 10
# target 允许的字符（端口/进程名/服务名均适用，阻断注入）
_TARGET_RE = re.compile(r"^[A-Za-z0-9_.:/@-]{1,128}$")

_lock = threading.Lock()
_monitor_task = None


def _default() -> dict:
    return {"items": []}


def _load() -> dict:
    if not os.path.exists(SVC_FILE):
        return _default()
    try:
        with open(SVC_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning("读取 svcmonitor.json 失败，按默认处理: %s", e)
        return _default()
    if not isinstance(data, dict):
        return _default()
    data.setdefault("items", [])
    return data


def _save(data: dict):
    with _lock:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = SVC_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, SVC_FILE)


def _find_item(items: list, item_id: str) -> dict:
    item = next((i for i in items if i.get("id") == item_id), None)
    if not item:
        raise HTTPException(status_code=404, detail="监控项不存在")
    return item


# ---------------------------------------------------------------------------
# 探测实现（线程安全，无共享状态）
# ---------------------------------------------------------------------------
def _probe_port(target: str, timeout_seconds: int) -> dict:
    """TCP 端口连通性探测。target 格式：host:port 或 port（默认 127.0.0.1）。"""
    host = "127.0.0.1"
    port = target
    if ":" in target:
        host, _, port = target.rpartition(":")
    try:
        port = int(port)
    except (TypeError, ValueError):
        return {"status": "down", "detail": "端口号非法"}
    if not 1 <= port <= 65535:
        return {"status": "down", "detail": "端口号超出范围 1-65535"}
    timeout = max(1, min(int(timeout_seconds or 5), 30))
    start = time.time()
    try:
        with socket.create_connection((host, port), timeout=timeout):
            latency_ms = round((time.time() - start) * 1000, 1)
            return {"status": "ok", "detail": f"连接成功（{latency_ms}ms）"}
    except socket.timeout:
        return {"status": "down", "detail": "连接超时"}
    except OSError as e:
        return {"status": "down", "detail": str(e)[:200]}


def _probe_process(target: str) -> dict:
    """进程探测：匹配进程名或命令行包含 target 的进程。"""
    try:
        import psutil

        name = target.strip()
        for p in psutil.process_iter(["name", "cmdline"]):
            try:
                pname = p.info.get("name") or ""
                cmdline = p.info.get("cmdline") or []
                if name in pname or any(name in (c or "") for c in cmdline):
                    return {"status": "ok", "detail": f"进程存在（PID {p.pid}）"}
            except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                continue
        return {"status": "down", "detail": "未找到匹配进程"}
    except Exception as e:
        return {"status": "down", "detail": f"进程探测失败：{str(e)[:200]}"}


def _probe_service(target: str) -> dict:
    """systemd 服务探测：systemctl is-active。仅 Linux 有意义。"""
    if os.name != "posix":
        return {"status": "unknown", "detail": "systemd 服务检测仅支持 Linux"}
    r = node_manager.host_cmd(
        ["systemctl", "is-active", target],
        capture_output=True,
        text=True,
        timeout=10,
    )
    out = (r.stdout or "").strip()
    if r.returncode == 0 and out in ("active", "activating"):
        return {"status": "ok", "detail": f"服务状态：{out}"}
    if r.returncode == 127:
        return {"status": "unknown", "detail": "宿主缺少 systemctl"}
    return {"status": "down", "detail": f"服务未运行（{out or 'exit ' + str(r.returncode)}）"}


def _probe_item(item: dict) -> dict:
    """按 kind 分发探测，返回 {status, detail}。"""
    kind = item.get("kind", "port")
    target = item.get("target", "")
    timeout_seconds = item.get("timeout_seconds") or 5
    if kind == "port":
        return _probe_port(target, timeout_seconds)
    if kind == "process":
        return _probe_process(target)
    if kind == "service":
        return _probe_service(target)
    return {"status": "unknown", "detail": f"未知监控类型：{kind}"}


def _probe_and_alert(item: dict) -> dict:
    """对单个监控项执行一次探测，更新状态与历史；状态变化时推送通知。"""
    result = _probe_item(item)
    now = datetime.now().isoformat()

    prev = item.get("last_status")  # 上次结果（"ok"/"down"/"unknown"/None）
    current = result.get("status", "down")

    # 更新状态字段
    item["last_status"] = current
    item["last_detail"] = result.get("detail", "")
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
        "detail": result.get("detail", ""),
    })
    if len(history) > MAX_HISTORY:
        del history[: len(history) - MAX_HISTORY]

    # 状态变化时推送通知（首次探测 prev 为空 → 不推送，避免开机轰炸；
    # unknown 属于环境不支持，不触发告警）
    if prev is not None and prev != current and current in ("ok", "down"):
        _push_status_change(item, current, result)
    return {**result, "status": current, "checked_at": now}


def _push_status_change(item: dict, current: str, result: dict) -> None:
    """状态变化时推送到通知中心所有启用渠道。"""
    from app.routers.notify import push_all

    name = item.get("name") or item["id"]
    kind_label = {"port": "端口", "process": "进程", "service": "服务"}.get(
        item.get("kind"), item.get("kind")
    )
    target = item["target"]
    if current == "down":
        message = f"【Graw 服务告警】{name}（{kind_label} {target}）异常：{result.get('detail') or '不可用'}"
    else:
        message = f"【Graw 服务恢复】{name}（{kind_label} {target}）已恢复正常：{result.get('detail') or '可用'}"
    sent, failed = push_all(message)
    logger.info("服务状态变化 %s：%s（推送 %d/%d）", name, current, sent, failed)


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
            logger.error("服务/端口监控检查失败: %s", e)
        await asyncio.sleep(TICK_SECONDS)


async def start_monitor():
    """启动后台监控（幂等）。"""
    global _monitor_task
    if _monitor_task is not None and not _monitor_task.done():
        return
    _monitor_task = asyncio.create_task(_monitor_loop())
    logger.info("服务/端口后台监控已启动")


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
# 请求模型与校验
# ---------------------------------------------------------------------------
KINDS = ("port", "process", "service")


def _validate_target(kind: str, target: str) -> str:
    target = (target or "").strip()
    if not target:
        raise HTTPException(status_code=400, detail="监控目标不能为空")
    if not _TARGET_RE.match(target):
        raise HTTPException(status_code=400, detail="监控目标包含非法字符")
    if kind == "port":
        # 校验端口部分合法性
        port = target.rpartition(":")[2] if ":" in target else target
        try:
            if not 1 <= int(port) <= 65535:
                raise ValueError
        except ValueError:
            raise HTTPException(status_code=400, detail="端口号非法（1-65535）")
    return target


class ItemRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    kind: str = "port"
    target: str
    interval_seconds: int = Field(60, ge=10, le=86400)
    timeout_seconds: int = Field(5, ge=1, le=30)
    enabled: Optional[bool] = True


class ItemUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=64)
    kind: Optional[str] = None
    target: Optional[str] = None
    interval_seconds: Optional[int] = Field(None, ge=10, le=86400)
    timeout_seconds: Optional[int] = Field(None, ge=1, le=30)
    enabled: Optional[bool] = None


# ---------------------------------------------------------------------------
# API 端点
# ---------------------------------------------------------------------------
@router.get("/status")
async def status():
    """服务/端口监控状态摘要。"""
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
    kind = (req.kind or "port").strip().lower()
    if kind not in KINDS:
        raise HTTPException(status_code=400, detail="监控类型必须是 port/process/service")
    item = {
        "id": "svc_" + uuid.uuid4().hex[:10],
        "name": (req.name or "").strip(),
        "kind": kind,
        "target": _validate_target(kind, req.target),
        "interval_seconds": req.interval_seconds,
        "timeout_seconds": req.timeout_seconds,
        "enabled": bool(req.enabled if req.enabled is not None else True),
        "created_at": datetime.now().isoformat(),
        "last_status": None,
        "last_detail": "",
        "last_checked_at": "",
        "last_checked_ts": 0,
        "down_since": "",
        "history": [],
    }
    data = _load()
    data.setdefault("items", []).append(item)
    _save(data)
    logger.info("创建服务监控项：%s（%s %s）", item["name"], kind, item["target"])
    return item


@router.put("/items/{item_id}")
async def update_item(item_id: str, req: ItemUpdateRequest):
    """更新监控项。"""
    data = _load()
    item = _find_item(data.get("items", []), item_id)
    if req.name is not None:
        item["name"] = (req.name or "").strip()
    if req.kind is not None:
        kind = (req.kind or "").strip().lower()
        if kind not in KINDS:
            raise HTTPException(status_code=400, detail="监控类型必须是 port/process/service")
        item["kind"] = kind
    if req.target is not None:
        item["target"] = _validate_target(item.get("kind", "port"), req.target)
    if req.interval_seconds is not None:
        item["interval_seconds"] = req.interval_seconds
    if req.timeout_seconds is not None:
        item["timeout_seconds"] = req.timeout_seconds
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
    data = _load()
    item = _find_item(data.get("items", []), item_id)
    result = await asyncio.to_thread(_probe_and_alert, item)
    _save(data)
    return result
