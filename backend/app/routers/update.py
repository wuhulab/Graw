# -*- coding: utf-8 -*-
"""
update.py - 面板自身版本检测与一键更新

版本源：
    Docker Hub 上官方镜像 `shunx/graw` 的语义版本 tag（如 1.2.0），
    与面板当前版本（app/main.py 的 APP_VERSION）比较得出是否有新版。

更新机制（Docker 容器部署两种形态，均以**独立执行容器**在宿主机完成，
避免面板容器被 stop 的瞬间更新进程被连带杀死）：

  1. compose 部署（docker compose up -d 安装）：
     从面板自身容器 labels 定位宿主机 compose 项目目录，再以独立的
     `docker/compose` 容器执行 `docker compose pull` 与 `docker compose up -d`。

  2. docker run 单容器部署（README 推荐的 `docker run -d --name graw-panel ...`
     安装方式，大部分安装者采用）：
     inspect 面板自身容器，把「原样重建参数」（镜像 / 挂载 / 端口 / 环境变量 /
     network / privileges / restart 策略等）写入宿主机临时目录，再启动一个
     独立的 shunx/graw 执行容器（挂载 docker.sock + 该临时目录 + 面板数据目录），
     由它完成：镜像 pull → 旧容器改名腾名 → 同名创建新容器 → 启动并等待稳定 →
     移除旧容器；任一步失败自动回滚（恢复旧容器原名并保持运行）。

    本机直接运行（非容器）时仅提供版本检测；一键更新返回 400 提示手动更新。

安全设计：
    - 版本查询 URL 为固定字符串，无 SSRF 风险；响应解析全量容错。
    - compose 工作目录/文件来自容器 labels（不可信），仅用于构造
      docker/compose 容器参数，路径做绝对路径/存在性校验。
    - docker run 重建：仅接受官方镜像 shunx/graw；重建脚本为代码内固定常量，
      容器配置只作为 JSON 参数传给执行容器（不拼接 shell 命令）；
      宿主临时目录权限收紧为 0700/0600（内含容器环境变量）。
    - 写操作接口挂 require_admin（main.py 统一挂 ADMIN 依赖）。
    - 并发防抖：同一时间只允许一个更新任务执行。
"""
import json
import logging
import os
import posixpath
import re
import shutil
import socket
import threading
import time
import urllib.error
import urllib.request
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException

logger = logging.getLogger("graw.update")

router = APIRouter()

# Docker Hub 上 Graw 官方镜像仓库
IMAGE_REPO = "shunx/graw"
_HUB_TAGS_URL = "https://hub.docker.com/v2/repositories/{}/tags".format(IMAGE_REPO)
_HTTP_TIMEOUT = 15  # 版本查询网络超时（秒）

# 语义版本 tag 白名单：1.2.0 / v1.2.0；拒绝 latest/buildcache 等非版本号
_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")

# 更新日志文件（backend/data/update.log），供排障
_DATA_DIR = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "data")
)
_UPDATE_LOG = os.path.join(_DATA_DIR, "update.log")

# 更新执行容器镜像（标准 docker compose 官方镜像）
_COMPOSE_IMAGE = "docker/compose:latest"
# 宿主机 docker socket 挂载点
_DOCKER_SOCK = "/var/run/docker.sock"

# 更新任务运行状态（防并发）
_update_state = {"running": False}


# ------------------------------------------------------------
# 版本比较工具
# ------------------------------------------------------------
def _version_key(tag: str) -> tuple:
    """把版本 tag 解析为可比较元组；非版本号返回 (0, 0, 0)（永远小于真实版本）。"""
    m = _VERSION_RE.match((tag or "").strip())
    if not m:
        return (0, 0, 0)
    return tuple(int(x) for x in m.groups())


def _current_version() -> str:
    """读取面板当前版本（延迟导入避免与 main.py 循环依赖）。"""
    from app.main import APP_VERSION  # noqa: PLC0415

    return APP_VERSION


# ------------------------------------------------------------
# 部署环境检测
# ------------------------------------------------------------
def _is_container() -> bool:
    """是否运行在 Docker 容器中（compose 设置了 HOST_ROOT，或存在 /.dockerenv）。"""
    if os.environ.get("HOST_ROOT"):
        return True
    return os.path.exists("/.dockerenv")


