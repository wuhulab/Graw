from typing import Optional
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends, Query, HTTPException
import psutil
import platform
import socket
import time
import os
import asyncio
import base64
import logging
import urllib.parse
from datetime import datetime

from app.hostfs import host_path
from app import node_manager
from app import metrics_store
from app import agent_client
from app.auth import (
    get_current_user,
    require_non_default_password,
    require_admin,
    get_current_user_ws_checked,
)

logger = logging.getLogger("graw.system")

router = APIRouter()

# 鉴权：HTTP 只读接口沿用「登录 + 非默认密码」；
# WebSocket 无法携带 Bearer 头，改用 ?token= 查询参数鉴权（get_current_user_ws）。
_PROTECTED = [Depends(get_current_user), Depends(require_non_default_password)]


# ----------------------------------------------------------------------
# 远程节点采样辅助：当当前管理主机为 SSH 节点时，改用系统命令读取指标，
# 这样「设置」切换主机后监控数据也随之切换。本地仍走 psutil。
# ----------------------------------------------------------------------
def _rrun(cmd: str) -> str:
    """远程执行命令并返回 stdout（失败返回空串）。"""
    r = node_manager.host_shell(cmd, capture_output=True, text=True, timeout=15)
    return r.stdout or ""


# 远端指标一次性采集：单次 SSH 连接执行一个脚本，把 CPU/内存/磁盘/负载/
# 网络/磁盘IO/系统信息全部取回，避免旧的每次一命令一连接（太重，2s 周期跑不完）。
# 解析约定：各段以 ===BLOCK<key>=== 打头，直到下一个块或结束。
_REMOTE_SCRIPT = r'''
echo "===BLOCKSTAT0==="; cat /proc/stat 2>/dev/null | head -1
sleep 0.12
echo "===BLOCKSTAT1==="; cat /proc/stat 2>/dev/null | head -1
echo "===BLOCKBTIME==="; cat /proc/stat 2>/dev/null | grep '^btime' | head -1
echo "===BLOCKMEM==="; cat /proc/meminfo 2>/dev/null
echo "===BLOCKDF==="; df -kP / 2>/dev/null | tail -1
echo "===BLOCKLOAD==="; cat /proc/loadavg 2>/dev/null
echo "===BLOCKNPROC==="; nproc 2>/dev/null
echo "===BLOCKNET==="; cat /proc/net/dev 2>/dev/null
echo "===BLOCKDISK==="; cat /proc/diskstats 2>/dev/null | awk '{print $6, $10}'
echo "===BLOCKNAME==="; hostname 2>/dev/null | head -1
echo "===BLOCKUNAME==="; uname -s 2>/dev/null | head -1; uname -r 2>/dev/null | head -1; uname -v 2>/dev/null | head -1; uname -m 2>/dev/null | head -1
echo "===BLOCKCPUINFO==="; grep 'model name' /proc/cpuinfo 2>/dev/null | head -1
'''


def _remote_fetch_all() -> dict:
    """单次 SSH 连接采集远端全部指标，按帧标记分段解析为 dict。"""
    r = node_manager.host_shell(_REMOTE_SCRIPT, capture_output=True, text=True, timeout=20)
    out = r.stdout or ""
    blocks: dict = {}
    cur = None
    buf = []
    for line in out.splitlines():
        if line.startswith("===BLOCK") and line.endswith("==="):
            if cur is not None:
                blocks[cur] = "\n".join(buf).strip()
            cur = line[len("===BLOCK"):-len("===")].strip()
            buf = []
        else:
            buf.append(line)
    if cur is not None:
        blocks[cur] = "\n".join(buf).strip()
    return blocks


# 最近一次远端采集结果的解析缓存：_overview/_network/_diskio/_info 各自解析一次，
# 避免每个指标都对同一份 blocks 做重复 SSH（采集本身已在一次连接内完成）。
# 带时间戳 TTL：一次采集（一次连接）后 2 秒内复用，超过则重新采集；
# 既能支撑 WebSocket 单生产者按周期刷新，也能让独立 HTTP 只读接口各自取到新数据。
_remote_blocks_cache: Optional[dict] = None
_remote_blocks_at = 0.0
_REMOTE_BLOCKS_TTL = 2.0


