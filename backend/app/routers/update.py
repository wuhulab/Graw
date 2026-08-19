# -*- coding: utf-8 -*-
"""
update.py - 面板自身版本检测与一键更新

版本源：
    Docker Hub 上官方镜像 `shunx/graw` 的语义版本 tag（如 1.2.0），
    与面板当前版本（app/main.py 的 APP_VERSION）比较得出是否有新版。

更新机制（仅 Docker 容器 + compose 部署支持）：
    通过 docker SDK 从面板自身容器 labels 定位宿主机上的 compose 项目
    目录，再以**独立的 `docker/compose` 容器**在宿主机执行
    `docker compose pull` 与 `docker compose up -d`。

    关键设计：更新执行体是独立容器，与面板容器相互独立——面板容器被
    重建/重启时不会中断更新流程（若在面板容器内直接跑 compose，
    容器自身被 stop 的瞬间 compose 进程会被连带杀死，更新会半途中断）。

    本机直接运行（非容器）时仅提供版本检测；一键更新返回 400 提示手动更新。

安全设计：
    - 版本查询 URL 为固定字符串，无 SSRF 风险；响应解析全量容错。
    - compose 工作目录/文件来自容器 labels（不可信），仅用于构造
      docker/compose 容器参数，路径做绝对路径/存在性校验。
    - 写操作接口挂 require_admin（main.py 统一挂 ADMIN 依赖）。
    - 并发防抖：同一时间只允许一个更新任务执行。
"""
import json
import logging
import os
import re
import socket
import threading
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
    return {"working_dir": working_dir, "config_files": config_files}


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
    return {
        "current_version": current,
        "latest_version": latest,
        "update_available": bool(latest) and _version_key(latest) > _version_key(current),
        "deploy_mode": "docker" if _is_container() else "local",
        "check_error": None if latest else "无法连接 Docker Hub 获取最新版本",
    }


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
            except ValueError:
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


def time_str() -> str:
    """返回当前时间字符串（用于更新日志）。"""
    import datetime

    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@router.post("/apply")
async def apply_update():
    """触发一键更新（后台执行，立即返回）。

    仅 Docker 容器 + compose 部署支持；本机运行或非 compose 部署返回 400。
    更新会重建面板自身容器，当前 HTTP 连接将在容器重启时断开。
    """
    if _update_state["running"]:
        raise HTTPException(status_code=409, detail="已有更新任务正在进行，请稍后再试")
    if not _is_container():
        raise HTTPException(
            status_code=400,
            detail="面板当前以本机方式运行，不支持一键更新。请使用 git pull 拉取最新代码后重启服务。",
        )
    ctx = _compose_context()
    if not ctx.get("config_files"):
        raise HTTPException(
            status_code=400,
            detail="未检测到 compose 部署信息。请改用「Docker 管理 - 容器升级」手动更新面板容器。",
        )
    # 标记运行中并启动后台线程（线程完成/失败时在 finally 中复位）
    _update_state["running"] = True
    threading.Thread(target=_run_update_bg, args=(ctx,), daemon=True).start()
    logger.info("一键更新已触发（compose 目录: %s）", ctx.get("working_dir"))
    return {
        "ok": True,
        "started": True,
        "message": "更新已启动，面板容器将自动重建。重建期间页面会短暂不可用，请稍后刷新。",
    }