def _compose_context() -> Dict[str, object]:
    """从面板自身容器 labels 定位宿主机 compose 项目上下文。

    返回 {"working_dir": str, "config_files": [str]}；非 compose 部署或
    查询失败返回空字典（调用方据此提示手动更新）。
    """
    try:
        import docker  # 已在 requirements.txt，延迟导入减少启动开销

        client = docker.from_env()
        me = client.containers.get(socket.gethostname())  # 容器内 hostname 即容器 ID
        labels = (me.attrs.get("Config", {}) or {}).get("Labels", {}) or {}
    except Exception as e:  # noqa: BLE001 - 容器信息查询失败属预期（本机/无 socket）
        logger.warning("获取面板自身容器信息失败: %s", e)
        return {}
    working_dir = (labels.get("com.docker.compose.project.working_dir") or "").strip()
    config_files = [
        f.strip()
        for f in (labels.get("com.docker.compose.project.config_files") or "").split(",")
        if f.strip()
    ]
    # compose 工作目录 label 缺失时，用首个配置文件所在目录兜底，保证挂载可用
    if not working_dir and config_files:
        working_dir = os.path.dirname(config_files[0])
    return {"working_dir": working_dir, "config_files": config_files}


# ------------------------------------------------------------
# docker run 单容器部署：检测与重建参数构造
# ------------------------------------------------------------
def _docker_run_detect() -> bool:
    """单容器 docker run 部署检测：面板自身容器镜像是否为官方 shunx/graw。"""
    try:
        import docker  # noqa: PLC0415 - 延迟导入减少启动开销

        client = docker.from_env()
        me = client.containers.get(socket.gethostname())
        img = (me.attrs.get("Config", {}) or {}).get("Image", "") or ""
        return img.partition(":")[0] == IMAGE_REPO
    except Exception as e:  # noqa: BLE001 - 无 docker socket / 本机运行属预期
        logger.warning("单容器部署检测失败: %s", e)
        return False


def _mounts_to_binds(mounts: Optional[List[dict]]) -> List[str]:
    """把 HostConfig.Mounts（--mount 语法）转为 Binds（-v 语法）列表。

    仅转换 bind/volume 两类挂载（保留 mode 后缀），tmpfs 等其余类型
    由 HostConfig.Tmpfs 原样透传保留，避免重复挂载。
    """
    binds: List[str] = []
    for m in mounts or []:
        typ = (m.get("Type") or "").lower()
        if typ not in ("bind", "volume"):
            continue
        src = (m.get("Source") or m.get("Name") or "").strip()
        dst = (m.get("Destination") or "").strip()
        if not src or not dst:
            continue
        entry = f"{src}:{dst}"
        mode = (m.get("Mode") or "").strip()
        if mode:
            entry += f":{mode}"
        binds.append(entry)
    return binds


def _host_data_dir(attrs: dict) -> str:
    """从容器挂载信息推导数据目录的宿主机路径（/app/backend/data 的绑定源）。

    供 docker run 更新时把独立执行容器的完整日志直接写回面板 update.log。
    """
    hconfig = attrs.get("HostConfig") or {}
    for b in hconfig.get("Binds") or []:
        parts = b.split(":")
        if len(parts) >= 2 and parts[1].strip("/") == "app/backend/data":
            return parts[0].strip()
    for m in hconfig.get("Mounts") or []:
        if (m.get("Type") == "bind") and (m.get("Destination") or "").strip("/") == "app/backend/data":
            return (m.get("Source") or "").strip()
    return ""


