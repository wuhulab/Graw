import os
import json
import shutil
import subprocess
import time
import asyncio
import threading
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List

router = APIRouter()

IS_WINDOWS = os.name == "nt"

try:
    import docker
    DOCKER_SDK = True
except Exception:
    DOCKER_SDK = False

_client = None
_last_reason = None
# Cache the *successful* CLI discovery permanently. Failed probes are NOT
# cached permanently: a transient WSL/podman startup delay would otherwise
# disable Docker for the entire server lifetime. Instead we re-probe after
# a short backoff so the panel recovers once the engine comes up.
_podman_cmd = None
_podman_fail_until = 0.0
_podman_fail_backoff = 5.0  # seconds between failed probes
_docker_fail_until = 0.0
_WSL_PROBE_TIMEOUT = 30  # WSL cold-start can take 20+ seconds
_DOCKER_SDK_TIMEOUT = 10  # per-request timeout for docker SDK client
_probe_lock = threading.Lock()  # serialise CLI / SDK discovery probes


def _run(cmd, timeout=30):
    """Run a subprocess and decode stdout/stderr as UTF-8.

    Using ``text=True`` would decode with the locale encoding (cp936/GBK on
    Chinese Windows), which raises ``UnicodeDecodeError`` on multibyte UTF-8
    output (e.g. container names or logs containing non-ASCII). Reading raw
    bytes and decoding as UTF-8 with ``errors=replace`` avoids the threaded
    reader crash entirely.
    """
    p = subprocess.run(cmd, capture_output=True, timeout=timeout)
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


def _clean_reason(e):
    msg = str(e)
    keywords = [
        "CreateFile", "FileNotFoundError", "ConnectionRefused",
        "WinError 10061", "Failed to establish", "No connection",
        "Errno 2", "ConnectError", "TimeoutError",
    ]
    if any(k in msg for k in keywords):
        return "未检测到运行中的 Docker/Podman 服务"
    return msg


def _find_podman():
    """Locate a usable container CLI (podman or docker).

    On Windows, prefer WSL podman. On Linux, prefer local podman, then docker.
    Returns a command list (prefix) whose first element identifies the engine.
    Success is cached permanently; failure is cached for only a few seconds so
    the panel can recover once the engine finishes starting up.
    """
    global _podman_cmd, _podman_fail_until
    if _podman_cmd is not None:
        return _podman_cmd
    now = time.time()
    if now < _podman_fail_until:
        return None
    # Serialise probes so concurrent requests don't all spawn WSL at once.
    with _probe_lock:
        if _podman_cmd is not None:
            return _podman_cmd
        now = time.time()
        if now < _podman_fail_until:
            return None
        found = None
        try:
            if IS_WINDOWS:
                if shutil.which("wsl"):
                    rc, out, _ = _run(["wsl", "-u", "root", "--", "podman", "--version"], timeout=_WSL_PROBE_TIMEOUT)
                    if rc == 0 and "podman" in out.lower():
                        found = ["wsl", "-u", "root", "--", "podman"]
            else:
                for cli in ("podman", "docker"):
                    path = shutil.which(cli)
                    if path:
                        rc, _out, _err = _run([path, "--version"], timeout=10)
                        if rc == 0:
                            found = [path]
                            break
        except Exception:
            found = None
        if found is not None:
            _podman_cmd = found
            return found
        _podman_fail_until = time.time() + _podman_fail_backoff
        return None


def _cli_engine() -> str:
    """Return 'podman' or 'docker' for the active CLI (for version display)."""
    cmd = _find_podman()
    if cmd is None:
        return ""
    if IS_WINDOWS:
        return "podman"
    name = os.path.basename(cmd[0])
    return "docker" if name.startswith("docker") else "podman"


def _podman_json(args: List[str]) -> list:
    cmd = _find_podman()
    if cmd is None:
        raise RuntimeError("podman 不可用")
    full = cmd + args
    rc, out, err = _run(full)
    if rc != 0:
        raise RuntimeError(err.strip() or "podman 命令失败")
    out = out.strip()
    if not out:
        return []
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return []


def _podman_version() -> str:
    cmd = _find_podman()
    if cmd is None:
        return ""
    _rc, out, _err = _run(cmd + ["--version"], timeout=10)
    return out.strip()


