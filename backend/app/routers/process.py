from fastapi import APIRouter, HTTPException, Request, Depends
from pydantic import BaseModel
import asyncio
import psutil

from app import node_manager
from app.auth import get_current_user, get_client_ip
from app import auditlog

router = APIRouter()


class KillRequest(BaseModel):
    force: bool = False


def _remote_ps_lines() -> list:
    """远程：通过 `ps` 命令采样进程列表，返回原始行。"""
    cmd = (
        "ps -eo pid=,user=,stat=,pcpu=,rss=,etimes=,comm= --no-headers 2>/dev/null "
        "|| ps -eo pid=,user=,stat=,pcpu=,rss=,etimes=,comm="
    )
    r = node_manager.host_shell(cmd, capture_output=True, text=True, timeout=20)
    return r.stdout.splitlines()


def _remote_list_processes(sort_by: str, limit: int):
    """远程进程列表：解析 `ps` 输出。"""
    import time

    procs = []
    now = time.time()
    for line in _remote_ps_lines():
        parts = line.split(None, 6)
        if len(parts) < 7:
            continue
        try:
            pid, user, stat, cpu, rss_kb, etimes, comm = (
                parts[0],
                parts[1],
                parts[2],
                parts[3],
                parts[4],
                parts[5],
                parts[6],
            )
            procs.append(
                {
                    "pid": int(pid),
                    "name": comm or "",
                    "username": user or "",
                    "status": stat or "",
                    "cpu": round(float(cpu or 0), 1),
                    "memory": int(float(rss_kb or 0) * 1024),
                    "create_time": int(now - float(etimes or 0)),
                }
            )
        except (ValueError, IndexError):
            continue
    key = sort_by if sort_by in ("cpu", "memory", "pid", "name") else "cpu"
    procs.sort(key=lambda x: x.get(key) or 0, reverse=key in ("cpu", "memory"))
    return procs[:limit]


@router.get("/list")
async def list_processes(sort_by: str = "cpu", limit: int = 200):
    if node_manager.is_remote():
        return await asyncio.to_thread(_remote_list_processes, sort_by, limit)
    result = await asyncio.to_thread(_list_processes_sync, sort_by, limit)
    return result


def _list_processes_sync(sort_by: str, limit: int):
    import time

    procs = []
    for p in psutil.process_iter(["pid", "name", "username", "status"]):
        try:
            p.cpu_percent(None)
        except Exception:
            pass
    time.sleep(0.1)
    for p in psutil.process_iter(["pid", "name", "username", "status", "memory_info", "create_time"]):
        try:
            info = p.info
            cpu = p.cpu_percent(None)
            mem = info.get("memory_info")
            procs.append({
                "pid": info.get("pid"),
                "name": info.get("name") or "",
                "username": info.get("username") or "",
                "status": info.get("status") or "",
                "cpu": round(cpu, 1),
                "memory": int(mem.rss) if mem else 0,
                "create_time": info.get("create_time") or 0,
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    key_map = {"cpu": "cpu", "memory": "memory", "pid": "pid", "name": "name"}
    key = key_map.get(sort_by, "cpu")

    def _sort_key(x):
        v = x.get(key)
        if isinstance(v, bool):
            return 0
        if isinstance(v, (int, float)):
            return v
        return str(v).lower() if v is not None else ""

    procs.sort(key=_sort_key, reverse=(key in ("cpu", "memory")))
    return procs[:limit]


@router.post("/{pid}/kill")
async def kill_process(
    pid: int,
    req: KillRequest,
    request: Request,
    user: dict = Depends(get_current_user),
):
    try:
        p = psutil.Process(pid)
        pname = p.name()
    except Exception:
        pname = ""
    if node_manager.is_remote():
        # 远程：signum 需整数；9 = SIGKILL，15 = SIGTERM
        signum = 9 if req.force else 15
        r = node_manager.host_shell(
            f"kill -{signum} {int(pid)}", capture_output=True, text=True, timeout=10
        )
        if r.returncode != 0 and "no such process" in (r.stderr or "").lower():
            raise HTTPException(status_code=404, detail="Process not found")
        if r.returncode != 0:
            raise HTTPException(status_code=500, detail=(r.stderr or "kill failed").strip())
        auditlog.record(
            "结束进程", user["username"], get_client_ip(request), f"pid:{pid}"
        )
        return {"ok": True}
    try:
        p = psutil.Process(pid)
        if req.force:
            p.kill()
        else:
            p.terminate()
        auditlog.record(
            "结束进程",
            user["username"],
            get_client_ip(request),
            f"pid:{pid} name:{pname} 强制:{req.force}",
        )
        return {"ok": True}
    except psutil.NoSuchProcess:
        raise HTTPException(status_code=404, detail="Process not found")
    except psutil.AccessDenied:
        raise HTTPException(status_code=403, detail="Access denied")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/{pid}")
async def process_detail(pid: int):
    if node_manager.is_remote():
        return await asyncio.to_thread(_remote_process_detail, pid)
    try:
        return await asyncio.to_thread(_process_detail_sync, pid)
    except psutil.NoSuchProcess:
        raise HTTPException(status_code=404, detail="Process not found")
    except psutil.AccessDenied:
        raise HTTPException(status_code=403, detail="Access denied")


def _remote_process_detail(pid: int):
    """远程单进程详情：`ps -p <pid> -o ...`。"""
    import time

    cmd = (
        f"ps -p {int(pid)} -o pid=,user=,stat=,pcpu=,rss=,etimes=,comm= "
        "[ ,args= ] 2>/dev/null"
    )
    # 兼容无逗号写法，使用标准字段
    cmd = f"ps -p {int(pid)} -o pid=,user=,stat=,pcpu=,rss=,etimes=,comm=,args= 2>/dev/null || echo MISSING"
    r = node_manager.host_shell(cmd, capture_output=True, text=True, timeout=10)
    out = r.stdout.strip()
    if not out or "MISSING" in out:
        raise HTTPException(status_code=404, detail="Process not found")
    parts = out.split(None, 7)
    if len(parts) < 7:
        raise HTTPException(status_code=404, detail="Process not found")
    try:
        pid_v, user, stat, cpu, rss_kb, etimes, comm = parts[:7]
        args = parts[7] if len(parts) > 7 else (comm or "")
        return {
            "pid": int(pid_v),
            "name": comm or "",
            "exe": "",
            "cmdline": ([comm] + args.split(" ")[1:]) if args else [],
            "username": user or "",
            "status": stat or "",
            "cpu": round(float(cpu or 0), 1),
            "memory": int(float(rss_kb or 0) * 1024),
            "num_threads": 0,
            "create_time": int(time.time() - float(etimes or 0)),
        }
    except (ValueError, IndexError) as e:
        raise HTTPException(status_code=500, detail=str(e))


def _process_detail_sync(pid: int):
    p = psutil.Process(pid)
    with p.oneshot():
        return {
            "pid": p.pid,
            "name": p.name(),
            "exe": p.exe() if hasattr(p, "exe") else "",
            "cmdline": p.cmdline(),
            "username": p.username(),
            "status": p.status(),
            "cpu": p.cpu_percent(0.1),
            "memory": p.memory_info().rss,
            "num_threads": p.num_threads(),
            "create_time": p.create_time(),
        }