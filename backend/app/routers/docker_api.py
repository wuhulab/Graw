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

# 可选依赖 PyYAML：用于解析 compose 项目 services（缺失时降级为空）
try:
    import yaml
    yaml_available = True
    _safe_yaml_load = yaml.safe_load
except Exception:
    yaml_available = False

    def _safe_yaml_load(text):  # pragma: no cover - 仅当 PyYAML 缺失时生效
        return None

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

# 容器附加元数据（标星 / 备注笔记）持久化文件，位于 backend/data/
_DATA_DIR = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data"))
_META_FILE = os.path.join(_DATA_DIR, "docker_meta.json")
# 容器备份目录（docker save 生成的 tar 包）
_BACKUP_DIR = os.path.join(_DATA_DIR, "docker-backups")


# ------------------------------------------------------------
# 容器附加元数据（标星 / 备注笔记）
# ------------------------------------------------------------
def _load_meta() -> dict:
    """读取容器元数据 {starred: {id: true}, notes: {id: "..."}}，文件损坏时返回空结构。"""
    default = {"starred": {}, "notes": {}}
    if not os.path.exists(_META_FILE):
        return default
    try:
        with open(_META_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return default
        return {
            "starred": data.get("starred") if isinstance(data.get("starred"), dict) else {},
            "notes": data.get("notes") if isinstance(data.get("notes"), dict) else {},
        }
    except Exception:
        return default


def _save_meta(meta: dict) -> None:
    """持久化容器元数据到 JSON 文件（线程安全地原子写入）。"""
    try:
        os.makedirs(_DATA_DIR, exist_ok=True)
        tmp = _META_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _META_FILE)
    except Exception:
        # 元数据写入失败不应拖垮主流程，仅记录日志
        import logging
        logging.getLogger("docker_api").warning("保存容器元数据失败", exc_info=True)


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


def _wsl_prefix() -> list:
    """返回在 WSL 中执行宿主机命令的命令前缀。

    当 Windows 且容器引擎通过 WSL 运行时返回 ["wsl","-u","root","--"]，
    否则返回空列表（直接在宿主/容器内执行）。
    """
    if not IS_WINDOWS:
        return []
    cli = _find_podman()
    if cli and cli[0] == "wsl":
        # cli = ["wsl","-u","root","--","podman"]，去掉末尾的 podman 即宿主机命令前缀
        return cli[:-1]
    return []