def _try_docker_sdk():
    """Try docker.from_env() plus common podman endpoints.

    Caches a connected client permanently; failed probes back off briefly so
    Docker Desktop / podman can be detected once it finishes booting.
    """
    global _client, _last_reason, _docker_fail_until
    if _client is not None:
        try:
            _client.ping()
            return _client
        except Exception:
            try:
                _client.close()
            except Exception:
                pass
            _client = None
    if not DOCKER_SDK:
        return None
    now = time.time()
    if now < _docker_fail_until:
        return None
    with _probe_lock:
        # Re-check inside the lock in case another thread just connected.
        if _client is not None:
            try:
                _client.ping()
                return _client
            except Exception:
                try:
                    _client.close()
                except Exception:
                    pass
                _client = None
        now = time.time()
        if now < _docker_fail_until:
            return None
        candidates = [None]
        if IS_WINDOWS:
            candidates += ["npipe:////./pipe/docker_engine"]
        else:
            candidates += ["unix:///var/run/docker.sock", "unix:///run/podman/podman.sock"]
        for host in candidates:
            try:
                kwargs = {"base_url": host} if host else {}
                kwargs["timeout"] = _DOCKER_SDK_TIMEOUT
                c = docker.DockerClient(**kwargs)
                c.ping()
                _client = c
                _last_reason = None
                return c
            except Exception:
                try:
                    c.close()
                except Exception:
                    pass
        _docker_fail_until = time.time() + _podman_fail_backoff
        return None


def get_backend():
    """Return ('cli', None) | ('docker', client). Raise HTTPException if none."""
    global _last_reason
    if _find_podman() is not None:
        return "cli", None
    c = _try_docker_sdk()
    if c is not None:
        return "docker", c
    _last_reason = _last_reason or "未检测到运行中的 Docker/Podman 服务"
    raise HTTPException(status_code=503, detail=_last_reason)


class ActionRequest(BaseModel):
    action: str


@router.get("/status")
async def status():
    return await asyncio.to_thread(_status_sync)


def _status_sync():
    global _client
    try:
        kind, client = get_backend()
    except HTTPException:
        _client = None
        return {"available": False, "reason": _last_reason or "Docker/Podman 不可用"}
    if kind == "cli":
        try:
            ps = _podman_json(["ps", "-a", "--format", "json"])
            imgs = _podman_json(["images", "--format", "json"])
            running = sum(1 for c in ps if c.get("Status", "").startswith("Up"))
            ver = _podman_version()
            engine = _cli_engine()
            os_label = f"linux ({engine} via WSL)" if IS_WINDOWS else f"linux ({engine})"
            return {
                "available": True,
                "containers": len(ps),
                "containers_running": running,
                "images": len(imgs),
                "server_version": ver,
                "os": os_label,
            }
        except Exception as e:
            return {"available": False, "reason": _clean_reason(e)}
    try:
        info = client.info()
        return {
            "available": True,
            "containers": info.get("Containers", 0),
            "containers_running": info.get("ContainersRunning", 0),
            "images": info.get("Images", 0),
            "server_version": info.get("ServerVersion", ""),
            "os": info.get("OperatingSystem", ""),
        }
    except Exception as e:
        _client = None
        return {"available": False, "reason": _clean_reason(e)}


def _parse_ports(ports_field):
    """Normalize podman port field (string or list) into ['host:port->container']."""
    result = []
    if not ports_field:
        return result
    if isinstance(ports_field, list):
        for p in ports_field:
            if isinstance(p, dict):
                host = p.get("host_port", "")
                container = p.get("container_port", "")
                result.append(f"{host}->{container}")
            else:
                result.append(str(p))
    elif isinstance(ports_field, str):
        for tok in ports_field.split(","):
            tok = tok.strip()
            if tok:
                result.append(tok)
    return result


@router.get("/containers")
async def containers(all: bool = True):
    return await asyncio.to_thread(_containers_sync, all)


def _parse_percent(s) -> float:
    """把 '0.10%' 之类的字符串解析为浮点数百分比，失败返回 0.0。"""
    try:
        return round(float(str(s).replace("%", "").strip()), 2)
    except Exception:
        return 0.0


