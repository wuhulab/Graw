from typing import Optional
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
import psutil
import platform
import socket
import time
import asyncio
from datetime import datetime

from app.hostfs import host_path
from app.auth import get_current_user, require_non_default_password, get_current_user_ws

router = APIRouter()

# 鉴权：HTTP 只读接口沿用「登录 + 非默认密码」；
# WebSocket 无法携带 Bearer 头，改用 ?token= 查询参数鉴权（get_current_user_ws）。
_PROTECTED = [Depends(get_current_user), Depends(require_non_default_password)]

# Prime psutil.cpu_percent so subsequent calls with interval=None return meaningful
# values instead of 0.0 on the very first invocation.
psutil.cpu_percent(interval=None)

_last_net = {"time": time.time(), "sent": psutil.net_io_counters().bytes_sent, "recv": psutil.net_io_counters().bytes_recv}
_last_disk = {"time": time.time(), "read": 0, "write": 0}
try:
    _d = psutil.disk_io_counters()
    if _d:
        _last_disk = {"time": time.time(), "read": _d.read_bytes, "write": _d.write_bytes}
except Exception:
    pass


@router.get("/overview", dependencies=_PROTECTED)
async def overview():
    return await asyncio.to_thread(_overview_sync)


def _overview_sync():
    cpu_percent = psutil.cpu_percent(interval=None)
    mem = psutil.virtual_memory()
    # 容器模式下监控宿主机根目录磁盘（映射为 /host），否则为 / 或 C:\
    disk_path = host_path("/") if platform.system() != "Windows" else "C:\\"
    disk = psutil.disk_usage(disk_path)
    try:
        load1, load5, load15 = psutil.getloadavg()
    except Exception:
        load1 = cpu_percent / 100 * psutil.cpu_count()
        load5 = load1
        load15 = load1
    load_percent = min(100, (load1 / max(1, psutil.cpu_count())) * 100)
    return {
        "cpu": round(cpu_percent, 1),
        "memory": {
            "percent": round(mem.percent, 1),
            "total": mem.total,
            "used": mem.used,
            "available": mem.available,
        },
        "storage": {
            "percent": round(disk.percent, 1),
            "total": disk.total,
            "used": disk.used,
            "free": disk.free,
        },
        "load": {
            "percent": round(load_percent, 1),
            "load1": round(load1, 2),
            "load5": round(load5, 2),
            "load15": round(load15, 2),
        },
    }


@router.get("/network", dependencies=_PROTECTED)
async def network():
    return await asyncio.to_thread(_network_sync)


def _network_sync():
    global _last_net
    now = time.time()
    counters = psutil.net_io_counters()
    elapsed = max(0.001, now - _last_net["time"])
    up_speed = (counters.bytes_sent - _last_net["sent"]) / elapsed
    down_speed = (counters.bytes_recv - _last_net["recv"]) / elapsed
    _last_net = {"time": now, "sent": counters.bytes_sent, "recv": counters.bytes_recv}
    return {
        "timestamp": int(now * 1000),
        "upload": max(0, up_speed),
        "download": max(0, down_speed),
        "total_sent": counters.bytes_sent,
        "total_recv": counters.bytes_recv,
    }


@router.get("/diskio", dependencies=_PROTECTED)
async def diskio():
    return await asyncio.to_thread(_diskio_sync)


def _diskio_sync():
    global _last_disk
    now = time.time()
    counters = psutil.disk_io_counters()
    if not counters:
        return {"timestamp": int(now * 1000), "read": 0, "write": 0}
    elapsed = max(0.001, now - _last_disk["time"])
    read_speed = (counters.read_bytes - _last_disk["read"]) / elapsed
    write_speed = (counters.write_bytes - _last_disk["write"]) / elapsed
    _last_disk = {"time": now, "read": counters.read_bytes, "write": counters.write_bytes}
    return {
        "timestamp": int(now * 1000),
        "read": max(0, read_speed),
        "write": max(0, write_speed),
    }


@router.get("/info", dependencies=_PROTECTED)
async def info():
    return await asyncio.to_thread(_info_sync)


