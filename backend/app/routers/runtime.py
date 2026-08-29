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
from pydantic import BaseModel, Field, field_validator
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
    # PHP：使用 php:apache 镜像，内置 Apache 随容器默认 CMD 自动启动，
    # suggest_cmd 留空以让镜像默认启动命令生效
    "php": {
        "label": "PHP",
        "versions": ["8.3", "8.2", "8.1", "8.0"],
        "default_version": "8.2",
        "image": lambda v: f"php:{v}-apache",
        "workdir": "/var/www/html",
        "suggest_cmd": "",
    },
    # HTML 静态项目：用 python 官方镜像自带的 SimpleHTTPServer 提供静态站点，
    # 启动端口（html_port）映射到容器内 80 端口，默认建议命令即 http.server
    "html": {
        "label": "HTML Project",
        "versions": ["3", "3.12"],
        "default_version": "3",
        "image": lambda v: f"python:{v}-alpine",
        "workdir": "/app",
        "suggest_cmd": "python -m http.server 80",
    },
    # 其他项目：无默认语言环境，镜像固定为基础 Linux（debian），
    # 环境安装命令（install_command）需用户手动填写，创建时先执行安装再启动
    "other": {
        "label": "Other",
        "versions": ["12", "11", "10"],
        "default_version": "12",
        "image": lambda v: f"debian:{v}",
        "workdir": "/app",
        "suggest_cmd": "",
    },
}

# 端口协议选项
PORT_PROTOCOLS = ("tcp", "udp")
# 挂载读写模式
MOUNT_MODES = ("rw", "ro")

# 容器名校验：Docker/Podman 容器名仅允许字母数字及 _ . -，且不能以 - 开头
_CONTAINER_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")

# 镜像名白名单：仅允许常见 registry/repo:tag 形态。注意必须以字母/数字开头，
# 防止以 `-` 开头的镜像名被 podman/docker 解析成 run 的选项（选项注入）。
_IMAGE_RE = re.compile(r"^[a-z0-9][a-z0-9](?:[a-z0-9._/-]*[a-z0-9_])?(?::[A-Za-z0-9][A-Za-z0-9._-]{0,127})?$")

# ---------------------------------------------------------------------------
# 宿主路径挂载安全校验（第十二轮审计修复，Low）
#
# 背景：MountItem.host / project_dir 会原样拼入容器引擎的 `-v <host>:<c>`。
# 此前 host 无任何校验，管理员可挂载宿主机根目录 / 或 docker.sock 进容器，
# 容器内即可读写宿主任意文件 / 执行任意 docker 命令（宿主逃逸级能力），
# 与 files.py 对 data/ 目录的保护基线不一致。
#
# 规则：
#   - 必须为绝对路径（Linux / 开头；Windows 盘符/UNC 一律拒绝——容器引擎
#     挂载语义以 POSIX 为准，UNC 设备共享路径无正当用途）；
#   - 拒绝 Windows 设备命名空间（\\?\ / \\.\），防 commonpath fail-open；
#   - 拒绝根目录 /（挂载根 = 宿主全盘逃逸）；
#   - 拒绝 docker.sock 与系统敏感目录（/etc /usr /var /run /bin /sbin /boot
#     /proc /sys /dev /lib /lib64 /root 及其子路径）——运行时开发容器
#     没有理由挂载这些目录；
#   - 拒绝面板数据目录（data/，含 JWT 密钥 / 用户表等敏感文件）。
# ---------------------------------------------------------------------------
_DATA_DIR_NORM = os.path.normpath(os.path.abspath(DATA_DIR))
_MOUNT_SYSTEM_ROOTS = tuple(
    os.path.normpath(r) for r in (
        "/etc", "/usr", "/var", "/run", "/bin", "/sbin", "/boot",
        "/proc", "/sys", "/dev", "/lib", "/lib64", "/root",
    )
)
# 额外可通过环境变量追加拒绝根（逗号分隔），便于部署方自定义
for _extra in os.environ.get("GRAW_RUNTIME_MOUNT_DENY", "").split(","):
    _extra = _extra.strip()
    if _extra:
        _MOUNT_SYSTEM_ROOTS += (os.path.normpath(_extra),)