def _parse_stats_cli(items: list) -> dict:
    """解析 podman/docker CLI 的 stats JSON 输出为 {name: stats}。

    podman `stats --format json` 输出一个数组，docker 输出逐行 JSON，
    统一在此处归一化为 key 为容器名称的字典。
    """
    result = {}
    for it in items:
        name = it.get("name") or it.get("container") or it.get("id") or ""
        if not name:
            continue
        # podman 字段：cpu_percent / mem_usage / mem_percent
        # docker 字段：CPUPerc / MemUsage / MemPerc
        cpu_percent = it.get("cpu_percent") or it.get("CPUPerc") or ""
        mem_percent = it.get("mem_percent") or it.get("MemPerc") or ""
        mem_usage = it.get("mem_usage") or it.get("MemUsage") or ""
        result[name] = {
            "cpu_percent": _parse_percent(cpu_percent),
            "mem_percent": _parse_percent(mem_percent),
            "mem_usage": str(mem_usage or ""),
        }
    return result


def _stats_sync() -> dict:
    """批量采集运行中容器的 CPU / 内存占用。

    返回 {name: {cpu_percent, mem_percent, mem_usage}}。
    CLI 模式：`podman/docker stats --no-stream --format json`。
    SDK 模式：逐个容器 c.stats(stream=False) 计算。
    任何失败都返回空字典，不影响容器列表主流程。
    """
    try:
        kind, client = get_backend()
    except HTTPException:
        return {}
    result = {}
    if kind == "cli":
        cli = _find_podman()
        if cli is None:
            return {}
        try:
            rc, out, err = _run(cli + ["stats", "--no-stream", "--format", "json"], timeout=30)
            if rc != 0:
                return {}
            out = out.strip()
            if not out:
                return {}
            items = []
            if out.startswith("["):
                # podman: 单个 JSON 数组
                try:
                    items = json.loads(out)
                except json.JSONDecodeError:
                    return {}
            else:
                # docker: 每行一个 JSON 对象
                for line in out.splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        items.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
            result = _parse_stats_cli(items)
        except Exception:
            return {}
    else:
        # docker SDK 模式：用容器 stats 计算 CPU / 内存
        try:
            for c in client.containers.list():
                try:
                    s = c.stats(stream=False)
                except Exception:
                    continue
                mem = s.get("memory_stats", {}) or {}
                usage = mem.get("usage") or 0
                limit = mem.get("limit") or 0
                mem_pct = round(usage / limit * 100, 2) if limit else 0.0
                # CPU：两次采样差值比
                cpu = s.get("cpu_stats", {}) or {}
                precpu = s.get("precpu_stats", {}) or {}
                cpu_delta = (cpu.get("cpu_usage", {}) or {}).get("total_usage", 0) - \
                            (precpu.get("cpu_usage", {}) or {}).get("total_usage", 0)
                sys_delta = cpu.get("system_cpu_usage", 0) - precpu.get("system_cpu_usage", 0)
                online = cpu.get("online_cpus", 1) or 1
                cpu_pct = round(cpu_delta / sys_delta * online * 100, 2) if sys_delta > 0 else 0.0
                result[c.name] = {
                    "cpu_percent": cpu_pct,
                    "mem_percent": mem_pct,
                    "mem_usage": f"{usage / 1024 / 1024:.1f}MB / {limit / 1024 / 1024:.1f}MB",
                }
        except Exception:
            return {}
    return result