def _remote_blocks(force: bool = False) -> dict:
    """返回（或按需刷新）最近一次远端采集的分段数据。"""
    global _remote_blocks_cache, _remote_blocks_at
    now = time.time()
    if _remote_blocks_cache is None or force or (now - _remote_blocks_at) >= _REMOTE_BLOCKS_TTL:
        _remote_blocks_cache = _remote_fetch_all()
        _remote_blocks_at = time.time()
    return _remote_blocks_cache


def _remote_parse_stat0() -> tuple:
    parts = _remote_blocks().get("STAT0", "").split()
    if len(parts) < 5:
        return 0, 0
    return sum(int(x) for x in parts[1:] if x.isdigit()), int(parts[4])


def _remote_parse_stat1() -> tuple:
    parts = _remote_blocks().get("STAT1", "").split()
    if len(parts) < 5:
        return 0, 0
    return sum(int(x) for x in parts[1:] if x.isdigit()), int(parts[4])


def _remote_overview() -> dict:
    """远程：CPU/内存/磁盘/负载（单次连接已取回全部原始数据）。"""
    # CPU 利用率：脚本内两次 stat 采样差分
    t_pre, i_pre = _remote_parse_stat0()
    t_post, i_post = _remote_parse_stat1()
    cpu_pct = 0.0
    if t_post > t_pre:
        cpu_pct = 100.0 * (1 - (i_post - i_pre) / max(1, (t_post - t_pre)))

    # 内存
    meminfo = {}
    for line in _remote_blocks().get("MEM", "").splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meminfo[k.strip()] = int("".join(c for c in v if c.isdigit()) or 0)
    mem_total = meminfo.get("MemTotal", 0) * 1024
    mem_avail = meminfo.get("MemAvailable", mem_total) * 1024
    mem_used = max(0, mem_total - mem_avail)
    mem_percent = round(mem_used / mem_total * 100, 1) if mem_total else 0.0

    # 磁盘：/（远程即根）
    disk_res = {"percent": 0.0, "total": 0, "used": 0, "free": 0}
    parts = _remote_blocks().get("DF", "").split()
    if len(parts) >= 5 and parts[1].isdigit():
        total_kb, used_kb, avail_kb = int(parts[1]), int(parts[2]), int(parts[3])
        disk_res = {
            "percent": round(used_kb / total_kb * 100, 1) if total_kb else 0.0,
            "total": total_kb * 1024,
            "used": used_kb * 1024,
            "free": avail_kb * 1024,
        }

    # 负载
    ld = _remote_blocks().get("LOAD", "").split()
    load1 = load5 = load15 = 0.0
    if len(ld) >= 3:
        try:
            load1, load5, load15 = map(float, ld[:3])
        except ValueError:
            # 负载数值解析失败时保持默认 0.0
            pass
    ncores = int(_remote_blocks().get("NPROC", "").strip() or "1")
    return {
        "cpu": round(cpu_pct, 1),
        "memory": {"percent": mem_percent, "total": mem_total, "used": mem_used, "available": mem_avail},
        "storage": disk_res,
        "load": {"percent": round(min(100, load1 / max(1, ncores) * 100), 1), "load1": round(load1, 2), "load5": round(load5, 2), "load15": round(load15, 2)},
    }


_rlast_net = {"time": 0.0, "sent": 0, "recv": 0}
_rlast_disk = {"time": 0.0, "read": 0, "write": 0}


def _remote_net_bytes() -> dict:
    """远程：累加 /proc/net/dev 的字节计数（取自单次采集缓存）。"""
    sent = recv = 0
    for line in _remote_blocks().get("NET", "").splitlines():
        if ":" not in line:
            continue
        _, rest = line.split(":", 1)
        p = rest.split()
        if len(p) >= 9:
            recv += int(p[0] or 0)
            sent += int(p[8] or 0)
    return {"sent": sent, "recv": recv}


def _remote_network() -> dict:
    global _rlast_net
    now = time.time()
    counters = _remote_net_bytes()
    elapsed = max(0.001, now - _rlast_net["time"])
    up = (counters["sent"] - _rlast_net["sent"]) / elapsed
    down = (counters["recv"] - _rlast_net["recv"]) / elapsed
    _rlast_net = {"time": now, "sent": counters["sent"], "recv": counters["recv"]}
    return {"timestamp": int(now * 1000), "upload": max(0, up), "download": max(0, down), "total_sent": counters["sent"], "total_recv": counters["recv"]}