def _validate_mount_host(host: str, field: str = "挂载源路径") -> str:
    """校验将被挂载进容器的宿主机路径；非法抛 HTTPException。"""
    host = (host or "").strip()
    if not host:
        raise HTTPException(status_code=400, detail=f"{field}不能为空")
    if host.startswith("\\\\?\\") or host.startswith("\\\\.\\"):
        raise HTTPException(status_code=400, detail=f"{field}非法（不支持设备命名空间路径）")
    if host.startswith("\\\\") or host.startswith("//") or (len(host) > 1 and host[1] == ":"):
        raise HTTPException(status_code=400, detail=f"{field}非法（不支持 Windows 盘符/UNC 路径）")
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in host):
        raise HTTPException(status_code=400, detail=f"{field}包含非法控制字符")
    norm = os.path.normpath(host.replace("\\", "/"))
    if norm in ("/", "."):
        raise HTTPException(status_code=400, detail=f"{field}不能是根目录或当前目录")
    if "docker.sock" in norm:
        raise HTTPException(status_code=400, detail=f"{field}禁止挂载 docker.sock（宿主逃逸风险）")
    for root in _MOUNT_SYSTEM_ROOTS:
        if norm == root or norm.startswith(root + "/"):
            raise HTTPException(
                status_code=400, detail=f"{field}位于系统敏感目录（{root}），禁止挂载"
            )
    try:
        in_data = os.path.commonpath([os.path.normcase(norm), os.path.normcase(_DATA_DIR_NORM)]) == os.path.normcase(_DATA_DIR_NORM)
    except ValueError:
        in_data = False
    if in_data:
        raise HTTPException(status_code=400, detail=f"{field}位于面板数据目录，禁止挂载")
    return host


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

        # 兜底分支：cmd 与主分支同源，镜像名已过白名单校验，install/启动命令为管理员容器内功能脚本
        p = subprocess.run(cmd, capture_output=True, timeout=timeout)  # lgtm[py/command-line-injection]
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
    # 项目目录自动挂载到工作目录（入口已校验，此处纵深防御）
    if cfg.get("project_dir"):
        _validate_mount_host(cfg["project_dir"], "项目目录")
        args += ["-v", f"{_mount_path(cfg['project_dir'])}:{workdir}"]
    # 端口映射：外部:内部/协议
    for p in cfg.get("ports") or []:
        ext, internal, proto = p.get("external"), p.get("internal"), p.get("protocol", "tcp")
        if not ext or not internal:
            continue
        proto = proto if proto in PORT_PROTOCOLS else "tcp"
        args += ["-p", f"{ext}:{internal}/{proto}"]
    # HTML 静态项目：填写的启动端口映射到容器内 80 端口
    if cfg.get("type") == "html" and cfg.get("html_port"):
        args += ["-p", f"{cfg['html_port']}:80/tcp"]
    # 环境变量
    for e in cfg.get("env") or []:
        name, value = e.get("name"), e.get("value", "")
        if not name:
            continue
        args += ["-e", f"{name}={value}"]
    # 自定义挂载：宿主机:容器[:ro]（入口已校验，此处纵深防御）
    for m in cfg.get("mounts") or []:
        host, container = m.get("host"), m.get("container")
        if not host or not container:
            continue
        _validate_mount_host(host, "挂载源路径")
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
    """容器的默认启动命令：优先使用配置，空则回退到运行时建议命令。

    若两者皆空（如 php 依赖镜像默认 CMD、other 仅填了安装命令），返回空串，
    由调用方决定是否追加命令（镜像默认 CMD 将生效）。
    """
    return (cfg.get("start_command") or "").strip() or RUNTIMES.get(cfg.get("type"), {}).get("suggest_cmd", "")


def _build_entry_script(command: str, install_cmd: str) -> str:
    """构造容器入口脚本：先执行安装命令，再执行启动命令。

    - 仅安装命令：追加 tail 保持容器存活，便于 exec 进入
    - 安装命令 + 启动命令：用 && 串联
    返回空串表示无需脚本（不覆盖镜像默认 CMD）。
    """
    install_cmd = (install_cmd or "").strip()
    if not install_cmd:
        return ""
    command = (command or "").strip()
    if command:
        return f"{install_cmd} && {command}"
    return f"{install_cmd} && tail -f /dev/null"


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
    # 安全（code-scanning py/command-line-injection）：镜像名直接作为
    # `docker/podman run` 的位置参数，必须以合法镜像名白名单校验，
    # 防止以 `-` 开头被解析为 run 选项（选项注入）。
    if not _IMAGE_RE.match(image) or image.startswith("-"):
        raise HTTPException(status_code=400, detail=f"镜像名不合法: {image}")
    command = _default_command(cfg)
    # 「其他项目」若填写了环境安装命令，需用 shell 依次执行安装命令与启动命令
    script = _build_entry_script(command, cfg.get("install_command"))

    cmd = _engine_command(final_name) + [
        "run", "-d", "--name", final_name, "--label", "graw.runtime=1",
    ]
    cmd += _build_run_args(cfg)
    cmd += [image]

    if script:
        # 单 argv 传给 sh，避免命令内含引号导致参数拆分错误
        cmd += ["sh", "-lc", script]
    else:
        # 有启动命令则拆分附加；无则使用镜像默认 CMD（如 php:apache 自动启动）
        if command:
            try:
                command_tokens = shlex.split(command)
            except ValueError:
                command_tokens = command.split()
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
    """端口映射：external/internal 为端口号（1-65535 数字字符串）。

    安全：值最终拼入 `-p ext:int/proto`，必须是纯数字，防止以 `-`
    开头的值被容器引擎解析为额外选项（选项注入）。
    """

    external: Optional[str] = Field(None, pattern=r"^\d{1,5}$")
    internal: Optional[str] = Field(None, pattern=r"^\d{1,5}$")
    protocol: str = "tcp"