def _host_to_wsl_path(p: str) -> str:
    """把 Windows 宿主路径转为 WSL 内路径（C:\\x -> /mnt/c/x）。

    供通过 WSL 执行的 podman/宿主机命令使用（如备份导出文件路径）。
    """
    import re

    p = p.replace("\\", "/")
    m = re.match(r"^([A-Za-z]):(/.*)$", p)
    if m:
        return f"/mnt/{m.group(1).lower()}{m.group(2)}"
    return p


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
    meta = _load_meta()
    if kind == "cli":
        try:
            arr = _podman_json(["ps", "-a", "--format", "json"])
            stats = _stats_sync() if any(c.get("Status", "").startswith("Up") for c in arr) else {}
            result = []
            for c in arr:
                state = "running" if c.get("Status", "").startswith("Up") else c.get("Status", "exited")
                name = (c.get("Names") or [""])[0] if c.get("Names") else ""
                st = stats.get(name, {})
                cid = c.get("Id", "")[:12]
                result.append({
                    "id": cid,
                    "name": name,
                    "image": c.get("Image", ""),
                    "status": c.get("Status", ""),
                    "state": state,
                    "created": c.get("Created", ""),
                    "ports": _parse_ports(c.get("Ports")),
                    "cpu_percent": st.get("cpu_percent", 0),
                    "mem_percent": st.get("mem_percent", 0),
                    "mem_usage": st.get("mem_usage", ""),
                    "starred": bool(meta["starred"].get(cid)),
                    "note": meta["notes"].get(cid, ""),
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
            cid = c.short_id
            result.append({
                "id": cid,
                "name": c.name,
                "image": c.image.tags[0] if c.image.tags else c.image.short_id,
                "status": c.status,
                "state": attrs.get("State", {}).get("Status", c.status),
                "created": attrs.get("Created", ""),
                "ports": ports,
                "cpu_percent": st.get("cpu_percent", 0),
                "mem_percent": st.get("mem_percent", 0),
                "mem_usage": st.get("mem_usage", ""),
                "starred": bool(meta["starred"].get(cid)),
                "note": meta["notes"].get(cid, ""),
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


# ------------------------------------------------------------
# 标星 / 取消标星
# ------------------------------------------------------------
@router.post("/containers/{container_id}/star")
async def toggle_star(container_id: str):
    """切换容器标星状态，返回最新的 starred 状态。"""
    return await asyncio.to_thread(_toggle_star_sync, container_id)


def _toggle_star_sync(container_id: str):
    meta = _load_meta()
    cid = container_id[:12]
    now_starred = not meta["starred"].get(cid, False)
    if now_starred:
        meta["starred"][cid] = True
    else:
        meta["starred"].pop(cid, None)
    _save_meta(meta)
    return {"starred": now_starred}


# ------------------------------------------------------------
# 容器备注笔记
# ------------------------------------------------------------
class NotesRequest(BaseModel):
    note: str = ""


@router.post("/containers/{container_id}/notes")
async def save_notes(container_id: str, req: NotesRequest):
    """保存容器备注笔记。"""
    return await asyncio.to_thread(_save_notes_sync, container_id, req.note)


def _save_notes_sync(container_id: str, note: str):
    meta = _load_meta()
    cid = container_id[:12]
    if note.strip():
        meta["notes"][cid] = note.strip()
    else:
        meta["notes"].pop(cid, None)
    _save_meta(meta)
    return {"ok": True}


# ------------------------------------------------------------
# 容器详细信息
# ------------------------------------------------------------
@router.get("/containers/{container_id}/inspect")
async def container_inspect(container_id: str):
    """返回容器详细信息：创建时间 / CPU / 核心数 / 内存 / 缓存 / 层大小等。"""
    return await asyncio.to_thread(_container_inspect_sync, container_id)


def _find_install_dir(inspect_data: dict) -> str:
    """从容器 inspect 数据推断应用安装目录（Graw 应用商店安装的 compose 项目目录）。

    优先使用 compose 项目标签定位 data/appstore/<project>；
    找不到时回退扫描 data/appstore 下 compose 内容包含该容器名的目录。
    """
    config = inspect_data.get("Config", {}) or {}
    labels = config.get("Labels", {}) or {}
    project = (
        labels.get("io.podman.compose.project")
        or labels.get("com.docker.compose.project")
        or ""
    ).strip()
    if project:
        candidate = os.path.join(_DATA_DIR, "appstore", project)
        if os.path.isdir(candidate):
            return os.path.normpath(candidate)

    name = (inspect_data.get("Name", "") or "").strip()
    appstore_dir = os.path.join(_DATA_DIR, "appstore")
    if os.path.isdir(appstore_dir):
        try:
            for entry in sorted(os.listdir(appstore_dir)):
                project_dir = os.path.join(appstore_dir, entry)
                if not os.path.isdir(project_dir):
                    continue
                compose = os.path.join(project_dir, "docker-compose.yml")
                if not os.path.isfile(compose):
                    continue
                try:
                    with open(compose, "r", encoding="utf-8") as f:
                        content = f.read()
                except Exception:
                    continue
                # 项目目录名或容器名出现在 compose 内容中即视为匹配
                if (entry and entry in content) or (name and name in content):
                    return os.path.normpath(project_dir)
        except Exception:
            pass
    return ""


def _container_inspect_sync(container_id: str):
    try:
        kind, client = get_backend()
    except HTTPException:
        raise
    if kind == "cli":
        try:
            # 1) podman inspect 获取容器基础信息
            arr = _podman_json(["inspect", container_id])
            if not arr:
                raise HTTPException(status_code=404, detail="容器不存在")
            c = arr[0]
            cid = c.get("Id", "")[:12]
            name = c.get("Name", "")
            created_raw = c.get("Created", "")
            state = c.get("State", {}) or {}
            host_config = c.get("HostConfig", {}) or {}
            config = c.get("Config", {}) or {}
            image_name = config.get("Image", "") or c.get("ImageName", "")
            image_id = c.get("ImageID", "") or ""

            # 2) 获取 stats（CPU 使用 / 内存使用 / CPU 总时长）
            stats_list = _podman_json(["stats", "--no-stream", "--format", "json"])
            stats = {}
            for s in stats_list:
                sn = s.get("name") or s.get("container") or ""
                if sn == name:
                    stats = s
                    break

            cpu_percent = _parse_percent(stats.get("cpu_percent", "0%"))
            cpu_time = stats.get("cpu_time", "")
            mem_usage_str = stats.get("mem_usage", "")
            mem_percent = _parse_percent(stats.get("mem_percent", "0%"))

            # 解析内存使用 / 限额
            mem_usage_val = 0
            mem_limit_val = 0
            if mem_usage_str:
                parts = mem_usage_str.split("/")
                if len(parts) == 2:
                    mem_usage_val = _parse_size_bytes(parts[0].strip())
                    mem_limit_val = _parse_size_bytes(parts[1].strip())

            # 3) 容器占用核心数（CPU 限额）
            #    NanoCpus 为纳秒级 CPU 配额（/1e9 即核数）；
            #    CpuQuota/CpuPeriod 为配额/周期（相除即核数）。
            #    两者均为 0 表示未限制。
            nano_cpus = host_config.get("NanoCpus", 0) or 0
            cpu_quota = host_config.get("CpuQuota", 0) or 0
            cpu_period = host_config.get("CpuPeriod", 0) or 0
            if nano_cpus > 0:
                cpu_cores = round(nano_cpus / 1e9, 2)
            elif cpu_quota > 0 and cpu_period > 0:
                cpu_cores = round(cpu_quota / cpu_period, 2)
            else:
                cpu_cores = 0

            # 4) 缓存使用（通过 cgroup memory.stat 的 file 字段估算）
            cache_bytes = 0
            try:
                cgroup_path = state.get("CgroupPath", "")
                if cgroup_path:
                    prefix = _wsl_prefix()
                    # WSL 内路径必须使用正斜杠（os.path.join 在 Windows 上会用反斜杠）
                    mem_stat = "/sys/fs/cgroup/" + cgroup_path.lstrip("/") + "/memory.stat"
                    cmd = prefix + ["cat", mem_stat]
                    if cmd:
                        rc, out, _ = _run(cmd, timeout=10)
                        if rc == 0:
                            for line in out.splitlines():
                                line = line.strip()
                                # cgroup v2 memory.stat 的 file 行表示文件缓存（近似 cache）
                                if line.startswith("file ") and not line.startswith("file_"):
                                    try:
                                        cache_bytes = int(line.split()[1])
                                    except Exception:
                                        pass
                                    break
            except Exception:
                pass

            # 5) 容器层大小（writable 层 = GraphDriver UpperDir 的磁盘占用）
            layer_size = 0
            try:
                upper_dir = (c.get("GraphDriver", {}) or {}).get("Data", {}).get("UpperDir", "")
                if upper_dir:
                    prefix = _wsl_prefix()
                    cmd = prefix + ["du", "-sb", upper_dir]
                    if cmd:
                        rc, out, _ = _run(cmd, timeout=30)
                        if rc == 0:
                            try:
                                layer_size = int(out.split()[0])
                            except Exception:
                                pass
            except Exception:
                pass

            # 6) 镜像虚拟大小（按 ImageName 在 images 列表中匹配）
            virtual_size = 0
            try:
                imgs = _podman_json(["images", "--format", "json"])
                for img in imgs:
                    names = img.get("Names") or img.get("RepoTags") or []
                    if not isinstance(names, list):
                        names = [names]
                    if image_name in names or (image_id and img.get("Id", "").startswith(image_id[:12])):
                        virtual_size = int(img.get("VirtualSize", 0) or img.get("Size", 0))
                        break
            except Exception:
                pass

            install_dir = _find_install_dir(c)

            return {
                "id": cid,
                "name": name,
                "image": image_name,
                "state": state.get("Status", ""),
                "created": created_raw,
                "started_at": state.get("StartedAt", ""),
                "finished_at": state.get("FinishedAt", ""),
                "cpu_percent": cpu_percent,
                "cpu_time": cpu_time,
                "cpu_cores": cpu_cores,
                "mem_usage": mem_usage_val,
                "mem_limit": mem_limit_val,
                "mem_percent": mem_percent,
                "cache_usage": cache_bytes,
                "layer_size": layer_size,
                "virtual_size": virtual_size,
                "restart_count": c.get("RestartCount", 0),
                "pid": state.get("Pid", 0),
                "cmd": c.get("Command") or config.get("Cmd", []),
                "entrypoint": config.get("Entrypoint", ""),
                "env": config.get("Env", []),
                "mounts": c.get("Mounts", []),
                "network_mode": host_config.get("NetworkMode", ""),
                "restart_policy": host_config.get("RestartPolicy", {}).get("Name", ""),
                "ports": c.get("Ports") or [],
                "install_dir": install_dir,
            }
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=_clean_reason(e))
    else:
        # Docker SDK 模式
        try:
            c = client.containers.get(container_id)
            attrs = c.attrs
            state = attrs.get("State", {}) or {}
            host_config = attrs.get("HostConfig", {}) or {}
            config = attrs.get("Config", {}) or {}

            # 获取 stats
            cpu_percent = 0
            mem_usage_val = 0
            mem_limit_val = 0
            mem_percent = 0
            try:
                s = c.stats(stream=False)
                mem = s.get("memory_stats", {}) or {}
                usage = mem.get("usage", 0)
                limit = mem.get("limit", 0)
                mem_usage_val = usage
                mem_limit_val = limit
                mem_percent = round(usage / limit * 100, 2) if limit else 0
                cpu = s.get("cpu_stats", {}) or {}
                precpu = s.get("precpu_stats", {}) or {}
                cpu_delta = (cpu.get("cpu_usage", {}) or {}).get("total_usage", 0) - \
                            (precpu.get("cpu_usage", {}) or {}).get("total_usage", 0)
                sys_delta = cpu.get("system_cpu_usage", 0) - precpu.get("system_cpu_usage", 0)
                online = cpu.get("online_cpus", 1) or 1
                cpu_percent = round(cpu_delta / sys_delta * online * 100, 2) if sys_delta > 0 else 0
            except Exception:
                pass

            # 镜像大小
            virtual_size = 0
            try:
                if c.image:
                    virtual_size = c.image.attrs.get("Size", 0)
            except Exception:
                pass

            # 容器占用核心数（CPU 限额）
            nano_cpus = host_config.get("NanoCpus", 0) or 0
            cpu_quota = host_config.get("CpuQuota", 0) or 0
            cpu_period = host_config.get("CpuPeriod", 0) or 0
            if nano_cpus > 0:
                cpu_cores = round(nano_cpus / 1e9, 2)
            elif cpu_quota > 0 and cpu_period > 0:
                cpu_cores = round(cpu_quota / cpu_period, 2)
            else:
                cpu_cores = 0

            return {
                "id": c.short_id,
                "name": c.name,
                "image": c.image.tags[0] if c.image.tags else c.image.short_id,
                "state": state.get("Status", ""),
                "created": attrs.get("Created", ""),
                "started_at": state.get("StartedAt", ""),
                "finished_at": state.get("FinishedAt", ""),
                "cpu_percent": cpu_percent,
                "cpu_time": "",
                "cpu_cores": cpu_cores,
                "mem_usage": mem_usage_val,
                "mem_limit": mem_limit_val,
                "mem_percent": mem_percent,
                "cache_usage": 0,
                "layer_size": 0,
                "virtual_size": virtual_size,
                "restart_count": attrs.get("RestartCount", 0),
                "pid": state.get("Pid", 0),
                "cmd": config.get("Cmd", []),
                "entrypoint": config.get("Entrypoint", ""),
                "env": config.get("Env", []),
                "mounts": [],
                "network_mode": host_config.get("NetworkMode", ""),
                "restart_policy": host_config.get("RestartPolicy", {}).get("Name", ""),
                "ports": [],
            }
        except HTTPException:
            raise
        except Exception as e:
            global _client
            _client = None
            raise HTTPException(status_code=500, detail=_clean_reason(e))


# ------------------------------------------------------------
# 备份容器（docker export / podman export）
# ------------------------------------------------------------
@router.post("/containers/{container_id}/backup")
async def backup_container(container_id: str):
    """导出容器文件系统为 tar 包，保存到 data/docker-backups/ 目录。"""
    return await asyncio.to_thread(_backup_container_sync, container_id)


def _backup_container_sync(container_id: str):
    try:
        kind, client = get_backend()
    except HTTPException:
        raise
    os.makedirs(_BACKUP_DIR, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    backup_path = os.path.join(_BACKUP_DIR, f"{container_id[:12]}_{timestamp}.tar")

    if kind == "cli":
        # Windows + WSL podman 时，导出路径需转换为 WSL 内路径（/mnt/...）
        out_path = _host_to_wsl_path(backup_path) if IS_WINDOWS and _find_podman() and _find_podman()[0] == "wsl" else backup_path
        cmd = _find_podman() + ["export", "-o", out_path, container_id]
        rc, out, err = _run(cmd, timeout=600)
        if rc != 0:
            raise HTTPException(status_code=500, detail=err.strip() or "备份失败")
    else:
        try:
            c = client.containers.get(container_id)
            with open(backup_path, "wb") as f:
                for chunk in c.export():
                    f.write(chunk)
        except Exception as e:
            raise HTTPException(status_code=500, detail=_clean_reason(e))

    # 获取文件大小
    file_size = os.path.getsize(backup_path) if os.path.exists(backup_path) else 0
    return {"ok": True, "path": backup_path, "size": file_size}


# ------------------------------------------------------------
# 升级容器（重新拉取镜像 + 重建容器）
# ------------------------------------------------------------
@router.post("/containers/{container_id}/upgrade")
async def upgrade_container(container_id: str):
    """重新拉取容器镜像，停止并删除旧容器，用相同参数重建。"""
    return await asyncio.to_thread(_upgrade_container_sync, container_id)


def _upgrade_container_sync(container_id: str):
    try:
        kind, client = get_backend()
    except HTTPException:
        raise
    if kind == "cli":
        # 1) 获取容器配置
        arr = _podman_json(["inspect", container_id])
        if not arr:
            raise HTTPException(status_code=404, detail="容器不存在")
        c = arr[0]
        image_name = c.get("ImageName", "") or c.get("Config", {}).get("Image", "")

        # 2) 重新拉取镜像
        rc, pull_out, pull_err = _run(_find_podman() + ["pull", image_name], timeout=600)
        if rc != 0:
            raise HTTPException(status_code=500, detail=f"拉取镜像失败: {pull_err.strip() or pull_out.strip()}")

        # 3) 获取原容器创建命令，重放重建（CreateCommand 形如
        #    ["podman","run","-d",<flags>,"<image>"] 或 ["podman","create",...]）
        create_cmd = c.get("Config", {}).get("CreateCommand", [])
        if not create_cmd:
            raise HTTPException(status_code=400, detail="无法获取容器创建参数，请手动升级")

        # 去掉开头的 "podman"（保留 run/create），再拼接引擎前缀执行
        sub = create_cmd[1:] if create_cmd[:1] == ["podman"] else create_cmd
        is_create = sub[:1] == ["create"]

        # 删除旧容器（失败也不阻塞，继续重建）
        _run(_find_podman() + ["rm", "-f", container_id], timeout=30)

        rc, create_out, create_err = _run(_find_podman() + sub, timeout=120)
        if rc != 0:
            raise HTTPException(status_code=500, detail=f"重建容器失败: {create_err.strip() or create_out.strip()}")

        new_id = create_out.strip()
        # podman create 只创建不启动，需要手动 start
        if is_create and new_id:
            rc_s, _so, se = _run(_find_podman() + ["start", new_id], timeout=60)
            if rc_s != 0:
                raise HTTPException(status_code=500, detail=f"启动容器失败: {se.strip()}")

        return {"ok": True, "new_container_id": (new_id or "")[:12], "image": image_name}
    else:
        # Docker SDK 模式
        try:
            c = client.containers.get(container_id)
            image = c.image
            # 拉取新镜像
            image.pull()
            # 获取配置重建
            config = c.attrs
            name = c.name
            c.remove(force=True)
            new_c = client.containers.run(image, name=name, detach=True, **{})
            return {"ok": True, "new_container_id": new_c.short_id, "image": str(image.tags)}
        except Exception as e:
            raise HTTPException(status_code=500, detail=_clean_reason(e))


# ------------------------------------------------------------
# 制作镜像（docker commit）
# ------------------------------------------------------------
class CommitRequest(BaseModel):
    repo: str = ""
    tag: str = "latest"


@router.post("/containers/{container_id}/commit")
async def commit_container(container_id: str, req: CommitRequest = None):
    """将容器当前状态提交为新镜像。"""
    return await asyncio.to_thread(_commit_container_sync, container_id, req)


def _commit_container_sync(container_id: str, req: CommitRequest = None):
    repo = (req.repo.strip() if req and req.repo else f"graw-commit-{container_id[:12]}").strip()
    tag = (req.tag.strip() if req and req.tag else "latest").strip()
    try:
        kind, client = get_backend()
    except HTTPException:
        raise
    if kind == "cli":
        full_name = f"{repo}:{tag}" if tag else repo
        rc, out, err = _run(_find_podman() + ["commit", container_id, full_name], timeout=300)
        if rc != 0:
            raise HTTPException(status_code=500, detail=err.strip() or "制作镜像失败")
        return {"ok": True, "image": full_name, "id": out.strip()[:19]}
    else:
        try:
            c = client.containers.get(container_id)
            img = c.commit(repository=repo, tag=tag)
            return {"ok": True, "image": f"{repo}:{tag}", "id": img.short_id}
        except Exception as e:
            raise HTTPException(status_code=500, detail=_clean_reason(e))


# ------------------------------------------------------------
# 辅助：解析 docker 大小字符串（如 "2.892MB / 4.011GB"）
# ------------------------------------------------------------
def _parse_size_bytes(s: str) -> int:
    """将 "2.892MB" 或 "4.011GB" 等大小字符串解析为字节数。"""
    s = s.strip()
    if not s:
        return 0
    try:
        return int(s)
    except (ValueError, TypeError):
        pass
    units = {
        "B": 1, "KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3,
        "TB": 1024 ** 4, "PB": 1024 ** 5,
        "K": 1024, "M": 1024 ** 2, "G": 1024 ** 3, "T": 1024 ** 4,
        "KiB": 1024, "MiB": 1024 ** 2, "GiB": 1024 ** 3, "TiB": 1024 ** 4,
    }
    import re
    m = re.match(r"^([\d.]+)\s*([A-Za-z]+)$", s)
    if m:
        try:
            val = float(m.group(1))
            unit = m.group(2)
            return int(val * units.get(unit, 1))
        except (ValueError, KeyError):
            pass
    return 0


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


# ------------------------------------------------------------
# 引擎配置（镜像加速 / 私有仓库 / iptables / 打开配置文件）
# ------------------------------------------------------------
# 面板自身配置（iptables 等无法写入 podman 的字段）
_PANEL_CFG_FILE = os.path.join(_DATA_DIR, "docker_config.json")


def _load_panel_cfg() -> dict:
    default = {"iptables": True}
    if not os.path.exists(_PANEL_CFG_FILE):
        return default
    try:
        with open(_PANEL_CFG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else default
    except Exception:
        return default


def _save_panel_cfg(cfg: dict) -> None:
    try:
        os.makedirs(_DATA_DIR, exist_ok=True)
        tmp = _PANEL_CFG_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, _PANEL_CFG_FILE)
    except Exception:
        pass


def _engine_config_path() -> tuple:
    """返回 (engine, config_path, config_type)。

    podman -> /etc/containers/registries.conf (toml)
    docker -> /etc/docker/daemon.json (json)
    """
    engine = _cli_engine() or ""
    if engine == "podman":
        return "podman", "/etc/containers/registries.conf", "toml"
    return "docker", "/etc/docker/daemon.json", "json"


def _read_engine_config(path: str) -> str:
    """读取引擎配置文件内容（WSL 内文件用 wsl 前缀）。"""
    prefix = _wsl_prefix()
    if prefix:
        rc, out, _ = _run(prefix + ["cat", path], timeout=15)
        if rc != 0:
            return ""
        return out
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""


def _write_engine_config(path: str, content: str) -> None:
    """写回引擎配置文件（WSL 内文件用 wsl 前缀 + stdin）。"""
    prefix = _wsl_prefix()
    if prefix:
        p = subprocess.run(
            prefix + ["sh", "-c", "cat > " + path],
            input=content.encode("utf-8"),
            capture_output=True,
            timeout=15,
        )
        if p.returncode != 0:
            raise RuntimeError(p.stderr.decode("utf-8", "replace").strip() or "写入配置文件失败")
        return
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _parse_registries_toml(content: str) -> dict:
    """解析 registries.conf (TOML) 提取 search/mirrors/private_registries。"""
    import re

    result = {"search": [], "mirrors": [], "private": []}
    # unqualified-search-registries = [...]（顶层）
    m = re.search(r"unqualified-search-registries\s*=\s*\[([^\]]*)\]", content)
    if m:
        result["search"] = [x.strip().strip('"\'') for x in m.group(1).split(",") if x.strip()]
    # [registries.search] 块
    m = re.search(r"\[registries\.search\]\s*registries\s*=\s*\[([^\]]*)\]", content)
    if m:
        result["search"] = [x.strip().strip('"\'') for x in m.group(1).split(",") if x.strip()]
    # [[registry]] 与 [[registry.mirror]] 块
    blocks = re.findall(r"\[\[registry(?:\.[a-z]+)?\]\]\s*location\s*=\s*\"([^\"]+)\"", content)
    in_private = False
    for token in re.findall(r"\[\[registry(?:\.([a-z]+))?\]\]\s*(?:location\s*=\s*\"([^\"]+)\"|insecure\s*=\s*(true|false))", content):
        part, loc, insecure = token
        if part == "mirror" and loc:
            result["mirrors"].append(loc)
        elif part == "" and loc:
            if insecure == "true" or in_private:
                result["private"].append(loc)
    # 兜底：把非 docker.io 且带端口的 registry 视为私有仓库
    for loc in re.findall(r"\[\[registry\]\]\s*location\s*=\s*\"([^\"]+)\"", content):
        if loc not in ("docker.io", "index.docker.io") and loc not in result["private"] and loc not in result["mirrors"]:
            result["private"].append(loc)
    return result


def _build_registries_toml(search: list, mirrors: list, private: list) -> str:
    """生成 registries.conf (TOML)，把镜像加速作为 docker.io 的 mirror。"""
    lines = ["# 由 Graw 面板生成，请勿手动编辑本文件头", ""]
    search = search or ["docker.io"]
    lines.append(f"unqualified-search-registries = {json.dumps(search, ensure_ascii=False)}")
    lines.append("[registries.search]")
    lines.append(f"registries = {json.dumps(search, ensure_ascii=False)}")
    lines.append("")
    lines.append("[[registry]]")
    lines.append('location = "docker.io"')
    for m in mirrors:
        lines.append("")
        lines.append("[[registry.mirror]]")
        lines.append(f'location = "{m}"')
    for p in private:
        lines.append("")
        lines.append("[[registry]]")
        lines.append(f'location = "{p}"')
        lines.append("insecure = true")
    return "\n".join(lines) + "\n"


@router.get("/config")
async def docker_config():
    """返回引擎配置信息：镜像加速 / 私有仓库 / iptables / 配置全文。"""
    return await asyncio.to_thread(_docker_config_sync)


def _docker_config_sync():
    try:
        get_backend()
    except HTTPException:
        raise
    engine, config_path, config_type = _engine_config_path()
    content = _read_engine_config(config_path)
    panel = _load_panel_cfg()

    mirrors, private, search, iptables = [], [], [], None
    if config_type == "toml":
        parsed = _parse_registries_toml(content)
        search, mirrors, private = parsed["search"], parsed["mirrors"], parsed["private"]
    else:  # daemon.json
        try:
            data = json.loads(content) if content.strip() else {}
            mirrors = data.get("registry-mirrors", []) or []
            private = data.get("insecure-registries", []) or []
            iptables = data.get("iptables", None)
        except Exception:
            pass

    return {
        "engine": engine,
        "config_path": config_path,
        "config_type": config_type,
        "mirror_enabled": bool(mirrors),
        "mirrors": mirrors,
        "private_registries": private,
        "iptables": iptables if iptables is not None else bool(panel.get("iptables", True)),
        "iptables_supported": config_type == "json",
        "content": content,
    }


class ConfigRequest(BaseModel):
    mirror_enabled: bool = False
    mirrors: List[str] = []
    private_registries: List[str] = []
    iptables: bool = True


@router.put("/config")
async def update_docker_config(req: ConfigRequest):
    """保存配置：重建引擎配置文件并持久化面板字段。"""
    return await asyncio.to_thread(_update_docker_config_sync, req)


def _update_docker_config_sync(req: ConfigRequest):
    try:
        get_backend()
    except HTTPException:
        raise
    engine, config_path, config_type = _engine_config_path()

    # 清理空项
    mirrors = [m.strip() for m in (req.mirrors or []) if m and m.strip()]
    private = [p.strip() for p in (req.private_registries or []) if p and p.strip()]
    if not req.mirror_enabled:
        mirrors = []

    try:
        if config_type == "toml":
            # 保留已有 search 列表（兜底 docker.io）
            content = _read_engine_config(config_path)
            parsed = _parse_registries_toml(content)
            search = parsed["search"] or ["docker.io"]
            new_content = _build_registries_toml(search, mirrors, private)
            _write_engine_config(config_path, new_content)
        else:
            # daemon.json
            content = _read_engine_config(config_path)
            data = {}
            if content.strip():
                try:
                    data = json.loads(content)
                    if not isinstance(data, dict):
                        data = {}
                except Exception:
                    data = {}
            data["registry-mirrors"] = mirrors
            if private:
                data["insecure-registries"] = private
            else:
                data.pop("insecure-registries", None)
            data["iptables"] = bool(req.iptables)
            _write_engine_config(config_path, json.dumps(data, indent=2, ensure_ascii=False))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"写入引擎配置文件失败: {e}")

    # 持久化面板字段（iptables 在 podman 下仅记录）
    panel = _load_panel_cfg()
    panel["iptables"] = bool(req.iptables)
    panel["config_path"] = config_path
    panel["engine"] = engine
    _save_panel_cfg(panel)
    return {"ok": True, "config_path": config_path, "config_type": config_type,
            "iptables_supported": config_type == "json"}


class ConfigRawRequest(BaseModel):
    content: str


@router.put("/config/raw")
async def save_docker_config_raw(req: ConfigRawRequest):
    """直接保存引擎配置文件全文（"打开配置文件"编辑器使用）。"""
    return await asyncio.to_thread(_save_docker_config_raw_sync, req.content)


def _save_docker_config_raw_sync(content: str):
    try:
        get_backend()
    except HTTPException:
        raise
    _engine, config_path, _type = _engine_config_path()
    try:
        _write_engine_config(config_path, content)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"写入配置文件失败: {e}")
    return {"ok": True, "config_path": config_path}


# ------------------------------------------------------------
# 编排（compose 项目）
# ------------------------------------------------------------
_APPSTORE_DIR = os.path.join(_DATA_DIR, "appstore")


@router.get("/compose/projects")
async def compose_projects():
    """列出 compose 项目及其运行状态。"""
    return await asyncio.to_thread(_compose_projects_sync)


def _compose_projects_sync():
    """扫描应用商店 compose 项目目录，结合容器 labels 统计运行状态。"""
    try:
        kind, client = get_backend()
    except HTTPException:
        raise
    projects = []
    if os.path.isdir(_APPSTORE_DIR):
        for name in sorted(os.listdir(_APPSTORE_DIR)):
            pdir = os.path.join(_APPSTORE_DIR, name)
            compose = os.path.join(pdir, "docker-compose.yml")
            if not os.path.isdir(pdir) or not os.path.isfile(compose):
                continue
            projects.append({
                "name": name,
                "path": os.path.normpath(pdir),
                "compose_file": compose,
            })

    # 查询容器，统计每个 project 的运行状态
    services_by_project = {}
    try:
        if kind == "cli":
            ps = _podman_json(["ps", "-a", "--format", "json"])
        else:
            ps = []
            for c in client.containers.list(all=True):
                attrs = c.attrs
                ps.append({
                    "Id": c.id,
                    "Name": c.name,
                    "State": attrs.get("State", {}).get("Status", ""),
                    "Status": c.status,
                    "Labels": attrs.get("Config", {}).get("Labels", {}) or {},
                })
    except Exception:
        ps = []

    running = {}
    total = {}
    for c in ps:
        labels = c.get("Labels") or {}
        proj = labels.get("io.podman.compose.project") or labels.get("com.docker.compose.project")
        if not proj:
            continue
        total[proj] = total.get(proj, 0) + 1
        if c.get("State") == "running" or str(c.get("Status", "")).startswith("Up"):
            running[proj] = running.get(proj, 0) + 1

    for p in projects:
        p["total"] = total.get(p["name"], 0)
        p["running"] = running.get(p["name"], 0)
        # 解析 compose 的 services 名
        services = []
        if yaml_available:
            try:
                with open(p["compose_file"], "r", encoding="utf-8") as f:
                    data = _safe_yaml_load(f.read())
                if data and isinstance(data.get("services"), dict):
                    services = list(data.get("services", {}).keys())
            except Exception:
                pass
        p["services"] = services
    return projects


class ComposeActionRequest(BaseModel):
    action: str  # up / down / restart


@router.post("/compose/{name}/action")
async def compose_action(name: str, req: ComposeActionRequest):
    """对 compose 项目执行 up / down / restart。"""
    return await asyncio.to_thread(_compose_action_sync, name, req.action)


def _compose_action_sync(name: str, action: str):
    if action not in ("up", "down", "restart"):
        raise HTTPException(status_code=400, detail=f"不支持的操作: {action}")
    try:
        get_backend()
    except HTTPException:
        raise
    # 路径安全：只允许访问 appstore 目录下的项目
    pdir = os.path.normpath(os.path.join(_APPSTORE_DIR, os.path.basename(name)))
    if not pdir.startswith(os.path.normpath(_APPSTORE_DIR)):
        raise HTTPException(status_code=400, detail="非法项目名")
    compose = os.path.join(pdir, "docker-compose.yml")
    if not os.path.isfile(compose):
        raise HTTPException(status_code=404, detail=f"未找到项目 {name} 的 docker-compose.yml")

    engine_path = _host_to_wsl_path(compose) if IS_WINDOWS and _find_podman() and _find_podman()[0] == "wsl" else compose
    if action == "up":
        args = ["up", "-d", "--remove-orphans"]
    elif action == "down":
        args = ["down"]
    else:
        args = ["restart"]

    cmd = _find_podman() + ["compose", "-f", engine_path] + args
    try:
        rc, out, err = _run(cmd, timeout=600)
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="compose 操作超时")
    if rc != 0:
        raise HTTPException(status_code=500, detail=err.strip() or out.strip() or "compose 操作失败")
    return {"ok": True, "action": action, "output": (out or err).strip()[-2000:]}


# ------------------------------------------------------------
# 镜像：删除
# ------------------------------------------------------------
@router.post("/images/{image_id}/remove")
async def remove_image(image_id: str):
    """删除镜像（-f 强制）。"""
    return await asyncio.to_thread(_remove_image_sync, image_id)


def _remove_image_sync(image_id: str):
    try:
        kind, client = get_backend()
    except HTTPException:
        raise
    if kind == "cli":
        rc, out, err = _run(_find_podman() + ["rmi", "-f", image_id], timeout=120)
        if rc != 0:
            raise HTTPException(status_code=500, detail=err.strip() or "删除镜像失败")
        return {"ok": True}
    try:
        img = client.images.get(image_id)
        img.remove(force=True)
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=_clean_reason(e))


