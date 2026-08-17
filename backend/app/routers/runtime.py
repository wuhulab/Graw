# -*- coding: utf-8 -*-
"""
runtime.py - 运行环境管理

为面板提供「运行环境」应用，允许用户按语言运行时（Python / Java / Node.js /
Go / .NET）创建一个隔离的开发/运行容器，并自动挂载项目目录、映射端口、注入
环境变量、添加自定义挂载与主机名映射。

数据模型：
  每条运行环境配置持久化到 backend/data/runtime.json。创建配置时会使用
  podman/docker 在宿主机上创建并启动一个对应语言镜像的容器，容器的启动命令、
  端口、环境变量、挂载、主机映射均来自配置。

端点：
    GET  /api/runtime/templates        返回各运行时的模板信息（默认镜像/版本/命令）
    GET  /api/runtime/list             列出已保存的运行环境（含容器状态）
    POST /api/runtime/create           创建运行环境（保存配置 + 创建/启动容器）
    POST /api/runtime/{id}/delete      删除运行环境（强制删除容器 + 清除配置）
    POST /api/runtime/{id}/action      对容器执行 start / stop / restart / remove

数据文件：
    backend/data/runtime.json          运行环境配置
"""
import json
import logging
import os
import re
import shlex
import time
import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import List, Optional

# 复用 docker_api 的引擎发现 / CLI 执行 / 路径转换辅助
from app.routers import docker_api

logger = logging.getLogger("runtime")
router = APIRouter()

_ROUTERS_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(_ROUTERS_DIR, "..", "..", "data")
RUNTIME_FILE = os.path.join(DATA_DIR, "runtime.json")

# 容器缺省工作目录（项目目录会挂载到该路径并作为工作目录）
DEFAULT_WORKDIR = "/app"
# 容器名统一前缀，便于在 Docker 中识别为 Graw 运行环境容器
CONTAINER_PREFIX = "graw-rt-"

# ------------------------------------------------------------
# 运行时模板定义
# ------------------------------------------------------------
# 每个运行时：label（展示名）、versions（可选版本）、default_version、
# image（由版本号生成完整镜像名）、workdir（容器工作目录）、suggest_cmd（建议启动命令）
RUNTIMES = {
    "python": {
        "label": "Python",
        "versions": ["3.13", "3.12", "3.11", "3.10", "3.9"],
        "default_version": "3.12",
        "image": lambda v: f"python:{v}",
        "workdir": "/app",
        "suggest_cmd": "python app.py",
    },
    "java": {
        "label": "Java",
        "versions": ["17", "21", "11", "8"],
        "default_version": "17",
        "image": lambda v: f"openjdk:{v}",
        "workdir": "/app",
        "suggest_cmd": "java -jar app.jar",
    },
    "node": {
        "label": "Node.js",
        "versions": ["20", "22", "18", "16"],
        "default_version": "20",
        "image": lambda v: f"node:{v}",
        "workdir": "/app",
        "suggest_cmd": "npm start",
    },
    "go": {
        "label": "Go",
        "versions": ["1.23", "1.22", "1.21", "1.20"],
        "default_version": "1.22",
        "image": lambda v: f"golang:{v}",
        "workdir": "/app",
        "suggest_cmd": "go run main.go",
    },
    "dotnet": {
        "label": ".NET",
        "versions": ["8.0", "9.0", "6.0"],
        "default_version": "8.0",
        "image": lambda v: f"mcr.microsoft.com/dotnet/sdk:{v}",
        "workdir": "/app",
        "suggest_cmd": "dotnet run",
    },
}

# 端口协议选项
PORT_PROTOCOLS = ("tcp", "udp")
# 挂载读写模式
MOUNT_MODES = ("rw", "ro")

# 容器名校验：Docker/Podman 容器名仅允许字母数字及 _ . -，且不能以 - 开头
_CONTAINER_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