def _containers_sync(all: bool = True):
    try:
        kind, client = get_backend()
    except HTTPException:
        raise
    if kind == "cli":
        try:
            arr = _podman_json(["ps", "-a", "--format", "json"])
            stats = _stats_sync() if any(c.get("Status", "").startswith("Up") for c in arr) else {}
            result = []
            for c in arr:
                state = "running" if c.get("Status", "").startswith("Up") else c.get("Status", "exited")
                name = (c.get("Names") or [""])[0] if c.get("Names") else ""
                st = stats.get(name, {})
                result.append({
                    "id": c.get("Id", "")[:12],
                    "name": name,
                    "image": c.get("Image", ""),
                    "status": c.get("Status", ""),
                    "state": state,
                    "created": c.get("Created", ""),
                    "ports": _parse_ports(c.get("Ports")),
                    "cpu_percent": st.get("cpu_percent", 0),
                    "mem_percent": st.get("mem_percent", 0),
                    "mem_usage": st.get("mem_usage", ""),
                })
            return result
        except Exception as e:
            raise HTTPException(status_code=500, detail=_clean_reason(e))
    try:
        stats = _stats_sync() if any(
            (c.attrs.get("State", {}).get("Status", "") == "running") for c in client.containers.list(all=all)
        ) else {}
        result = []
        for c in client.containers.list(all=all):
            attrs = c.attrs
            ports = []
            try:
                port_map = attrs.get("NetworkSettings", {}).get("Ports", {}) or {}
                for k, v in port_map.items():
                    if v:
                        for p in v:
                            ports.append(f"{p.get('HostIp', '')}:{p.get('HostPort','')}->{k}")
                    else:
                        ports.append(k)
            except Exception:
                pass
            st = stats.get(c.name, {})
            result.append({
                "id": c.short_id,
                "name": c.name,
                "image": c.image.tags[0] if c.image.tags else c.image.short_id,
                "status": c.status,
                "state": attrs.get("State", {}).get("Status", c.status),
                "created": attrs.get("Created", ""),
                "ports": ports,
                "cpu_percent": st.get("cpu_percent", 0),
                "mem_percent": st.get("mem_percent", 0),
                "mem_usage": st.get("mem_usage", ""),
            })
        return result
    except Exception as e:
        global _client
        _client = None
        raise HTTPException(status_code=500, detail=_clean_reason(e))


@router.post("/containers/{container_id}/action")
async def container_action(container_id: str, req: ActionRequest):
    return await asyncio.to_thread(_container_action_sync, container_id, req)


def _container_action_sync(container_id: str, req: ActionRequest):
    try:
        kind, client = get_backend()
    except HTTPException:
        raise
    if kind == "cli":
        mapping = {"start": "start", "stop": "stop", "restart": "restart", "remove": "rm -f"}
        sub = mapping.get(req.action)
        if not sub:
            raise HTTPException(status_code=400, detail=f"Unknown action: {req.action}")
        cmd = _find_podman() + sub.split() + [container_id]
        rc, _out, err = _run(cmd)
        if rc != 0:
            raise HTTPException(status_code=500, detail=err.strip() or "操作失败")
        return {"ok": True}
    try:
        c = client.containers.get(container_id)
        if req.action == "start":
            c.start()
        elif req.action == "stop":
            c.stop()
        elif req.action == "restart":
            c.restart()
        elif req.action == "remove":
            c.remove(force=True)
        else:
            raise HTTPException(status_code=400, detail=f"Unknown action: {req.action}")
        return {"ok": True}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=_clean_reason(e))


@router.get("/containers/{container_id}/logs")
async def container_logs(container_id: str, tail: int = 200):
    return await asyncio.to_thread(_container_logs_sync, container_id, tail)


def _container_logs_sync(container_id: str, tail: int = 200):
    try:
        kind, client = get_backend()
    except HTTPException:
        raise
    if kind == "cli":
        cmd = _find_podman() + ["logs", "--tail", str(tail), container_id]
        rc, out, err = _run(cmd)
        if rc != 0:
            raise HTTPException(status_code=500, detail=err.strip() or "获取日志失败")
        return {"logs": out or "(空)"}
    try:
        c = client.containers.get(container_id)
        logs = c.logs(tail=tail).decode("utf-8", errors="replace")
        return {"logs": logs}
    except Exception as e:
        raise HTTPException(status_code=500, detail=_clean_reason(e))


@router.get("/images")
async def images():
    return await asyncio.to_thread(_images_sync)


def _images_sync():
    try:
        kind, client = get_backend()
    except HTTPException:
        raise
    if kind == "cli":
        try:
            arr = _podman_json(["images", "--format", "json"])
            result = []
            for img in arr:
                tags = img.get("Names") or img.get("RepoTags") or []
                result.append({
                    "id": (img.get("Id", "") or "")[:19],
                    "tags": tags if isinstance(tags, list) else [tags],
                    "size": img.get("Size", 0),
                    "created": img.get("Created", ""),
                })
            return result
        except Exception as e:
            raise HTTPException(status_code=500, detail=_clean_reason(e))
    try:
        result = []
        for img in client.images.list():
            result.append({
                "id": img.short_id,
                "tags": img.tags,
                "size": img.attrs.get("Size", 0),
                "created": img.attrs.get("Created", ""),
            })
        return result
    except Exception as e:
        global _client
        _client = None
        raise HTTPException(status_code=500, detail=_clean_reason(e))
