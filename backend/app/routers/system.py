from fastapi import APIRouter
import psutil
import platform
import socket
import time
import asyncio
from datetime import datetime

from app.hostfs import host_path

router = APIRouter()

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


@router.get("/overview")
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


@router.get("/network")
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


@router.get("/diskio")
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


@router.get("/info")
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