# ------------------------------------------------------------
# 配置持久化
# ------------------------------------------------------------
def _load_configs() -> list:
    """读取运行环境配置列表，文件缺失/损坏时返回空列表。"""
    if not os.path.exists(RUNTIME_FILE):
        return []
    try:
        with open(RUNTIME_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning("读取运行环境配置失败: %s", e)
        return []


def _save_configs(configs: list) -> None:
    """将运行环境配置原子写入 JSON 文件。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = RUNTIME_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(configs, f, ensure_ascii=False, indent=2)
    os.replace(tmp, RUNTIME_FILE)


def _get_config(rid: str) -> Optional[dict]:
    """按 id 查询运行环境配置，不存在返回 None。"""
    return next((c for c in _load_configs() if c.get("id") == rid), None)


# ------------------------------------------------------------
# 容器操作辅助
# ------------------------------------------------------------
def _engine_prefix() -> list:
    """返回 podman/docker 引擎前缀；引擎不可用时抛 503。"""
    try:
        docker_api.get_backend()
    except HTTPException:
        raise HTTPException(
            status_code=503, detail="未检测到运行中的 Docker/Podman 服务"
        )
    cmd = docker_api._find_podman()
    if cmd is None:
        # SDK 模式下暂不支持创建运行环境容器，给出可读提示
        raise HTTPException(
            status_code=501, detail="当前仅支持通过 podman/docker CLI 创建运行环境容器"
        )
    return cmd


def _mount_path(path: str) -> str:
    """把宿主路径转换为容器引擎可识别的挂载源路径。

    - Windows + WSL podman：转为 /mnt/c/... 形式
    - 其它场景：原样使用（面板直跑时路径即宿主机路径）
    """
    if docker_api.IS_WINDOWS and docker_api._find_podman() and docker_api._find_podman()[0] == "wsl":
        return docker_api._host_to_wsl_path(path)
    return path


def _engine_command(container_name: str) -> list:
    """返回针对某一容器的 CLI 定位前缀（如 [podman, --name] 之外的引擎前缀）。"""
    return _engine_prefix()


def _run(cmd, timeout=120):
    """执行命令并返回 (returncode, stdout, stderr)，统一 UTF-8 解码。"""
    try:
        return docker_api._run(cmd, timeout=timeout)
    except Exception:
        # docker_api._run 无 timeout 透传；此处保护以防超时
        import subprocess

        p = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


def _container_status(name: str) -> dict:
    """查询单个容器状态：运行中 / 已退出 / 不存在。

    通过 CLI `ps -a --filter name=...` 与 `inspect` 获取真实状态，失败时
    回退为 'unknown'，不因容器引擎短暂不可用而阻塞列表展示。
    """
    try:
        ps = docker_api._podman_json(["ps", "-a", "--filter", f"name=^{name}$", "--format", "json"])
        state = "unknown"
        running = False
        if ps:
            status = ps[0].get("Status", "")
            running = str(status).startswith("Up")
            state = "running" if running else "exited"
        return {
            "exists": bool(ps),
            "running": running,
            "state": state,
            "status": ps[0].get("Status", "") if ps else "",
            "container_id": (ps[0].get("Id", "") or "")[:12] if ps else "",
        }
    except Exception as e:
        logger.warning("查询容器状态失败 %s: %s", name, e)
        return {"exists": False, "running": False, "state": "unknown", "status": "", "container_id": ""}


def _build_run_args(cfg: dict) -> list:
    """根据运行环境配置构造 `run -d` 剩余参数（不含镜像与命令）。"""
    args = []
    workdir = (cfg.get("workdir") or DEFAULT_WORKDIR).rstrip("/")
    # 项目目录自动挂载到工作目录
    if cfg.get("project_dir"):
        args += ["-v", f"{_mount_path(cfg['project_dir'])}:{workdir}"]
    # 端口映射：外部:内部/协议
    for p in cfg.get("ports") or []:
        ext, internal, proto = p.get("external"), p.get("internal"), p.get("protocol", "tcp")
        if not ext or not internal:
            continue
        proto = proto if proto in PORT_PROTOCOLS else "tcp"
        args += ["-p", f"{ext}:{internal}/{proto}"]
    # 环境变量
    for e in cfg.get("env") or []:
        name, value = e.get("name"), e.get("value", "")
        if not name:
            continue
        args += ["-e", f"{name}={value}"]
    # 自定义挂载：宿主机:容器[:ro]
    for m in cfg.get("mounts") or []:
        host, container = m.get("host"), m.get("container")
        if not host or not container:
            continue
        mode = "ro" if m.get("mode") in MOUNT_MODES and m.get("mode") == "ro" else "rw"
        spec = f"{_mount_path(host)}:{container}"
        if mode == "ro":
            spec += ":ro"
        args += ["-v", spec]
    # 主机映射：--add-host 主机名:IP
    for h in cfg.get("hosts") or []:
        hostname, ip = h.get("hostname"), h.get("ip")
        if hostname and ip:
            args += ["--add-host", f"{hostname}:{ip}"]
    # 工作目录
    args += ["-w", workdir]
    # 默认不限制自动重启，保持简单；如需可加 --restart unless-stopped
    return args


def _build_image(cfg: dict) -> str:
    """根据运行时类型与版本号生成完整镜像名。"""
    rt = RUNTIMES.get(cfg.get("type"), {})
    version = cfg.get("app_version") or rt.get("default_version", "latest")
    build = rt.get("image")
    return build(version) if build else version or "latest"


def _default_command(cfg: dict) -> str:
    """容器的默认启动命令：优先使用配置，空则回退到运行时建议命令。"""
    return (cfg.get("start_command") or "").strip() or RUNTIMES.get(cfg.get("type"), {}).get("suggest_cmd", "tail -f /dev/null")


def _create_container(cfg: dict) -> dict:
    """创建并启动运行环境容器。

    构造 `podman/docker run -d ... <image> <command>`，返回容器名称与 ID。
    在容器已存在同名时自动追加短后缀以避免冲突。
    """
    base_name = cfg.get("container_name") or f"{CONTAINER_PREFIX}{cfg.get('name', 't')}"
    if not _CONTAINER_NAME_RE.match(base_name):
        raise HTTPException(status_code=400, detail=f"容器名称不合法: {base_name}")
    # 若同名容器已存在，追加短后缀
    final_name = base_name
    suffix = 0
    while _container_status(final_name)["exists"]:
        suffix += 1
        final_name = f"{base_name}-{suffix}"

    image = _build_image(cfg)
    command = _default_command(cfg)
    try:
        command_tokens = shlex.split(command)
    except ValueError:
        command_tokens = command.split()

    cmd = _engine_command(final_name) + [
        "run", "-d", "--name", final_name, "--label", "graw.runtime=1",
    ]
    cmd += _build_run_args(cfg)
    cmd += [image]
    cmd += command_tokens

    rc, out, err = _run(cmd, timeout=600)
    if rc != 0:
        raise HTTPException(status_code=500, detail=(err.strip() or out.strip() or "容器创建失败"))
    container_id = out.strip().splitlines()[-1][:12] if out.strip() else ""
    return {"container_name": final_name, "container_id": container_id, "image": image, "command": command}


# ------------------------------------------------------------
# 请求模型
# ------------------------------------------------------------
class PortMap(BaseModel):
    external: Optional[str] = None
    internal: Optional[str] = None
    protocol: str = "tcp"


class EnvVar(BaseModel):
    name: str = ""
    value: str = ""


class MountItem(BaseModel):
    host: str = ""
    container: str = ""
    mode: str = "rw"


class HostMap(BaseModel):
    hostname: str = ""
    ip: str = ""


class RuntimeCreate(BaseModel):
    type: str = Field(..., pattern="^(python|java|node|go|dotnet)$")
    name: str = Field(..., min_length=1, max_length=64)
    project_dir: str = Field(..., min_length=1)
    start_command: str = ""
    app_version: str = ""
    container_name: str = ""
    notes: str = ""
    ports: List[PortMap] = []
    env: List[EnvVar] = []
    mounts: List[MountItem] = []
    hosts: List[HostMap] = []


class ActionRequest(BaseModel):
    action: str  # start / stop / restart / remove


# ------------------------------------------------------------
# 端点
# ------------------------------------------------------------
@router.get("/templates")
async def templates():
    """返回各运行时模板：版本列表、默认版本、建议命令、工作目录、图片前缀。"""
    result = []
    for key, rt in RUNTIMES.items():
        result.append({
            "type": key,
            "label": rt["label"],
            "versions": rt["versions"],
            "default_version": rt["default_version"],
            "workdir": rt["workdir"],
            "suggest_cmd": rt["suggest_cmd"],
            "image": rt["image"](rt["default_version"]),
        })
    return {"runtimes": result, "port_protocols": list(PORT_PROTOCOLS), "mount_modes": list(MOUNT_MODES)}


@router.get("/list")
async def list_runtimes():
    """列出全部运行环境，并为每个配置附带对应容器的实时状态。"""
    configs = _load_configs()
    result = []
    for c in configs:
        item = dict(c)
        # 合并最新引擎状态（不修改磁盘配置）
        st = _container_status(c.get("container_name") or "")
        item["status"] = st
        result.append(item)
    return {"runtimes": result}


@router.post("/create")
async def create_runtime(req: RuntimeCreate):
    """创建运行环境：保存配置并创建/启动容器。

    校验：项目目录必须为绝对路径；容器名若填写需合法。创建容器失败时
    不会写入配置，保证配置与容器状态一致。
    """
    # 项目目录要求绝对路径（允许 Windows 盘符路径或 Linux 绝对路径）
    if not os.path.isabs(req.project_dir):
        raise HTTPException(status_code=400, detail="项目目录必须为绝对路径")
    if req.container_name and not _CONTAINER_NAME_RE.match(req.container_name):
        raise HTTPException(status_code=400, detail="容器名称只能包含字母、数字、_、-、.，且不能以 - 开头")

    cfg = {
        "id": "rt_" + uuid.uuid4().hex[:8],
        "type": req.type,
        "name": req.name.strip(),
        "project_dir": req.project_dir.strip(),
        "start_command": req.start_command.strip(),
        "app_version": req.app_version.strip() or RUNTIMES.get(req.type, {}).get("default_version", ""),
        "container_name": req.container_name.strip(),
        "notes": req.notes.strip(),
        "ports": [p.dict() for p in req.ports],
        "env": [e.dict() for e in req.env],
        "mounts": [m.dict() for m in req.mounts],
        "hosts": [h.dict() for h in req.hosts],
        "workdir": RUNTIMES.get(req.type, {}).get("workdir", DEFAULT_WORKDIR),
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }

    try:
        created = _create_container(cfg)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("创建运行环境容器失败: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=f"创建容器失败: {e}")

    # 回填实际容器名与启动信息
    cfg["container_name"] = created["container_name"]
    cfg["container_id"] = created["container_id"]
    cfg["image"] = created["image"]

    configs = _load_configs()
    configs.append(cfg)
    _save_configs(configs)
    return cfg


@router.post("/{rid}/delete")
async def delete_runtime(rid: str):
    """删除运行环境：强制删除关联容器并清除配置。"""
    cfg = _get_config(rid)
    if not cfg:
        raise HTTPException(status_code=404, detail="运行环境不存在")
    name = cfg.get("container_name")
    if name and _container_status(name)["exists"]:
        cmd = _engine_command(name) + ["rm", "-f", name]
        rc, _out, err = _run(cmd, timeout=120)
        if rc != 0:
            raise HTTPException(status_code=500, detail=(err.strip() or "删除容器失败"))
    configs = [c for c in _load_configs() if c.get("id") != rid]
    _save_configs(configs)
    return {"ok": True}


@router.post("/{rid}/action")
async def runtime_action(rid: str, req: ActionRequest):
    """对运行环境容器执行 start / stop / restart / remove。"""
    cfg = _get_config(rid)
    if not cfg:
        raise HTTPException(status_code=404, detail="运行环境不存在")
    name = cfg.get("container_name")
    if not name or not _container_status(name)["exists"]:
        raise HTTPException(status_code=404, detail="运行环境容器不存在，请重新创建")

    mapping = {"start": "start", "stop": "stop", "restart": "restart", "remove": "rm -f"}
    sub = mapping.get(req.action)
    if not sub:
        raise HTTPException(status_code=400, detail=f"不支持的操作: {req.action}")
    cmd = _engine_command(name) + sub.split() + [name]
    rc, _out, err = _run(cmd, timeout=180)
    if rc != 0:
        raise HTTPException(status_code=500, detail=(err.strip() or "操作失败"))
    return {"ok": True}