def _docker_run_context() -> Dict[str, object]:
    """构造单容器（docker run）部署的重建上下文。

    inspect 面板自身容器，把「原样重建」所需参数整理为独立执行容器可直接
    消费的 JSON（create kwargs + HostConfig 白名单）。返回结构：
      {"ok": True, "container_id", "name", "image", "pull_repo", "pull_tag",
       "backup_name", "create": {...}, "data_host_dir": str}
      或 {"ok": False, "error": "..."}

    安全约束：
      - 仅接受官方镜像 IMAGE_REPO（shunx/graw），自定义镜像拒绝一键更新；
      - container:<x> 网络模式（引用其它容器）无法安全重建，拒绝；
      - HostConfig 采用白名单字段透传，键名均为 docker SDK 稳定参数。
    """
    try:
        import docker  # noqa: PLC0415

        client = docker.from_env()
        me = client.containers.get(socket.gethostname())  # 容器内 hostname 即容器 ID
        attrs = me.attrs or {}
    except Exception as e:  # noqa: BLE001 - 获取容器信息失败属预期
        logger.warning("获取面板自身容器信息失败: %s", e)
        return {"ok": False, "error": "无法获取面板自身容器信息，请改用「Docker 管理-容器升级」手动更新。"}

    config = attrs.get("Config") or {}
    hconfig = attrs.get("HostConfig") or {}
    current_image = (config.get("Image") or "").strip()
    repo, _, tag = current_image.partition(":")
    if not tag:
        tag = "latest"
    if repo != IMAGE_REPO:
        return {
            "ok": False,
            "error": f"面板容器镜像 {current_image!r} 非官方镜像 {IMAGE_REPO}，"
            "不支持一键更新，请改用「Docker 管理-容器升级」手动更新。",
        }

    # 目标镜像：当前 tag 为 latest 则继续跟随 latest；否则升到 Docker Hub 最新版本号
    latest = _fetch_latest_version()
    if not latest:
        return {"ok": False, "error": "无法获取 Docker Hub 最新版本，请稍后重试。"}
    pull_tag = "latest" if tag == "latest" else latest
    target_image = f"{IMAGE_REPO}:{pull_tag}"

    # ----- 基础字段（Config）-----
    create_kwargs: Dict[str, object] = {
        "image": target_image,
        "name": me.name,
        "detach": True,
    }
    # 命令/入口优先取镜像原始值（docker inspect 的 Original* 为 run 时实参）
    cmd = config.get("OriginalCmd") or config.get("Cmd")
    if cmd:
        create_kwargs["command"] = cmd
    entrypoint = config.get("OriginalEntrypoint") or config.get("Entrypoint")
    if entrypoint:
        create_kwargs["entrypoint"] = entrypoint
    if config.get("WorkingDir"):
        create_kwargs["working_dir"] = config["WorkingDir"]
    if config.get("User"):
        create_kwargs["user"] = config["User"]
    if config.get("Labels"):
        create_kwargs["labels"] = config["Labels"]
    if isinstance(config.get("Env"), list):
        create_kwargs["environment"] = config["Env"]
    # hostname：仅当用户显式指定（默认是短容器 ID，重建后应使用新容器的 ID）
    # 容器 Id 缺失时无法判断是否为默认值，保守不传（由 docker 自动分配）
    hostname = (config.get("Hostname") or "").strip()
    if hostname and me.id and hostname != str(me.id)[:12]:
        create_kwargs["hostname"] = hostname

    # ----- HostConfig（白名单透传）-----
    host_cfg: Dict[str, object] = {}
    binds = hconfig.get("Binds") or []
    if not binds:
        binds = _mounts_to_binds(hconfig.get("Mounts"))
    if binds:
        host_cfg["binds"] = binds
    if hconfig.get("PortBindings"):
        host_cfg["port_bindings"] = hconfig["PortBindings"]
    network_mode = (hconfig.get("NetworkMode") or "").strip() or "bridge"
    if network_mode.startswith("container:"):
        return {
            "ok": False,
            "error": "面板容器使用 container 网络模式（引用其它容器），无法安全重建，"
            "请改用「Docker 管理-容器升级」手动更新。",
        }
    if network_mode in ("default", ""):
        network_mode = "bridge"  # docker API 中 default 与 bridge 等价，统一传 bridge
    host_cfg["network_mode"] = network_mode
    for src, dst in (
        ("Privileged", "privileged"),
        ("PidMode", "pid_mode"),
        ("RestartPolicy", "restart_policy"),
        ("Devices", "devices"),
        ("CapAdd", "cap_add"),
        ("CapDrop", "cap_drop"),
        ("ExtraHosts", "extra_hosts"),
        ("Dns", "dns"),
        ("LogConfig", "log_config"),
        ("ShmSize", "shm_size"),
        ("SecurityOpt", "security_opt"),
        ("IpcMode", "ipc_mode"),
        ("Ulimits", "ulimits"),
        ("Sysctls", "sysctls"),
        ("Runtime", "runtime"),
        ("AutoRemove", "auto_remove"),
        ("ReadonlyRootfs", "readonly_rootfs"),
        ("Tmpfs", "tmpfs"),
    ):
        v = hconfig.get(src)
        if v:  # 仅透传非空值；None/False 用镜像默认
            host_cfg[dst] = v
    create_kwargs["host_config"] = host_cfg

    return {
        "ok": True,
        "container_id": me.id,
        "name": me.name,
        "image": target_image,
        "pull_repo": repo,
        "pull_tag": pull_tag,
        "backup_name": f"{me.name}-old-{time.strftime('%Y%m%d%H%M%S')}",
        "create": create_kwargs,
        "data_host_dir": _host_data_dir(attrs),
    }