def _remote_diskio() -> dict:
    global _rlast_disk
    read = write = 0
    for line in _remote_blocks().get("DISK", "").splitlines():
        p = line.split()
        if len(p) >= 2 and p[0].isdigit() and p[1].isdigit():
            read += int(p[0]) * 512
            write += int(p[1]) * 512
    now = time.time()
    elapsed = max(0.001, now - _rlast_disk["time"])
    rs = (read - _rlast_disk["read"]) / elapsed
    ws = (write - _rlast_disk["write"]) / elapsed
    _rlast_disk = {"time": now, "read": read, "write": write}
    return {"timestamp": int(now * 1000), "read": max(0, rs), "write": max(0, ws)}


def _remote_info() -> dict:
    """远程：系统信息（单次连接已取回）。"""
    b = _remote_blocks()
    hostname = b.get("NAME", "").strip()
    uname_lines = b.get("UNAME", "").splitlines() or ["Linux", "", "", ""]
    def _u(i):
        return uname_lines[i].strip() if i < len(uname_lines) else ""
    # processor 从 /proc/cpuinfo 取 model name
    processor = ""
    for line in b.get("CPUINFO", "").splitlines():
        if ":" in line:
            processor = line.split(":", 1)[1].strip()
            break
    ncores = int(b.get("NPROC", "").strip() or "1")
    btime = 0
    for line in b.get("BTIME", "").splitlines():
        parts = line.split()
        if len(parts) >= 2 and parts[0] == "btime":
            try:
                btime = int(parts[1])
            except ValueError:
                btime = 0
            break
    now = time.time()
    uptime = int(now - btime) if btime else 0
    return {
        "hostname": hostname,
        "system": _u(0) or "Linux",
        "release": _u(1),
        "version": _u(2),
        "machine": _u(3),
        "processor": processor,
        "python_version": "",
        "cpu_count": ncores,
        "cpu_count_physical": ncores,
        "boot_time": datetime.fromtimestamp(btime).isoformat() if btime else datetime.now().isoformat(),
        "uptime_seconds": uptime,
    }

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
    # 磁盘 IO 统计不可用（平台不支持）时使用上一周期默认值
    pass


@router.get("/overview", dependencies=_PROTECTED)
async def overview():
    return await asyncio.to_thread(_overview_sync)


def _overview_sync():
    # 远端节点：读取远端主机指标（复用 _remote_overview）
    if node_manager.is_remote():
        return _remote_overview()
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
    if node_manager.is_remote():
        return _remote_network()
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
    if node_manager.is_remote():
        return _remote_diskio()
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