class EnvVar(BaseModel):
    """环境变量：名称必须是合法 POSIX 环境变量名（防选项注入）。"""

    name: str = Field("", pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")
    value: str = ""


class MountItem(BaseModel):
    """自定义挂载：container 必须是容器内绝对路径（防选项注入）。"""

    host: str = ""
    container: str = Field("", pattern=r"^/")
    mode: str = "rw"


class HostMap(BaseModel):
    """主机映射：hostname 白名单 + ip 必须可解析为合法 IP。"""

    hostname: str = Field("", pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,253}$")
    ip: str = ""


def _check_port_range(ports: List["PortMap"]) -> List["PortMap"]:
    """端口范围校验（1-65535）：pattern 只约束数字格式，范围在此统一拦截。"""
    for p in ports:
        for side in (p.external, p.internal):
            if side is not None and not (1 <= int(side) <= 65535):
                raise ValueError(f"端口越界: {side}（必须在 1-65535）")
    return ports


def _check_host_ips(hosts: List["HostMap"]) -> List["HostMap"]:
    """主机映射 IP 合法性校验（IPv4/IPv6），拦截任意文本注入 --add-host。"""
    import ipaddress

    for h in hosts:
        if h.ip:
            try:
                ipaddress.ip_address(h.ip)
            except ValueError:
                raise ValueError(f"主机映射 IP 不合法: {h.ip}")
    return hosts


class _RuntimeValidatorMixin(BaseModel):
    """运行环境创建请求的公共校验器（字段级白名单 + 范围校验）。

    安全：所有字段最终会拼入容器引擎 CLI argv——以 `-` 开头的版本号/
    端口/变量名会被 docker 解析为额外选项（选项注入，如 --privileged），
    故各字段均加 pattern 白名单；端口范围与 IP 合法性由 validator 拦截。
    """

    # check_fields=False：ports/hosts 字段声明在子类 RuntimeCreate 中，
    # 校验器在 Mixin 层定义时需显式跳过基类字段存在性检查
    @field_validator("ports", check_fields=False)
    @classmethod
    def _ports_range(cls, v):
        return _check_port_range(v)

    @field_validator("hosts", check_fields=False)
    @classmethod
    def _hosts_ip(cls, v):
        return _check_host_ips(v)

    @field_validator("html_port", check_fields=False)
    @classmethod
    def _html_port_range(cls, v):
        # HTML 项目启动端口范围校验（1-65535），pattern 只约束数字格式
        if v is not None:
            try:
                if not (1 <= int(v) <= 65535):
                    raise ValueError(f"启动端口越界: {v}（必须在 1-65535）")
            except (TypeError, ValueError):
                raise ValueError(f"启动端口不合法: {v}（必须在 1-65535）")
        return v


class RuntimeCreate(_RuntimeValidatorMixin):
    type: str = Field(..., pattern="^(python|java|node|go|dotnet|php|html|other)$")
    name: str = Field(..., min_length=1, max_length=64)
    project_dir: str = Field(..., min_length=1)
    start_command: str = ""
    # 版本号白名单：值会拼入镜像名（如 python:<version>），
    # 以 `-` 开头的版本会被 docker 解析为选项（如 --privileged）
    app_version: str = Field("", pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,31}$")
    container_name: str = ""
    notes: str = ""
    # HTML 静态项目的对外访问（启动）端口，映射到容器内 80 端口
    html_port: Optional[str] = Field(None, pattern=r"^\d{1,5}$")
    # 其他项目的环境安装命令：容器创建/启动时先执行，再启动 start_command
    install_command: str = ""
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
    # 安全修复（第十二轮审计）：项目目录与自定义挂载的宿主路径均不得为
    # 根目录 / 系统敏感目录 / docker.sock / 面板数据目录（防容器逃逸）。
    _validate_mount_host(req.project_dir, "项目目录")
    for m in req.mounts or []:
        _validate_mount_host(m.host, "挂载源路径")
    if req.container_name and not _CONTAINER_NAME_RE.match(req.container_name):
        raise HTTPException(status_code=400, detail="容器名称只能包含字母、数字、_、-、.，且不能以 - 开头")
    # 端口范围 / 主机映射 IP 已由 RuntimeCreate 的 field_validator 拦截

    cfg = {
        "id": "rt_" + uuid.uuid4().hex[:8],
        "type": req.type,
        "name": req.name.strip(),
        "project_dir": req.project_dir.strip(),
        "start_command": req.start_command.strip(),
        "app_version": req.app_version.strip() or RUNTIMES.get(req.type, {}).get("default_version", ""),
        "container_name": req.container_name.strip(),
        "notes": req.notes.strip(),
        "html_port": req.html_port,
        "install_command": req.install_command.strip(),
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