# ------------------------------------------------------------
# 网络
# ------------------------------------------------------------
@router.get("/networks")
async def networks():
    """列出容器网络。"""
    return await asyncio.to_thread(_networks_sync)


def _networks_sync():
    try:
        kind, client = get_backend()
    except HTTPException:
        raise
    if kind == "cli":
        try:
            arr = _podman_json(["network", "ls", "--format", "json"])
            result = []
            for n in arr:
                subnets = []
                for s in n.get("subnets") or []:
                    subnets.append({
                        "subnet": s.get("subnet", ""),
                        "gateway": s.get("gateway", ""),
                    })
                result.append({
                    "name": n.get("name", ""),
                    "id": (n.get("id", "") or "")[:12],
                    "driver": n.get("driver", ""),
                    "interface": n.get("network_interface", ""),
                    "created": n.get("created", ""),
                    "subnets": subnets,
                    "internal": bool(n.get("internal")),
                    "labels": n.get("labels") or {},
                })
            return result
        except Exception as e:
            raise HTTPException(status_code=500, detail=_clean_reason(e))
    try:
        result = []
        for net in client.networks.list():
            attrs = net.attrs
            ipam = attrs.get("IPAM", {}) or {}
            configs = ipam.get("Config", []) or []
            subnets = [{"subnet": c.get("Subnet", ""), "gateway": c.get("Gateway", "")} for c in configs]
            result.append({
                "name": net.name,
                "id": net.id[:12],
                "driver": attrs.get("Driver", ""),
                "interface": attrs.get("Options", {}).get("com.docker.network.bridge.name", ""),
                "created": attrs.get("Created", ""),
                "subnets": subnets,
                "internal": bool(attrs.get("Internal")),
                "labels": attrs.get("Labels", {}) or {},
            })
        return result
    except Exception as e:
        global _client
        _client = None
        raise HTTPException(status_code=500, detail=_clean_reason(e))


@router.post("/networks/{network_name}/remove")
async def remove_network(network_name: str):
    """删除容器网络（使用中的网络会报错）。"""
    return await asyncio.to_thread(_remove_network_sync, network_name)


def _remove_network_sync(network_name: str):
    try:
        kind, client = get_backend()
    except HTTPException:
        raise
    if kind == "cli":
        rc, out, err = _run(_find_podman() + ["network", "rm", network_name], timeout=60)
        if rc != 0:
            raise HTTPException(status_code=500, detail=err.strip() or "删除网络失败")
        return {"ok": True}
    try:
        net = client.networks.get(network_name)
        net.remove()
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=_clean_reason(e))