# ------------------------------------------------------------
# 安装完整性检测
# README 要求以「完整宿主机模式」运行容器（挂载 /:/host + HOST_ROOT、
# --privileged、--pid host、Docker socket、GRAW_HOST_DATA）。若用户未按
# README 安装（例如只裸跑 -p 8000:8000），容器会缺少宿主机权限，面板的
# 文件管理/Web 终端/Docker/防火墙等功能无法正常作用于宿主机。
# 这里在容器模式下逐项检测，缺失时前端弹窗提醒用户重新安装。
# ------------------------------------------------------------
def _is_running_in_container() -> bool:
    """判断是否运行在容器内（Docker / Podman）。

    依据：容器标记文件（/.dockerenv、/run/.containerenv）或 PID 1 的
    cgroup 路径中包含容器运行时关键字。
    """
    if os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv"):
        return True
    try:
        with open("/proc/1/cgroup", "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        return any(k in content for k in ("docker", "kubepods", "libpod", "podman"))
    except OSError:
        return False


def _pid1_comm() -> str:
    """读取 PID 1 的进程名。

    --pid host 生效时 PID 1 是宿主机 init（如 systemd）；未生效时 PID 1
    是容器自身的启动进程（如 python3/uvicorn），据此可判断是否共享了
    宿主机的进程命名空间。
    """
    try:
        with open("/proc/1/comm", "r", encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return ""


def _capeff() -> int:
    """读取 PID 1 的有效能力集（CapEff，十六进制）。

    用于判断容器是否 --privileged：非特权容器通常被裁剪掉 CAP_SYS_ADMIN，
    而 chroot /host 等操作依赖该能力。
    """
    try:
        with open("/proc/1/status", "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.startswith("CapEff:"):
                    return int(line.split()[1], 16)
    except OSError:
        # 读取失败时按无能力处理（视为非特权）
        pass
    return 0


def install_check_sync() -> dict:
    """检测当前运行环境是否满足 README 要求的「完整宿主机模式」安装。

    仅容器内运行时才深度检测；本机直跑（Windows/Linux 直接启动）天然拥有
    宿主机权限，视为完整。返回缺失项 key 列表，前端据此弹窗提醒。
    """
    if not _is_running_in_container():
        # 本机直跑：直接操作宿主机，视为完整，无需提醒
        return {"ok": True, "container": False, "mode": "host", "missing": []}

    missing = []
    # 1) HOST_ROOT 环境变量（告知后端宿主机根目录挂载点）
    host_root = os.environ.get("HOST_ROOT", "").strip()
    if not host_root:
        missing.append("host_root")
    elif not os.path.exists(os.path.join(host_root, "etc", "passwd")):
        # 2) 宿主机根目录挂载完整性：/host 下应存在宿主机必备文件
        #    （若只挂载了子目录，如 -v /home:/host，则检测不到）
        missing.append("host_mount")
    # 3) Docker socket：容器与镜像管理必需
    if not os.path.exists("/var/run/docker.sock"):
        missing.append("docker_sock")
    # 4) --pid host：PID 1 应为宿主机 init（进程管理/系统监控依赖）
    if _pid1_comm() != "systemd":
        missing.append("pid_host")
    # 5) --privileged：缺少 CAP_SYS_ADMIN 时 chroot /host、防火墙等无法生效
    if not (_capeff() & (1 << 21)):
        missing.append("privileged")
    # 6) GRAW_HOST_DATA：宿主机数据目录（应用商店/备份导出依赖）
    if not os.environ.get("GRAW_HOST_DATA", "").strip():
        missing.append("host_data")
    return {"ok": not missing, "container": True, "mode": "docker", "missing": missing}


@router.get("/install-check", dependencies=_PROTECTED)
async def install_check():
    """检测安装环境是否完整（是否缺少 README 要求的宿主机权限挂载）。"""
    return await asyncio.to_thread(install_check_sync)


def _info_sync():
    if node_manager.is_remote():
        return _remote_info()
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
# 单次采集的超时保护（秒）。本地 psutil 采样通常 <1s；远程走单条 SSH
# 脚本采集，含建连 + 脚本执行一般 2~5s。超时即按失败处理，防止远端节点
# 慢/卡时无限期拖住采集周期。
_METRICS_COLLECT_TIMEOUT = 15.0
# 连续采集失败时的退避（秒）：正常每 _METRICS_INTERVAL 拉一个周期；
# 远程节点不可达时按 5s→10s→20s→30s 分级拉长，避免对不可达主机反复
# 触发长时间 SSH 尝试（连接风暴 + 线程池占用）。
_METRICS_FAIL_SLEEP_BASE = 5.0
_METRICS_FAIL_SLEEP_MAX = 30.0
# 已连接的系统指标 WebSocket 客户端集合
_ws_clients: set = set()
# 最近一次采集的合并指标缓存（供新客户端连入时立即回放）
_metrics_cache: Optional[dict] = None
# 连续采集失败计数（退避与日志用）；成功后清零
_metrics_fails = 0


def _collect_sync() -> dict:
    """同步采集全部指标并合并为一个 payload（overview + network + diskio + info）。"""
    # 远端节点：每个采集周期强制刷新一次单连接批量采集，再供四项指标共用
    if node_manager.is_remote():
        _remote_blocks(force=True)
    return {
        "overview": _overview_sync(),
        "network": _network_sync(),
        "diskio": _diskio_sync(),
        "info": _info_sync(),
    }


async def _broadcast(payload: dict) -> None:
    """把一帧推送给所有已连接客户端，并顺带清理失效连接。"""
    dead = []
    for ws in list(_ws_clients):
        try:
            await ws.send_json(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _ws_clients.discard(ws)


async def _metrics_producer():
    """后台采集协程：周期性采集指标并广播给所有已连接的 WS 客户端。

    采用单生产者模式，即便同时存在多个 WS 客户端，也只做一次采集，
    避免每个客户端各自触发 psutil 采样（连接池优化）。
    同时把采样点交给 metrics_store 持久化，供历史监控回放使用。

    健壮性（本轮修复）：
      - 采集经 asyncio.to_thread 放入线程池，并用 wait_for 加超时保护，
        远端节点慢/卡时不会无限期拖住采集周期。
      - 采集失败时记录日志、保留上一次缓存，并按 5→30s 指数退避重试，
        避免对不可达的 SSH 节点反复触发长时间连接尝试。
      - 失败时向所有客户端广播 {"type":"unavailable"} 帧，前端据此展示
        「当前管理节点不可达」降级提示，而不是永久空白/假数据。
    """
    global _metrics_cache, _metrics_fails
    fail_sleep = _METRICS_FAIL_SLEEP_BASE
    while True:
        success = False
        try:
            # to_thread 保证 psutil/SSH 的阻塞采样不阻塞事件循环；
            # wait_for 兜底防卡死，超时后丢弃该次结果、按失败处理
            snapshot = await asyncio.wait_for(
                asyncio.to_thread(_collect_sync), timeout=_METRICS_COLLECT_TIMEOUT
            )
            _metrics_cache = snapshot
            _metrics_fails = 0
            success = True
        except asyncio.CancelledError:
            # 应用关闭：向上抛出，由 stop_metrics_producer 统一收尾
            raise
        except Exception as e:
            _metrics_fails += 1
            # 连续失败只留一条 warning，避免刷屏；恢复后下一帧即正常
            logger.warning(
                "系统指标采集失败（连续 %d 次）：%s", _metrics_fails, e
            )

        # 落盘历史采样（成功才记录；失败/None 由 metrics_store 自行忽略）
        try:
            metrics_store.record_sample(_metrics_cache if success else None)
        except Exception:
            # 历史采样落盘失败不影响实时采集主循环
            pass

        if success and _metrics_cache is not None:
            await _broadcast({"type": "metrics", "data": _metrics_cache})
        else:
            # 广播不可用状态：节点不可达 / 采集超时，前端立即降级提示
            await _broadcast({
                "type": "unavailable",
                "reason": f"当前管理节点数据采集失败（连续 {_metrics_fails} 次），请检查节点连通性或在「多节点管理」中切换主机",
            })

        # 连续失败时指数退避，降低对不可达节点的无效连接风暴
        sleep_for = _METRICS_INTERVAL if success else min(_METRICS_FAIL_SLEEP_MAX, fail_sleep)
        fail_sleep = min(_METRICS_FAIL_SLEEP_MAX, fail_sleep * 2)
        await asyncio.sleep(sleep_for)


_producer_task: Optional[asyncio.Task] = None
_flush_task: Optional[asyncio.Task] = None


async def _metrics_flusher():
    """后台落盘协程：定期把内存缓冲的历史采样写入磁盘。"""
    while True:
        await asyncio.sleep(metrics_store.SAMPLE_INTERVAL)
        try:
            await asyncio.to_thread(metrics_store.flush)
        except Exception:
            # 落盘异常不影响实时采集主循环
            pass


async def start_metrics_producer():
    """在应用启动时启动后台采集协程（非阻塞，立即返回）。重复调用安全。

    注意：这里**不再**同步 await 一次预热采集。此前 warmup 在 lifespan
    里阻塞执行 `to_thread(_collect_sync)`，而采集会跟随「当前选中节点」：
    若启动时选中的是不可达的 SSH 节点，SSH 连接超时（可达数十秒）会把整个
    应用启动卡住，表现为所有请求（含「多节点管理」）一律超时。改为纯异步
    建任务后，首次采集由 producer 协程自行完成（同样受超时与退避保护）。
    """
    global _producer_task, _flush_task, _metrics_fails
    if _producer_task is None or _producer_task.done():
        _metrics_fails = 0
        _producer_task = asyncio.create_task(_metrics_producer())
    if _flush_task is None or _flush_task.done():
        _flush_task = asyncio.create_task(_metrics_flusher())


async def stop_metrics_producer():
    """停止后台采集/落盘协程（关闭连接），并清空剩余缓冲。"""
    global _producer_task, _flush_task, _metrics_fails
    for task in (_producer_task, _flush_task):
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                # 取消协程时预期抛出 CancelledError，忽略即可
                pass
    _producer_task = None
    _flush_task = None
    _metrics_fails = 0
    # 进程退出前把残留采样落盘，避免丢失最近一个周期
    try:
        await asyncio.to_thread(metrics_store.flush)
    except Exception:
        # 退出前落盘失败不影响关闭流程
        pass


# --------------------------------------------------------------------------
# 子节点指标 WS 桥接（系统概览 / 实时监控走子节点原生 WebSocket）
# --------------------------------------------------------------------------
# 子节点本身运行完整的 Graw（含统一的指标生产者 /api/system/ws）。
# 此前主面板对子节点的指标来自「SSH 批量脚本采集」（_REMOTE_SCRIPT），
# 数据路径重且函数覆盖有限。此处当当前节点为「已配置 Agent 的 SSH 子节点」时，
# 把浏览器 WS 经 SSH 隧道桥接到子节点自身的 /api/system/ws：
#   浏览器 <-> 主面板 system_ws <-> (SSH direct-tcpip 隧道) <-> 子节点 /api/system/ws
# 让概览/实时监控直接使用子节点原生 psutil 指标，与子节点侧 WS 完全一致。
# 仅做帧级双向转发，不解析业务内容；桥接失败自动回退到本地生产者采集。
# --------------------------------------------------------------------------
def _ws_server_frame_parse(buf: bytes):
    """解析（服务端→客户端）WebSocket 帧；缓冲不足返回 (None, 0)。

    返回 ((fin, opcode, payload), consumed)。按 RFC 6455，服务端帧不带掩码，
    但这里对带掩码的服务端帧也做容错解析（防御非标实现）。
    """
    if len(buf) < 2:
        return None, 0
    b0, b1 = buf[0], buf[1]
    opcode = b0 & 0x0F
    fin = bool(b0 & 0x80)
    length = b1 & 0x7F
    masked = bool(b1 & 0x80)
    off = 2
    if length == 126:
        if len(buf) < 4:
            return None, 0
        length = int.from_bytes(buf[2:4], "big")
        off = 4
    elif length == 127:
        if len(buf) < 10:
            return None, 0
        length = int.from_bytes(buf[2:10], "big")
        off = 10
    mask = None
    if masked:
        if len(buf) < off + 4:
            return None, 0
        mask = buf[off:off + 4]
        off += 4
    if len(buf) < off + length:
        return None, 0
    payload = buf[off:off + length]
    if mask:
        payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return (fin, opcode, payload), off + length


def _ws_client_frame(opcode: int, payload: bytes) -> bytes:
    """构造（客户端→服务端，带掩码）WebSocket 帧。"""
    head = bytearray([0x80 | opcode])
    n = len(payload)
    if n < 126:
        head.append(0x80 | n)
    elif n < 65536:
        head.append(0x80 | 126)
        head += n.to_bytes(2, "big")
    else:
        head.append(0x80 | 127)
        head += n.to_bytes(8, "big")
    mask = os.urandom(4)
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    return bytes(head) + mask + masked


def _tunnel_ws_handshake(client, dst_port: int, path: str, jwt: str) -> tuple:
    """在 SSH 隧道通道上完成 WebSocket 握手，返回 (channel, 已读出的首帧字节)。

    direct-tcpip 通道是原始字节流，可承载 WebSocket 协议：手写 HTTP/1.1
    Upgrade 握手 + 后续帧级转发。jwt 为子节点 agent JWT（主面板换取的管理员令牌）。
    """
    chan = agent_client._open_tunnel_channel(client, dst_port)
    sec_key = base64.b64encode(os.urandom(16)).decode("ascii")
    req = (
        f"GET {path} HTTP/1.1\r\n"
        "Host: localhost\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {sec_key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        # 浏览器 WS 无法带 Bearer 头，但这里是我们自己发起的连接，可带
        f"Authorization: Bearer {jwt}\r\n"
        "\r\n"
    )
    try:
        chan.sendall(req.encode("utf-8"))
        resp = b""
        # 握手限时保护：子节点异常（无响应/握手不清零）时不能无限等待
        deadline = time.time() + 15
        while b"\r\n\r\n" not in resp:
            if time.time() > deadline:
                raise ConnectionError("子节点 WebSocket 握手超时")
            chunk = chan.recv(65536)
            if not chunk:
                break
            resp += chunk
    except Exception:
        try:
            chan.close()
        except Exception:
            # 关闭通道失败（已断开）时忽略
            pass
        raise
    head, sep, extra = resp.partition(b"\r\n\r\n")
    status = head.split(b"\r\n", 1)[0].decode("utf-8", "replace") if head else ""
    if not sep or "101" not in status:
        try:
            chan.close()
        except Exception:
            # 握手失败时关闭通道失败（已断开）忽略
            pass
        raise ConnectionError(f"子节点 WebSocket 握手失败: {status or '无响应'}")
    return chan, extra


def _agent_ws_connect(node: dict) -> tuple:
    """同步建立到子节点 Agent 的 WS 隧道连接（放线程池执行）。

    - 取回/复用 SSH 连接池 client；
    - 用成对密钥向子节点 /api/agent/issue 换取 agent JWT（带缓存）；
    - 在隧道上完成 WebSocket 握手，返回 (channel, 已读出的首帧字节)。
    全部为阻塞 I/O，调用方必须用 asyncio.to_thread 包裹。
    """
    timeout = 20
    client = node_manager._paramiko_pool_client(node, timeout)
    jwt = agent_client._ensure_token(node, client)
    cfg = agent_client._node_agent_cfg(node)
    # 子节点 WS 端点走 ?token= 鉴权（get_current_user_ws），agent JWT 同样有效
    path = "/api/system/ws?token=" + urllib.parse.quote(jwt, safe="")
    return _tunnel_ws_handshake(client, cfg["port"], path, jwt)


async def _bridge_agent_ws(node: dict, browser: WebSocket) -> None:
    """把浏览器 WS 桥接到子节点 Agent 的 /api/system/ws（走 SSH 隧道）。

    双向转发：
      - 子节点→浏览器：解析隧道上的服务端 WS 帧，文本帧 send_text / 二进制帧
        send_bytes 转发；子节点 ping 自动回 pong；收到 close 则关闭浏览器侧。
      - 浏览器→子节点：浏览器发来的文本/二进制帧按客户端帧规则转发到隧道
        （前端心跳 ping 会被子节点 receive_text 正常消费）。
    任一侧断开（或异常）都会结束桥接并关闭两侧连接。
    """
    # 连接/换 token/握手都是阻塞 I/O，放线程池避免卡住事件循环
    chan, buf = await asyncio.to_thread(_agent_ws_connect, node)

    closed = asyncio.Event()

    async def _node_to_browser():
        nonlocal buf
        try:
            while not closed.is_set():
                frame = _ws_server_frame_parse(buf)
                if frame[0] is not None:
                    _, opcode, payload = frame[0]
                    buf = buf[frame[1]:]
                    if opcode == 1:  # 文本帧（指标 JSON）
                        await browser.send_text(payload.decode("utf-8", "replace"))
                    elif opcode == 2:  # 二进制帧
                        await browser.send_bytes(payload)
                    elif opcode == 8:  # 子节点主动关闭
                        try:
                            await browser.close()
                        except Exception:
                            # 浏览器侧已断开关闭失败时忽略
                            pass
                        return
                    elif opcode == 9:  # 子节点 ping -> 回 pong
                        try:
                            chan.sendall(_ws_client_frame(10, payload))
                        except Exception:
                            # pong 发送失败（通道断开）时忽略
                            pass
                    continue
                # 缓冲不足：读更多字节（paramiko recv 阻塞，放线程池）
                data = await asyncio.to_thread(chan.recv, 65536)
                if not data:
                    break
                buf += data
        except Exception:
            # 正常断连（浏览器关闭/子节点关通道）也会走到这里，仅记 debug
            logger.debug("子节点指标 WS 桥接读循环结束（连接关闭）", exc_info=True)
        finally:
            closed.set()

    async def _browser_to_node():
        try:
            while not closed.is_set():
                msg = await browser.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                text = msg.get("text")
                if isinstance(text, str):
                    chan.sendall(_ws_client_frame(1, text.encode("utf-8")))
                else:
                    raw = msg.get("bytes")
                    if raw:
                        chan.sendall(_ws_client_frame(2, raw))
        except Exception:
            pass  # 浏览器关闭/异常：放弃转发，交由主循环收尾
        finally:
            closed.set()

    t1 = asyncio.create_task(_node_to_browser())
    t2 = asyncio.create_task(_browser_to_node())
    try:
        await asyncio.wait({t1, t2}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        closed.set()
        for t in (t1, t2):
            if not t.done():
                t.cancel()
        for t in (t1, t2):
            try:
                await t
            except (asyncio.CancelledError, Exception):
                # 桥接任务被取消或异常时忽略，连接已收尾
                pass
        try:
            chan.close()
        except Exception:
            # 关闭通道失败（已断开）时忽略
            pass
        try:
            await browser.close()
        except Exception:
            # 浏览器侧已断开关闭失败时忽略
            pass


@router.websocket("/ws")
async def system_ws(
    websocket: WebSocket,
    user: Optional[dict] = Depends(get_current_user_ws_checked),
    node: str = Query(default=""),
):
    """统一系统指标 WebSocket。

    通过 ?token= 鉴权（get_current_user_ws_checked：登录 + 非默认密码，
    与 /api/system/* HTTP 只读接口的 _PROTECTED 语义对齐）；鉴权失败时
    依赖内部已关闭连接，此处直接返回。鉴权通过后：
      - 目标节点是「已配置 Agent 的 SSH 子节点」（?node= 显式指定或全局当前）：
        桥接到子节点自身的 /api/system/ws，前端直接使用子节点原生指标。
      - 其余（本地 / 未配置 agent 的远程节点）：订阅主面板后台生产者的广播，
        并先回放一次缓存数据。桥接失败同样回退到本路径（SSH 采集失败时
        生产者会广播不可用帧，前端正常降级提示）。

    安全修复（第十三轮审计，Medium）：
      1) 鉴权升级为 get_current_user_ws_checked——默认密码账号无法再经
         WebSocket 绕过改密拦截订阅监控流（此前可读本机与任意子节点实时
         指标，与 HTTP 只读接口被 403 拦截的语义不一致）。
      2) ?node= 仅管理员可用——此前任意登录用户可指定任意已配置 SSH
         子节点 ID 桥接其监控数据，普通用户的数据可见范围被放大到全部
         节点；现非管理员忽略 ?node=（回落到全局当前节点，与 HTTP
         /api/system/overview 的可视范围一致）。
    """
    if user is None:
        # get_current_user_ws_checked 内部已在鉴权失败时 close(4401/4403)
        return
    await websocket.accept()

    # 解析目标节点（?node= 显式指定优先，否则全局当前主机）
    # 非管理员不得指定任意节点：监控数据可见范围与 HTTP 接口保持一致
    if node and user.get("role") != "admin":
        node = ""
    target = node_manager.get_node(node) if node else None
    if target is None:
        target = node_manager.get_current_node()

    if (
        target is not None
        and target.get("type") == "ssh"
        and agent_client.agent_ready(target)
    ):
        try:
            await _bridge_agent_ws(target, websocket)
            return
        except Exception as e:  # noqa: BLE001 - 桥接失败需回退，不中断连接
            logger.warning("桥接子节点指标 WS 失败，回退本地代理采集: %s", e)
            # 桌面卡片是全局语义：仅当目标即全局当前节点时才能交给本地生产者回退，
            # 否则（跨节点误连）关闭连接，由前端带正确 node 重连。
            if node and target.get("id") != node_manager.current_node_id():
                try:
                    await websocket.close(code=1011)
                except Exception:
                    # 关闭失败（连接已断开）时忽略
                    pass
                return

    # —— 本地路径：加入主面板后台生产者广播 ——
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
                # 关闭失败（连接已断开）时忽略
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
            # 关闭失败（连接已断开）时忽略
            pass


# ---------------------------------------------------------------------------
# 历史监控回放：查询持久化的指标采样（登录即可查看）
# ---------------------------------------------------------------------------
@router.get("/metrics/status", dependencies=_PROTECTED)
async def metrics_status():
    """返回历史数据概况（保留天数、可用文件、最早/最新采样时间）。"""
    return metrics_store.status()


@router.get("/metrics/history", dependencies=_PROTECTED)
async def metrics_history(
    start: float = Query(default=0, description="起始 Unix 时间戳（秒）"),
    end: float = Query(default=0, description="结束 Unix 时间戳（秒）"),
    bucket: int = Query(default=0, ge=0, le=86400, description="聚合桶大小（秒），0 表示原始采样"),
):
    """查询指定时间范围的指标历史。

    不传 start/end 时默认取最近 1 小时；end 缺省取当前时间。
    """
    now = time.time()
    if not start:
        start = now - 3600
    if not end:
        end = now
    # 数值校验：防止异常大/负值造成恶意查询开销
    if start < 0 or end < 0 or end - start > 365 * 86400:
        raise HTTPException(status_code=400, detail="时间范围非法")
    return metrics_store.history(start, end, bucket or None)


@router.delete("/metrics/clear", dependencies=[Depends(require_admin)])
async def metrics_clear():
    """清空全部历史监控采样（管理员操作）。"""
    metrics_store.clear()
    return {"ok": True}