def _info_sync():
    boot_time = psutil.boot_time()
    uptime_seconds = int(time.time() - boot_time)
    return {
        "hostname": socket.gethostname(),
        "system": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "python_version": platform.python_version(),
        "cpu_count": psutil.cpu_count(logical=True),
        "cpu_count_physical": psutil.cpu_count(logical=False),
        "boot_time": datetime.fromtimestamp(boot_time).isoformat(),
        "uptime_seconds": uptime_seconds,
    }


# ------------------------------------------------------------
# 统一系统指标 WebSocket
# 将原本由前端三个独立轮询（overview / network / diskio / info）拉取的数据，
# 合并为单条 /api/system/ws 推送，由后端「单生产者」定时采集并广播给所有已连接客户端，
# 从而减少连接数并避免每个客户端各自占用一次采集。
# ------------------------------------------------------------
# 每隔多久向所有 WS 客户端推送一次合并指标
_METRICS_INTERVAL = 2.0
# 已连接的系统指标 WebSocket 客户端集合
_ws_clients: set = set()
# 最近一次采集的合并指标缓存（供新客户端连入时立即回放）
_metrics_cache: Optional[dict] = None


def _collect_sync() -> dict:
    """同步采集全部指标并合并为一个 payload（overview + network + diskio + info）。"""
    return {
        "overview": _overview_sync(),
        "network": _network_sync(),
        "diskio": _diskio_sync(),
        "info": _info_sync(),
    }


async def _metrics_producer():
    """后台采集协程：周期性采集指标并广播给所有已连接的 WS 客户端。

    采用单生产者模式，即便同时存在多个 WS 客户端，也只做一次采集，
    避免每个客户端各自触发 psutil 采样（连接池优化）。
    """
    global _metrics_cache
    while True:
        try:
            # to_thread 保证 psutil 的阻塞采样不阻塞事件循环
            _metrics_cache = await asyncio.to_thread(_collect_sync)
        except Exception:
            # 采集失败时保留上一次缓存，避免频繁报错
            pass
        if _metrics_cache is not None:
            dead = []
            payload = {"type": "metrics", "data": _metrics_cache}
            for ws in list(_ws_clients):
                try:
                    await ws.send_json(payload)
                except Exception:
                    dead.append(ws)
            for ws in dead:
                _ws_clients.discard(ws)
        await asyncio.sleep(_METRICS_INTERVAL)


_producer_task: Optional[asyncio.Task] = None


async def start_metrics_producer():
    """在应用启动时预热一次并启动后台采集协程。重复调用是安全的。"""
    global _producer_task, _metrics_cache
    try:
        _metrics_cache = await asyncio.to_thread(_collect_sync)
    except Exception:
        pass
    if _producer_task is None or _producer_task.done():
        _producer_task = asyncio.create_task(_metrics_producer())


async def stop_metrics_producer():
    """停止后台采集协程（关闭连接）。"""
    global _producer_task
    if _producer_task is not None:
        _producer_task.cancel()
        try:
            await _producer_task
        except asyncio.CancelledError:
            pass
        _producer_task = None


@router.websocket("/ws")
async def system_ws(websocket: WebSocket, user: Optional[dict] = Depends(get_current_user_ws)):
    """统一系统指标 WebSocket。

    通过 ?token= 鉴权（get_current_user_ws）；鉴权失败时依赖内部已关闭连接，
    此处直接返回。鉴权通过后订阅后台生产者的广播，并先回放一次缓存数据。
    """
    if user is None:
        # get_current_user_ws 内部已在鉴权失败时 close(4401)
        return
    await websocket.accept()
    _ws_clients.add(websocket)
    # 新客户端连入立即回放最近一次指标，无需等待下一个生产周期
    if _metrics_cache is not None:
        try:
            await websocket.send_json({"type": "metrics", "data": _metrics_cache})
        except Exception:
            _ws_clients.discard(websocket)
            try:
                await websocket.close()
            except Exception:
                pass
            return
    try:
        # 保持连接存活；生产者负责发送数据
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        _ws_clients.discard(websocket)
        try:
            await websocket.close()
        except Exception:
            pass