# ------------------------------------------------------------
# 版本检测
# ------------------------------------------------------------
def _fetch_latest_version() -> Optional[str]:
    """查询 Docker Hub 上最新语义版本 tag；网络/解析失败返回 None。"""
    try:
        req = urllib.request.Request(
            _HUB_TAGS_URL + "?page_size=100&ordering=last_updated",
            headers={"User-Agent": "Graw-Panel"},
        )
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001 - 网络波动/超时属预期
        logger.warning("查询 Docker Hub 最新版本失败: %s", e)
        return None
    if not isinstance(data, dict):
        return None
    tags = [
        t.get("name", "")
        for t in data.get("results", [])
        if isinstance(t, dict) and isinstance(t.get("name"), str)
    ]
    versions = [t for t in tags if _VERSION_RE.match(t.strip())]
    if not versions:
        return None
    return max(versions, key=_version_key)


@router.get("/status")
async def update_status():
    """面板版本状态：当前版本 / 最新版本 / 是否有新版 / 部署模式。"""
    current = _current_version()
    latest = _fetch_latest_version()
    if _is_container():
        deploy_mode = "docker"
        # 部署细分：compose / docker run 单容器 / 自定义镜像（不支持一键更新）
        if _compose_context().get("config_files"):
            deploy_detail = "compose"
        elif _docker_run_detect():
            deploy_detail = "docker-run"
        else:
            deploy_detail = "unsupported"
    else:
        deploy_mode = "local"
        deploy_detail = "local"
    return {
        "current_version": current,
        "latest_version": latest,
        "update_available": bool(latest) and _version_key(latest) > _version_key(current),
        "deploy_mode": deploy_mode,
        "deploy_detail": deploy_detail,
        "check_error": None if latest else "无法连接 Docker Hub 获取最新版本",
    }


@router.get("/log")
async def update_log():
    """读取面板最近一次一键更新日志（最后 150 行），供前端展示排障。"""
    try:
        with open(_UPDATE_LOG, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
        return {"log": "\n".join(lines[-150:])}
    except FileNotFoundError:
        return {"log": "(暂无更新记录)"}
    except Exception as e:  # noqa: BLE001 - 日志读取失败不应当影响接口可用性
        logger.warning("读取更新日志失败: %s", e)
        return {"log": f"(读取更新日志失败: {e})"}


# ------------------------------------------------------------
# 一键更新
# ------------------------------------------------------------
def _compose_mounts(working_dir: str, config_files: List[str]):
    """构造 docker/compose 执行容器的挂载与 -f 参数。

    将宿主机 compose 工作目录挂载到容器 /work，config_files 尽量转为
    /work 下的相对路径；无法转换的保留绝对路径（容器内通常不存在，
    执行时会报错并写入更新日志，提示用户手动处理）。
    """
    volumes = {
        _DOCKER_SOCK: {"bind": _DOCKER_SOCK, "mode": "rw"},
        working_dir: {"bind": "/work", "mode": "rw"},
    }
    args: List[str] = []
    for f in config_files:
        if os.path.isabs(f):
            try:
                rel = os.path.relpath(f, working_dir)
                if not rel.startswith(".."):
                    args += ["-f", rel]
                    continue
            except ValueError:  # lgtm[py/empty-except] 跨盘/不同根时 relpath 抛异常，回退原路径分支
                pass
        args += ["-f", f]
    return volumes, args


def _run_update_bg(ctx: Dict[str, object]) -> None:
    """后台执行 compose pull + up -d（独立容器），日志写入 update.log。"""
    working_dir = str(ctx.get("working_dir") or "")
    config_files = [str(x) for x in (ctx.get("config_files") or [])]
    lines: List[str] = []
    lines.append("=" * 60)
    lines.append(f"[{time_str()}] 开始一键更新（compose 目录: {working_dir}）")

    def _log(lines_: List[str], msg: str) -> None:
        lines_.append(msg)
        logger.info("面板更新: %s", msg)

    try:
        import docker  # noqa: PLC0415

        client = docker.from_env()
        volumes, compose_args = _compose_mounts(working_dir, config_files)
        if not config_files:
            raise RuntimeError("未检测到 compose 配置文件")
        for action, cmd in (("拉取镜像", ["pull"]), ("重建容器", ["up", "-d", "--remove-orphans"])):
            _log(lines, f"--- {action} ---")
            container = client.containers.run(
                _COMPOSE_IMAGE,
                command=compose_args + cmd,
                working_dir="/work",
                volumes=volumes,
                detach=True,
                remove=True,
            )
            # 阻塞等待执行容器结束，流式收集输出
            exit_code = container.wait(timeout=1800)
            try:
                logs = container.logs().decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001 - 日志读取失败不影响主流程
                logs = ""
            _log(lines, (logs or "(无输出)")[-3000:])
            if exit_code != 0:
                raise RuntimeError(f"{action} 失败（退出码 {exit_code}）")
        _log(lines, "更新完成，面板容器已重建，新版本即将生效")
    except Exception as e:  # noqa: BLE001 - 后台任务需捕获所有异常并落盘
        _log(lines, f"更新失败: {e}")
    finally:
        _update_state["running"] = False
        try:
            with open(_UPDATE_LOG, "a", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        except Exception:  # noqa: BLE001 - 日志写入失败不抛给后台线程
            pass


# ------------------------------------------------------------
# docker run 单容器更新：独立执行容器内置重建脚本
# ------------------------------------------------------------
# 该脚本运行在独立的 shunx/graw 执行容器内（挂载 docker.sock + 宿主临时目录 /work
# + 面板数据目录 /data），由面板后台线程启动；面板容器被重建/重启时它不受影响。
# 脚本本身是代码内固定常量，容器配置仅作为 JSON（/work/cfg.json）传入，不拼接
# shell 命令，规避命令注入风险。
_UPDATER_SCRIPT = r'''# -*- coding: utf-8 -*-
"""执行容器内置的面板自更新脚本（docker run 单容器部署）。

运行在独立的 shunx/graw 执行容器内，挂载 docker.sock + 宿主临时目录(/work)
+ 面板数据目录(/data)；面板容器被重建/重启时本脚本不受影响。
容器配置由面板侧以 JSON 写入 /work/cfg.json，脚本按该配置「原样重建」面板。
"""
import json
import time
import traceback

_LOGGED_PATHS = ("/work/update.log", "/data/update.log")


def _log(msg):
    line = "[" + time.strftime("%Y-%m-%d %H:%M:%S") + "] " + str(msg)
    print(line, flush=True)
    for p in _LOGGED_PATHS:
        try:
            with open(p, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass


def _state(client, cid):
    try:
        return client.containers.get(cid).attrs["State"]
    except Exception:
        return {}


def main():
    try:
        with open("/work/cfg.json", "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception as e:
        _log("读取更新配置失败: " + str(e))
        return 1

    import docker
    from docker.types import HostConfig

    client = docker.from_env()
    cid = cfg["container_id"]
    name = cfg["name"]
    backup_name = cfg["backup_name"]
    create_kwargs = dict(cfg["create"])
    # HostConfig 由 JSON dict 还原为 SDK 类型
    hc = create_kwargs.pop("host_config", None) or {}
    create_kwargs["host_config"] = HostConfig(**hc)

    _log("目标镜像: " + str(create_kwargs.get("image")))
    try:
        old = client.containers.get(cid)
    except Exception as e:
        _log("找不到面板旧容器: " + str(e))
        return 1

    # 旧容器改名为 原容器名-old-<ts>，腾出原名给新容器（原名保持不变；
    # 挂载/端口/重启策略在重建时透传创建参数，不丢失）
    try:
        old.rename(backup_name)
        _log("旧容器已改名: " + backup_name)
    except Exception as e:
        _log("旧容器改名失败: " + str(e))
        return 1

    new = None
    try:
        _log("正在创建新面板容器...")
        new = client.containers.create(**create_kwargs)
        _log("新容器已创建: " + str(new.id or "")[:12])
        new.start()
        _log("新容器已启动，等待其稳定运行...")
        deadline = time.time() + 60
        stable = False
        while time.time() < deadline:
            time.sleep(2)
            st = _state(client, new.id)
            if st.get("Running") and not st.get("Restarting"):
                stable = True
                break
        if not stable:
            raise RuntimeError("新面板容器未能稳定运行（Exited/Restarting）")
        _log("新面板容器运行稳定，移除旧容器")
        try:
            old.remove(force=True)
            _log("旧容器已移除")
        except Exception as e:
            _log("移除旧容器失败（可手动清理）: " + str(e))
        _log("面板更新完成，新版本已生效")
        return 0
    except Exception as e:
        _log("面板更新失败，开始回滚: " + str(e))
        _log(traceback.format_exc())
        # 回滚：删除未成功启动的新容器，旧容器恢复原名并保持运行
        try:
            if new is not None:
                client.containers.get(new.id).remove(force=True)
                _log("已移除未成功启动的新容器")
        except Exception:
            pass
        try:
            old.rename(name)
            _log("旧面板容器已恢复原名")
            if not _state(client, old.id).get("Running"):
                old.start()
                _log("旧面板容器已重新启动（回滚成功）")
            else:
                _log("旧面板容器保持运行（回滚成功）")
        except Exception as e2:
            _log("回滚失败，请手动恢复旧容器 " + backup_name + ": " + str(e2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
'''


def _prune_old_tmp(host_root: str) -> None:
    """清理宿主机 /tmp 下 3 天前的面板更新临时目录，避免长期累积。"""
    try:
        base = posixpath.join(host_root, "tmp")
        now = time.time()
        for entry in os.listdir(base):
            if not entry.startswith("graw-update-"):
                continue
            p = posixpath.join(base, entry)
            try:
                if os.path.isdir(p) and now - os.path.getmtime(p) > 3 * 86400:
                    shutil.rmtree(p, ignore_errors=True)
            except OSError:  # noqa: BLE001 - 单个目录清理失败跳过
                continue
    except OSError:  # noqa: BLE001 - 清理失败不影响更新主流程
        pass


def _run_update_bg_docker(ctx: Dict[str, object]) -> None:
    """docker run 单容器部署的容器内后台更新：pull 镜像 + 独立执行容器重建。

    执行时机都在面板自身容器内（线程），但真正改动容器的是独立执行容器：
      - 面板线程只负责拉取镜像、写脚本/配置、启动执行容器、等待并抄写日志；
      - 执行容器完成「新建同名容器 → 启动稳定 → 移除旧容器」的重建动作，
        （旧容器被移除时面板线程随之终止属预期，其完整日志已由执行容器直接
        写入面板数据目录的 update.log）。
    """
    lines: List[str] = ["=" * 60]
    lines.append(f"[{time_str()}] 开始一键更新（docker run 重建，镜像: {ctx.get('image')}）")

    def _log(msg: str) -> None:
        lines.append(msg)
        logger.info("面板更新(docker run): %s", msg)

    try:
        import docker  # noqa: PLC0415

        client = docker.from_env()
        host_root = os.environ.get("HOST_ROOT", "/host") or "/"

        # 1) 拉取新版镜像（可能耗时较长，面板在此期间保持在线）
        _log(f"正在拉取镜像 {ctx['image']} ...")
        client.images.pull(ctx.get("pull_repo"), tag=ctx.get("pull_tag"))
        _log(f"镜像拉取完成: {ctx['image']}")

        # 2) 准备重建脚本与配置（写入宿主机临时目录；配置含容器环境变量，
        #    权限收紧为 0700/0600）
        ts = time.strftime("%Y%m%d%H%M%S")
        host_tmp_abs = posixpath.join(host_root, "tmp", f"graw-update-{ts}")
        try:
            os.makedirs(host_tmp_abs, exist_ok=True)
            os.chmod(host_tmp_abs, 0o700)
        except OSError:  # noqa: BLE001 - 非 POSIX 下 chmod 失败不影响
            pass
        # docker 守护进程视角的宿主机路径：面板容器内 /host/tmp/x 即宿主 /tmp/x
        host_tmp = (
            host_tmp_abs[len(host_root):] if host_tmp_abs.startswith(host_root) else host_tmp_abs
        )
        script_path = posixpath.join(host_tmp_abs, "run.py")
        cfg_path = posixpath.join(host_tmp_abs, "cfg.json")
        cfg = {
            "container_id": ctx.get("container_id"),
            "name": ctx.get("name"),
            "backup_name": ctx.get("backup_name"),
            "create": ctx.get("create"),
            "data_host_dir": ctx.get("data_host_dir", ""),
        }
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(_UPDATER_SCRIPT)
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False)
        for p in (script_path, cfg_path):
            try:
                os.chmod(p, 0o600)
            except OSError:  # noqa: BLE001
                pass
        _log(f"重建脚本已就绪: {host_tmp}")

        # 3) 挂载：docker.sock + 宿主临时目录 /work + 面板数据目录 /data
        volumes: Dict[str, dict] = {
            _DOCKER_SOCK: {"bind": _DOCKER_SOCK, "mode": "rw"},
            host_tmp: {"bind": "/work", "mode": "rw"},
        }
        if ctx.get("data_host_dir"):
            volumes[str(ctx["data_host_dir"])] = {"bind": "/data", "mode": "rw"}
        _log("正在启动独立执行容器...")
        c = client.containers.run(
            str(ctx["image"]),
            command=["python", "/work/run.py"],
            working_dir="/work",
            volumes=volumes,
            detach=True,
            remove=True,
        )
        _log("独立执行容器已启动，等待重建完成（面板容器将在此过程中被重建）...")
        exit_code = c.wait(timeout=3600)
        try:
            logs = c.logs().decode("utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - remove=True 容器日志读取失败属预期
            logs = ""
        _log(f"独立执行容器退出码: {exit_code}")
        if logs:
            _log("--- 执行容器输出 ---")
            _log(logs[-3000:])
        _prune_old_tmp(host_root)
        if exit_code != 0:
            _log("更新未完全成功，请查看上方日志或面板「更新日志」。")
        else:
            _log("一键更新（docker run）流程结束，请刷新页面确认新版本。")
    except Exception as e:  # noqa: BLE001 - 后台任务需捕获所有异常并落盘
        _log(f"更新失败: {e}")
    finally:
        _update_state["running"] = False
        try:
            with open(_UPDATE_LOG, "a", encoding="utf-8") as f:
                f.write("\n".join(lines) + "\n")
        except Exception:  # noqa: BLE001 - 日志写入失败不抛给后台线程
            pass


def time_str() -> str:
    """返回当前时间字符串（用于更新日志）。"""
    import datetime

    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@router.post("/apply")
async def apply_update():
    """触发一键更新（后台执行，立即返回）。

    部署形态支持：
      - Docker 容器 + compose：经独立 docker/compose 容器执行 pull + up -d；
      - Docker 容器 + docker run 单容器：镜像 pull + 按原容器配置同名重建；
      本机运行不支持一键更新。
    更新会重建面板自身容器，当前 HTTP 连接将在容器重启时断开。
    """
    if _update_state["running"]:
        raise HTTPException(status_code=409, detail="已有更新任务正在进行，请稍后再试")
    if not _is_container():
        raise HTTPException(
            status_code=400,
            detail="面板当前以本机方式运行，不支持一键更新。请使用 git pull 拉取最新代码后重启服务。",
        )
    # 1) compose 部署：沿用独立 docker/compose 容器执行 pull + up -d
    compose_ctx = _compose_context()
    if compose_ctx.get("config_files"):
        _update_state["running"] = True
        threading.Thread(target=_run_update_bg, args=(compose_ctx,), daemon=True).start()
        logger.info("一键更新已触发（compose 目录: %s）", compose_ctx.get("working_dir"))
        return {
            "ok": True,
            "started": True,
            "message": "更新已启动，面板容器将自动重建。重建期间页面会短暂不可用，请稍后刷新。",
        }
    # 2) 单容器 docker run 部署：镜像 pull + 原样重建（配置从原容器透传）
    dctx = _docker_run_context()
    if not dctx.get("ok"):
        raise HTTPException(status_code=400, detail=dctx.get("error") or "当前部署方式不支持一键更新")
    _update_state["running"] = True
    threading.Thread(target=_run_update_bg_docker, args=(dctx,), daemon=True).start()
    logger.info("一键更新已触发（docker run 重建，镜像: %s，容器: %s）", dctx["image"], dctx["name"])
    return {
        "ok": True,
        "started": True,
        "message": "更新已启动：正在拉取新版镜像并按原配置原样重建面板容器（挂载、端口、环境变量与启动参数保持不变）。"
        "重建期间页面会短暂不可用，请稍后刷新；更新结果可查看「更新日志」。",
    }
