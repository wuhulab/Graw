# -*- coding: utf-8 -*-
"""
backup.py - Graw 备份中心路由

功能：
  1. 通用备份任务：目录 / 文件类型的备份（手动立即备份 + 按 cron 计划备份），
     统一管理所有备份任务（站点目录、数据库数据目录、任意指定目录等）。
  2. 备份轮转：每个任务独立配置「保留份数」与「保留天数」，备份后自动清理
     超出策略的旧备份，防止备份目录被撑爆。
  3. 一键恢复：从备份文件解压还原到指定目标路径（默认还原到任务原路径）。
  4. 记录管理：从备份目录实时扫描生成备份记录列表，支持删除备份文件。

设计说明：
  - 手动备份用 Python tarfile 直接打包（不经 shell），避免 shell 注入；
    计划备份复用 cron 模块生成 shell tar 命令写入 crontab（与 protection
    同一套命名约定 {safe}_{yyyyMMdd_HHmmss}.tar.gz），两者产物可互相识别。
  - 备份目录内文件名严格匹配命名约定，扫描时按任务 safe 前缀归属，因此
    cron 生成的备份也能自动进入记录列表。
  - 支持"本机直跑"与"Docker /host 挂载"两种部署方式（hostfs 路径映射）。

数据存储：
  backend/data/backup.json :
    {
      "backup_dir": "/data/graw-backups" | "C:\\GrawBackups",  # 默认备份目录
      "tasks": [ { id/name/type/source/target/schedule/keep_count/keep_days/... } ]
    }
"""
import json
import logging
import os
import platform
import re
import shutil
import tarfile
import threading
import time
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.hostfs import host_path, unhost_path

logger = logging.getLogger("graw.backup")

router = APIRouter()

# ---------------------------------------------------------------------------
# 常量与全局状态
# ---------------------------------------------------------------------------
DATA_DIR = os.path.normpath(
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data"))
)
BACKUP_FILE = os.path.join(DATA_DIR, "backup.json")

IS_WINDOWS = platform.system() == "Windows"

# 默认备份目录（与 protection 的落盘位置保持一致，便于统一管理）
DEFAULT_BACKUP_DIR = r"C:\GrawBackups" if IS_WINDOWS else "/data/graw-backups"

# 默认计划（分 时 日 月 周 = 每天 02:30）
DEFAULT_BACKUP_SCHEDULE = "30 2 * * *"

# 备份文件命名约定：{safe}_{yyyyMMdd_HHmmss}.tar.gz
BACKUP_FILE_RE = re.compile(r"^(?P<safe>[A-Za-z0-9_.-]+)_(?P<ts>\d{8}_\d{6})\.tar\.gz$")

# 任务 id / 备份 safe 前缀白名单（防路径穿越与注入）
_BK_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# 数据写锁（防止并发读写 JSON）
_backup_lock = threading.Lock()


# ---------------------------------------------------------------------------
# 数据存储
# ---------------------------------------------------------------------------
def _load_backup() -> dict:
    """读取备份中心数据文件，损坏时返回空结构。"""
    if not os.path.exists(BACKUP_FILE):
        return {"backup_dir": DEFAULT_BACKUP_DIR, "tasks": []}
    try:
        with open(BACKUP_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning("读取 backup.json 失败，按空数据处理: %s", e)
        return {"backup_dir": DEFAULT_BACKUP_DIR, "tasks": []}
    if not isinstance(data, dict):
        return {"backup_dir": DEFAULT_BACKUP_DIR, "tasks": []}
    data.setdefault("backup_dir", DEFAULT_BACKUP_DIR)
    data.setdefault("tasks", [])
    return data


def _save_backup(data: dict):
    """写回备份中心数据文件（原子写 + 写锁）。"""
    with _backup_lock:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = BACKUP_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, BACKUP_FILE)


# ---------------------------------------------------------------------------
# 安全校验工具
# ---------------------------------------------------------------------------
def _is_forbidden(host_view: str) -> bool:
    """判断宿主机视角路径是否位于面板数据目录（data/）内（与 files.py 同基线）。

    data/ 内含 secret.key / users.json 等敏感文件，备份/恢复不得触碰。
    """
    try:
        p = os.path.normcase(os.path.normpath(host_view))
        data = os.path.normcase(DATA_DIR)
        return os.path.commonpath([p, data]) == data
    except ValueError:
        # 跨盘符 / UNC：与本地 data 目录不可能同根，必然不在其内
        return False


def _reject_device_namespace(path: str) -> None:
    """拒绝 Windows 设备命名空间前缀（\\\\?\\ 与 \\\\.\\）。

    此类路径绕过 Win32 路径规范化，且盘符解析差异会使 commonpath 抛
    ValueError，导致 data 目录拦截 fail-open（第六轮审计实测可借此读取
    secret.key），必须在入口直接拒绝。
    """
    if path.startswith("\\\\?\\") or path.startswith("\\\\.\\"):
        raise HTTPException(status_code=400, detail="非法路径（不支持设备命名空间路径）")


def _validate_source_path(path: str) -> str:
    """校验并规范化备份源路径。

    要求：绝对路径、无控制字符、不以 - 开头（防 tar 选项注入）、
    不在面板 data 目录内。

    安全修复（第十一轮审计）：Windows 下额外拒绝 `"`——该字符在 Windows
    文件名中本身非法，且会被拼入计划任务的 `powershell -Command "..."`
    外层双引号包裹（见 _build_cron_command），裸 `"` 可提前闭合外层引号，
    使 `&`/`|`/`>` 等 cmd 元字符逃逸为命令分隔符（存储型命令注入）。
    """
    path = (path or "").strip()
    if not path:
        raise HTTPException(status_code=400, detail="备份源路径不能为空")
    _reject_device_namespace(path)
    if "\x00" in path:
        raise HTTPException(status_code=400, detail="备份源路径不能包含非法字符")
    if IS_WINDOWS and '"' in path:
        raise HTTPException(status_code=400, detail="路径包含非法字符 \"（Windows 文件名不允许双引号）")
    if not os.path.isabs(path):
        raise HTTPException(status_code=400, detail="备份源路径必须为绝对路径")
    base = os.path.basename(path.rstrip("/\\"))
    if base.startswith("-"):
        raise HTTPException(status_code=400, detail="备份源路径的文件/目录名不能以 - 开头")
    if _is_forbidden(path):
        raise HTTPException(status_code=403, detail="无权备份面板数据目录")
    return os.path.normpath(path)


def _validate_target_dir(path: str, allow_empty: bool = True) -> str:
    """校验并规范化备份目标目录（可为空，空则使用默认备份目录）。

    要求：绝对路径、无控制字符、不在面板 data 目录内、不能是根目录。

    安全修复（第十一轮审计）：Windows 下额外拒绝 `"`（同
    _validate_source_path —— 防计划任务命令注入；该字符在 Windows
    文件名中本身非法，拒绝不影响任何合法路径）。
    """
    path = (path or "").strip()
    if not path:
        if allow_empty:
            return ""
        raise HTTPException(status_code=400, detail="目标目录不能为空")
    _reject_device_namespace(path)
    if "\x00" in path:
        raise HTTPException(status_code=400, detail="目标目录不能包含非法字符")
    if IS_WINDOWS and '"' in path:
        raise HTTPException(status_code=400, detail="路径包含非法字符 \"（Windows 文件名不允许双引号）")
    if not os.path.isabs(path):
        raise HTTPException(status_code=400, detail="目标目录必须为绝对路径")
    norm = os.path.normpath(path)
    if norm in (os.sep, os.path.normpath("/"), os.path.normpath("\\")):
        raise HTTPException(status_code=400, detail="目标目录不能是根目录")
    if _is_forbidden(norm):
        raise HTTPException(status_code=403, detail="无权将面板数据目录作为备份目标")
    return norm


def _sanitize_name(name: str) -> str:
    """把任务名/路径转成安全的备份文件前缀（白名单字符）。"""
    base = os.path.basename((name or "").rstrip("/\\")) or "backup"
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", base)
    return safe or "backup"


def _ps_quote(s: str) -> str:
    """PowerShell 单引号字符串转义：内部单引号双写。"""
    return "'" + s.replace("'", "''") + "'"


def _find_task(tasks: list, task_id: str) -> dict:
    """按 id 查找任务，不存在则抛 404。"""
    task = next((t for t in tasks if t.get("id") == task_id), None)
    if not task:
        raise HTTPException(status_code=404, detail="备份任务不存在")
    return task


# ---------------------------------------------------------------------------
# 备份执行
# ---------------------------------------------------------------------------
def _backup_dir(data: dict) -> str:
    """返回当前生效的备份目录（任务未单独指定时使用）。"""
    return (data.get("backup_dir") or "").strip() or DEFAULT_BACKUP_DIR


def _task_target(data: dict, task: dict) -> str:
    """返回任务的最终备份目标目录（宿主机视角）。"""
    return (task.get("target") or "").strip() or _backup_dir(data)


# ---------------------------------------------------------------------------
# 远程备份（WebDAV）
# ---------------------------------------------------------------------------
def _find_remote(data: dict, remote_id: str) -> Optional[dict]:
    """按 id 查找远程配置，不存在返回 None。"""
    if not remote_id:
        return None
    return next((r for r in data.get("remotes", []) if r.get("id") == remote_id), None)


def _remote_auth_header(remote: dict) -> dict:
    """构造 WebDAV Basic 认证头（未配置账号时返回空）。"""
    username = (remote.get("username") or "").strip()
    password = (remote.get("password") or "").strip()
    if not username and not password:
        return {}
    import base64

    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": "Basic " + token}


def _webdav_url(remote: dict, path: str = "") -> str:
    """拼接 WebDAV URL：base 去尾斜杠 + 相对路径。

    仅允许 http/https scheme，且目标主机不得为回环 / 链路本地（含云
    metadata 169.254.169.254）/ 保留地址；内网存储（RFC1918/ULA）允许，
    但受保护地址始终拒绝（SSRF 防护，与 appstore 同基线）。
    """
    base = (remote.get("base") or "").strip().rstrip("/")
    if not base.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="WebDAV 地址必须为 http/https URL")
    try:
        from app.ssrf_guard import assert_safe_http_url
        assert_safe_http_url(base, allow_private=True)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return base + "/" + path.lstrip("/")


def _webdav_request(method: str, remote: dict, path: str, headers_extra: dict = None,
                    timeout: int = 30, **kw):
    """执行 WebDAV HTTP 请求，统一处理认证失败与网络异常。

    SSRF 防护（第八轮审计修复，High）：强制 allow_redirects=False 并拒绝 3xx。
    ssrf_guard 只校验初始 URL，跟随 30x 重定向会直达 169.254.169.254（云
    元数据）或内网服务，构成 SSRF 绕过。
    """
    import requests

    url = _webdav_url(remote, path)
    # DNS rebinding 缓解（第十四轮审计）：校验与实际连接共用同一解析结果
    from app.ssrf_guard import pin_http_url

    url, host_hdr = pin_http_url(url, allow_private=True)
    headers = _remote_auth_header(remote)
    if host_hdr:
        headers.setdefault("Host", host_hdr)
    if headers_extra:
        headers.update(headers_extra)
    try:
        r = requests.request(
            method, url, headers=headers, timeout=timeout,
            allow_redirects=False, **kw,
        )
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"WebDAV 请求失败：{e}")
    if r.status_code in (301, 302, 303, 307, 308):
        raise HTTPException(status_code=400, detail="检测到重定向跳转，已拒绝（SSRF 防护）")
    if r.status_code in (401, 403):
        raise HTTPException(status_code=401, detail="WebDAV 认证失败或无权限")
    return r


def _remote_ensure_dir(remote: dict, path: str) -> None:
    """确保远程目录存在（MKCOL；目录已存在/服务器不支持时忽略）。"""
    if not path:
        return
    import requests

    url = _webdav_url(remote, path)
    # DNS rebinding 缓解（第十四轮审计）
    from app.ssrf_guard import pin_http_url

    url, host_hdr = pin_http_url(url, allow_private=True)
    headers = _remote_auth_header(remote)
    if host_hdr:
        headers.setdefault("Host", host_hdr)
    try:
        r = requests.request("MKCOL", url, headers=headers, timeout=30,
                             allow_redirects=False)
    except requests.RequestException:
        return  # 网络问题静默，由后续 PUT 步骤最终报错
    if r.status_code in (301, 302, 303, 307, 308):
        raise HTTPException(status_code=400, detail="检测到重定向跳转，已拒绝（SSRF 防护）")
    if r.status_code in (401, 403):
        raise HTTPException(status_code=401, detail="WebDAV 认证失败或无权限")
    # 405 = 已存在，201/204 = 创建成功，均视为 OK


def _remote_upload(remote: dict, local_path: str, remote_dir: str, filename: str) -> None:
    """上传本地备份文件到 WebDAV 的 {remote_dir}/{filename}。"""
    import requests

    _remote_ensure_dir(remote, remote_dir)
    url = _webdav_url(remote, f"{remote_dir}/{filename}")
    # DNS rebinding 缓解（第十四轮审计）
    from app.ssrf_guard import pin_http_url

    url, host_hdr = pin_http_url(url, allow_private=True)
    headers = _remote_auth_header(remote)
    if host_hdr:
        headers.setdefault("Host", host_hdr)
    try:
        with open(local_path, "rb") as f:
            r = requests.request("PUT", url, headers=headers,
                                 data=f, timeout=600, allow_redirects=False)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"读取本地备份文件失败：{e}")
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"WebDAV 上传失败：{e}")
    if r.status_code in (301, 302, 303, 307, 308):
        raise HTTPException(status_code=400, detail="检测到重定向跳转，已拒绝（SSRF 防护）")
    if r.status_code in (401, 403):
        raise HTTPException(status_code=401, detail="WebDAV 认证失败或无权限")
    if r.status_code not in (200, 201, 204):
        raise HTTPException(status_code=502, detail=f"WebDAV 上传失败：HTTP {r.status_code}")


def _test_remote(remote: dict) -> None:
    """测试 WebDAV 连接：PROPFIND 根目录（Depth 0）。"""
    import requests

    body = ('<?xml version="1.0"?><d:propfind xmlns:d="DAV:"><d:prop>'
            '<d:resourcetype/></d:prop></d:propfind>')
    r = _webdav_request(
        "PROPFIND", remote, "",
        headers_extra={"Depth": "0", "Content-Type": "application/xml"},
        data=body, timeout=30,
    )
    if r.status_code >= 400:
        raise HTTPException(status_code=502, detail=f"WebDAV 连接失败：HTTP {r.status_code}")


def _do_backup_sync(data: dict, task: dict) -> dict:
    """同步执行一次备份（tarfile 打包），返回结果摘要。

    打包内容：源路径（含顶层目录名），产物文件名 {safe}_{yyyyMMdd_HHmmss}.tar.gz，
    与 cron 计划备份的 shell 命令产物完全一致，可互相识别与轮转。
    """
    source = task["source"]
    target = _task_target(data, task)
    safe = task["safe"]
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = f"{safe}_{ts}.tar.gz"

    # 宿主机视角 -> 容器内实际路径
    real_source = host_path(source)
    real_target = host_path(target)
    if not os.path.exists(real_source):
        raise HTTPException(status_code=404, detail=f"备份源路径不存在：{source}")
    try:
        os.makedirs(real_target, exist_ok=True)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"无法创建备份目录：{e}")

    dest = os.path.join(real_target, fname)
    # arcname 只取顶层名，保证解压后带目录结构（与 cron tar 行为一致）
    arcname = os.path.basename(source.rstrip("/\\")) or "backup"
    start = time.time()
    try:
        with tarfile.open(dest, "w:gz", format=tarfile.PAX_FORMAT) as tf:
            tf.add(real_source, arcname=arcname, recursive=True)
    except tarfile.TarError as e:
        logger.error("备份 %s -> %s 失败: %s", source, dest, e)
        raise HTTPException(status_code=500, detail=f"备份失败：{e}")
    except OSError as e:
        logger.error("备份 %s -> %s 失败: %s", source, dest, e)
        raise HTTPException(status_code=500, detail=f"备份失败：{e}")

    size = os.path.getsize(dest) if os.path.exists(dest) else 0
    elapsed = round(time.time() - start, 2)
    logger.info("备份完成：%s -> %s（%.2f 秒，%.2f MB）", source, dest, elapsed, size / 1048576)

    # 轮转清理旧备份
    removed = _rotate_sync(data, task)

    # 远程备份：任务绑定了远程配置则上传备份文件到 WebDAV
    remote_result = {"uploaded": False, "remote_id": "", "remote_name": "", "error": ""}
    remote_id = task.get("remote_id") or ""
    remote = _find_remote(data, remote_id) if remote_id else None
    if remote:
        try:
            _remote_upload(remote, dest, task["safe"], fname)
            remote_result = {
                "uploaded": True,
                "remote_id": remote.get("id", ""),
                "remote_name": remote.get("name", ""),
                "error": "",
            }
            logger.info("远程备份上传成功：%s -> %s/%s", fname, remote.get("name"), task["safe"])
        except HTTPException as e:
            # 上传失败不影响本地备份成功，但要在结果中如实上报
            remote_result = {
                "uploaded": False,
                "remote_id": remote.get("id", ""),
                "remote_name": remote.get("name", ""),
                "error": e.detail,
            }
            logger.warning("远程备份上传失败：%s", e.detail)

    return {
        "ok": True,
        "task_id": task["id"],
        "name": task["name"],
        "file": os.path.join(target, fname),
        "size": size,
        "elapsed_seconds": elapsed,
        "removed": removed,
        "remote": remote_result,
    }


def _rotate_sync(data: dict, task: dict) -> list:
    """执行轮转清理：按「保留份数 / 保留天数」删除该任务超出策略的旧备份。

    返回被删除的备份文件名列表。删除条件（满足其一即删）：
      - 超过 keep_count 份（最新 N 份之外）；
      - 早于 keep_days 天（但始终保留最新一份，避免误删全部）。
    """
    keep_count = int(task.get("keep_count") or 0)
    keep_days = int(task.get("keep_days") or 0)
    if keep_count <= 0 and keep_days <= 0:
        return []

    target = _task_target(data, task)
    safe = task["safe"]
    real_target = host_path(target)
    if not os.path.isdir(real_target):
        return []

    prefix = safe + "_"
    files = []
    try:
        for fn in os.listdir(real_target):
            if fn.startswith(prefix) and fn.endswith(".tar.gz"):
                fp = os.path.join(real_target, fn)
                try:
                    files.append((fp, os.path.getmtime(fp)))
                except OSError:
                    continue
    except OSError:
        return []

    # 最新的在前
    files.sort(key=lambda x: x[1], reverse=True)
    now = time.time()
    removed = []
    for idx, (fp, mtime) in enumerate(files):
        delete = False
        if keep_count > 0 and idx >= keep_count:
            delete = True
        if keep_days > 0 and idx > 0 and (now - mtime) > keep_days * 86400:
            delete = True
        if delete:
            try:
                os.remove(fp)
                removed.append(os.path.basename(fp))
            except OSError as e:
                logger.warning("删除旧备份失败 %s: %s", fp, e)
    if removed:
        logger.info("任务 %s 轮转清理 %s 个旧备份: %s", task["id"], len(removed), removed)
    return removed


# ---------------------------------------------------------------------------
# 计划任务（cron）命令生成
# ---------------------------------------------------------------------------
def _build_cron_command(source: str, target: str, safe: str) -> str:
    """根据平台生成计划备份命令（Linux crontab / Windows 计划任务）。

    安全说明：source/target 会拼入 shell / PowerShell 命令串，必须严格转义
    （Linux 用 shlex.quote，Windows 用 PowerShell 单引号双写），且 basename
    以 "-" 开头已在 _validate_source_path 入口拒绝（tar 选项注入防护）。

    安全修复（第十一轮审计，High）：Windows 分支整体包裹在
    `powershell -Command "..."` 的【双引号】中，_ps_quote 的单引号转义只对
    PowerShell 解析层有效——target/parent/base 中的裸 `"` 会在 cmd.exe
    解析层提前闭合外层引号，使 `&`/`|`/`>` 逃逸为命令分隔符（存储型命令
    注入，写入 data/<tid>_task.bat 后由 schtasks 执行）。此处 fail-closed：
    任何插值含 `"` 一律拒绝（`"` 在 Windows 文件名中本身非法，不影响合法
    路径；入口 _validate_source_path/_validate_target_dir 已先行拦截，
    本断言为纵深防御，防止未来新增调用方绕过入口校验）。
    """
    if IS_WINDOWS:
        for name, val in (("source", source), ("target", target), ("safe", safe)):
            if '"' in (val or ""):
                raise HTTPException(
                    status_code=400,
                    detail=f"计划备份命令构造中止：{name} 含非法字符 \"（命令注入防护）",
                )
    source = source.rstrip("/\\")
    parent, base = os.path.split(source)
    if not parent:
        parent = os.sep
    if not base:
        base = safe
    # 创建目标目录 + tar 打包（与手动备份同名产物，轮转由后端统一执行）
    if IS_WINDOWS:
        return (
            "powershell -NoProfile -Command "
            '"New-Item -ItemType Directory -Force -Path {target} | Out-Null; '
            "$d=Get-Date -Format yyyyMMdd_HHmmss; "
            'tar -czf \\"{target}\\{safe}_$d.tar.gz\\" -C {parent} {base}"'.format(
                target=_ps_quote(target),
                safe=safe,
                parent=_ps_quote(parent),
                base=_ps_quote(base),
            )
        )
    import shlex

    # Linux：tar 打包到目标目录；% 需转义为 \\% 以免被 crontab 解释；
    # base 前置 "--" 终结选项解析（与入口拒绝形成双保险，纵深防御）。
    return (
        f"mkdir -p {shlex.quote(target)} && "
        f"tar -czf {shlex.quote(target)}/{safe}_$(date +\\%Y\\%m\\%d_\\%H\\%M\\%S).tar.gz "
        f"-C {shlex.quote(parent)} -- {shlex.quote(base)}"
    )


async def _sync_cron_task(task: dict, data: dict) -> dict:
    """按任务的 schedule 字段同步其 cron 计划任务。

    - 有 schedule 且 enabled：创建或更新 cron 任务（shell tar 命令）；
    - 无 schedule 或未启用：删除已有 cron 任务。
    返回更新后的 task（附带 cron_task_id）。
    cron 模块的 create/update/delete 均为 async 函数，本函数也必须是 async。
    """
    from app.routers.cron import create_task as cron_create_task
    from app.routers.cron import CreateTask
    from app.routers.cron import update_task as cron_update_task
    from app.routers.cron import UpdateTask as CronUpdateTask
    from app.routers.cron import delete_task as cron_delete_task

    schedule = (task.get("schedule") or "").strip()
    target = _task_target(data, task)
    command = _build_cron_command(task["source"], target, task["safe"])
    name = f"Graw备份-{task['safe']}"

    cron_task_id = task.get("cron_task_id") or ""
    if schedule and task.get("enabled", True):
        if cron_task_id:
            try:
                await cron_update_task(
                    cron_task_id,
                    CronUpdateTask(schedule=schedule, command=command, enabled=True),
                )
            except HTTPException:
                # 原任务已不存在，重新创建
                cron_task_id = ""
            except Exception as e:
                logger.error("更新计划任务 %s 失败: %s", cron_task_id, e)
                cron_task_id = ""
        if not cron_task_id:
            task_obj = await cron_create_task(
                CreateTask(name=name, schedule=schedule, command=command, enabled=True)
            )
            cron_task_id = task_obj.get("id", "")
    else:
        if cron_task_id:
            try:
                await cron_delete_task(cron_task_id)
            except Exception as e:
                logger.warning("删除计划任务 %s 失败: %s", cron_task_id, e)
            cron_task_id = ""
    task["cron_task_id"] = cron_task_id
    return task


# ---------------------------------------------------------------------------
# 记录扫描
# ---------------------------------------------------------------------------
def _scan_records_sync(data: dict, tasks: list) -> list:
    """扫描备份目录，按命名约定识别备份文件并归属到任务，生成记录列表。

    记录按时间倒序返回。无法归属到任务的孤立备份文件也展示（task_id 为空）。
    """
    records = []
    # 收集所有任务的 safe 前缀映射
    safe_to_task = {t["safe"]: t for t in tasks if t.get("safe")}
    seen_dirs = set()

    def scan_dir(target: str):
        real = host_path(target)
        if not os.path.isdir(real) or target in seen_dirs:
            return
        seen_dirs.add(target)
        try:
            for fn in os.listdir(real):
                m = BACKUP_FILE_RE.match(fn)
                if not m:
                    continue
                fp = os.path.join(real, fn)
                try:
                    st = os.stat(fp)
                except OSError:
                    continue
                safe = m.group("safe")
                task = safe_to_task.get(safe)
                try:
                    created = datetime.strptime(m.group("ts"), "%Y%m%d_%H%M%S").isoformat()
                except ValueError:
                    created = datetime.fromtimestamp(st.st_mtime).isoformat()
                records.append(
                    {
                        "id": f"{safe}_{m.group('ts')}",
                        "task_id": task["id"] if task else "",
                        "task_name": task["name"] if task else safe,
                        "file": os.path.join(target, fn),
                        "name": fn,
                        "size": st.st_size,
                        "created_at": created,
                        "mtime": st.st_mtime,
                    }
                )
        except OSError as e:
            logger.warning("扫描备份目录 %s 失败: %s", target, e)

    # 扫描所有任务的目标目录 + 全局默认备份目录
    for t in tasks:
        scan_dir(_task_target(data, t))
    scan_dir(_backup_dir(data))
    # 时间倒序
    records.sort(key=lambda r: r["mtime"], reverse=True)
    return records


# ---------------------------------------------------------------------------
# 恢复
# ---------------------------------------------------------------------------
def _do_restore_sync(task: dict, file_name: str, target: str) -> dict:
    """从备份文件恢复（tarfile 解压）到目标目录。

    防穿越：解压前逐成员校验，拒绝绝对路径与 .. 越界成员，且解压后
    所有文件必须位于目标目录之内（防 Zip Slip / Tar Slip）。
    """
    safe = task["safe"]
    if not BACKUP_FILE_RE.match(file_name) or not file_name.startswith(safe + "_"):
        raise HTTPException(status_code=400, detail="备份文件名非法")
    target = _validate_target_dir(target, allow_empty=False)
    # 校验：恢复目标不能是任务源路径本身（避免解压覆盖自身？），
    # 允许覆盖，由前端确认。
    real_target = host_path(target)
    try:
        os.makedirs(real_target, exist_ok=True)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"无法创建恢复目标目录：{e}")

    # 定位备份文件：任务目标目录 / 默认备份目录
    data = _load_backup()
    real_file = None
    for cand in {_task_target(data, task), _backup_dir(data)}:
        fp = os.path.join(host_path(cand), file_name)
        if os.path.isfile(fp):
            real_file = fp
            break
    if not real_file:
        raise HTTPException(status_code=404, detail="备份文件不存在")

    start = time.time()
    try:
        with tarfile.open(real_file, "r:gz") as tf:
            members = tf.getmembers()
            base_norm = os.path.normcase(os.path.abspath(real_target))
            for m in members:
                # 安全：拒绝符号链接/硬链接成员——归档内的链接成员在 extractall
                # 时会在目标目录外创建指向任意文件的链接（配合后续写入即任意
                # 文件覆盖）。与 panelbackup._sanitize_member 的防护对齐。
                if m.issym() or m.islnk():
                    raise HTTPException(
                        status_code=400,
                        detail=f"备份内包含链接成员（{m.name}），已中止恢复",
                    )
                # 统一正斜杠为当前平台分隔符后做绝对化判断
                name = m.name.replace("/", os.sep)
                if os.path.isabs(name):
                    raise HTTPException(status_code=400, detail="备份内包含绝对路径，已中止恢复")
                # 逐级校验，防 .. 越界。
                # 注意：比较前必须对双方统一 normcase——Windows 文件系统大小写不敏感，
                # 否则 "C:\...\restored\web".startswith("c:\...\restored\") 会因大小写
                # 不匹配而误判为越界（同文件路径判断必须两端一致规范化）。
                joined = os.path.normpath(os.path.join(real_target, name))
                joined_norm = os.path.normcase(joined)
                if joined_norm != base_norm and not joined_norm.startswith(base_norm + os.sep):
                    raise HTTPException(status_code=400, detail="备份内包含越界路径，已中止恢复")
            tf.extractall(real_target, members=members)
    except tarfile.TarError as e:
        logger.error("恢复 %s 失败: %s", file_name, e)
        raise HTTPException(status_code=500, detail=f"恢复失败：{e}")
    except HTTPException:
        raise
    except OSError as e:
        logger.error("恢复 %s 失败: %s", file_name, e)
        raise HTTPException(status_code=500, detail=f"恢复失败：{e}")

    elapsed = round(time.time() - start, 2)
    logger.info("恢复完成：%s -> %s（%.2f 秒）", file_name, target, elapsed)
    return {"ok": True, "file": file_name, "target": target, "elapsed_seconds": elapsed}


# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------
class CreateTaskRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    type: str = Field("dir", pattern=r"^(dir|file)$")
    source: str
    target: Optional[str] = ""
    schedule: Optional[str] = ""
    keep_count: int = Field(10, ge=0, le=10000)
    keep_days: int = Field(0, ge=0, le=36500)
    enabled: Optional[bool] = True
    remote_id: Optional[str] = ""  # 绑定的远程备份目标 id（空=仅本地）


class UpdateTaskRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=64)
    source: Optional[str] = None
    target: Optional[str] = None
    schedule: Optional[str] = None
    keep_count: Optional[int] = Field(None, ge=0, le=10000)
    keep_days: Optional[int] = Field(None, ge=0, le=36500)
    enabled: Optional[bool] = None
    remote_id: Optional[str] = None


class RemoteRequest(BaseModel):
    """远程备份目标（WebDAV）配置。"""
    name: str = Field(..., min_length=1, max_length=64)
    type: str = Field("webdav", pattern=r"^(webdav)$")
    base: str = Field(..., max_length=1024)  # WebDAV 根 URL（http/https）
    username: Optional[str] = Field("", max_length=128)
    password: Optional[str] = Field("", max_length=512)


class RestoreRequest(BaseModel):
    file: str  # 备份文件名（如 xxx_20260820_020000.tar.gz）
    target: str  # 恢复目标目录（绝对路径）


class DeleteRecordRequest(BaseModel):
    file: str


# ---------------------------------------------------------------------------
# API 端点
# ---------------------------------------------------------------------------
@router.get("/status")
async def status():
    """备份中心状态摘要（默认备份目录 + 任务数 + 备份文件数 + 占用空间）。"""
    import asyncio

    data = _load_backup()
    return await asyncio.to_thread(_status_sync, data)


def _status_sync(data: dict) -> dict:
    bdir = _backup_dir(data)
    real = host_path(bdir)
    file_count = 0
    total_size = 0
    if os.path.isdir(real):
        try:
            for fn in os.listdir(real):
                if BACKUP_FILE_RE.match(fn):
                    fp = os.path.join(real, fn)
                    try:
                        total_size += os.path.getsize(fp)
                        file_count += 1
                    except OSError:
                        continue
        except OSError:
            pass
    return {
        "backup_dir": bdir,
        "task_count": len(data.get("tasks", [])),
        "file_count": file_count,
        "total_size": total_size,
    }


@router.get("/tasks")
async def list_tasks():
    """返回备份任务列表（含实时扫描的备份记录数）。"""
    import asyncio

    data = _load_backup()
    tasks = data.get("tasks", [])
    records = await asyncio.to_thread(_scan_records_sync, data, tasks)
    # 为每个任务补充记录数
    counts = {}
    for r in records:
        if r["task_id"]:
            counts[r["task_id"]] = counts.get(r["task_id"], 0) + 1
    for t in tasks:
        t["record_count"] = counts.get(t["id"], 0)
    return {
        "tasks": tasks,
        "backup_dir": _backup_dir(data),
        "remotes": [_mask_remote(r) for r in data.get("remotes", [])],
    }


@router.post("/tasks")
async def create_task(req: CreateTaskRequest):
    """创建备份任务（可带 cron 计划，计划任务同步创建）。"""
    data = _load_backup()
    source = _validate_source_path(req.source)
    target = _validate_target_dir(req.target or "") if req.target else ""
    schedule = (req.schedule or "").strip()

    task = {
        "id": "bk_" + uuid.uuid4().hex[:10],
        "name": (req.name or "").strip() or os.path.basename(source),
        "type": req.type,
        "source": source,
        "target": target,
        "schedule": schedule,
        "keep_count": req.keep_count,
        "keep_days": req.keep_days,
        "enabled": bool(req.enabled if req.enabled is not None else True),
        "remote_id": (req.remote_id or "").strip(),
        "safe": _sanitize_name(source),
        "cron_task_id": "",
        "created_at": datetime.now().isoformat(),
        "last_backup_at": "",
        "last_status": "",
        "last_error": "",
    }
    # 校验远程 id 存在（若有绑定）
    if task["remote_id"] and not _find_remote(data, task["remote_id"]):
        raise HTTPException(status_code=400, detail="绑定的远程备份目标不存在")
    # 同步创建 / 更新 cron 计划任务
    try:
        task = await _sync_cron_task(task, data)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("创建计划任务失败: %s", e)
        raise HTTPException(status_code=500, detail=f"创建计划任务失败：{e}")

    data.setdefault("tasks", []).append(task)
    _save_backup(data)
    logger.info("创建备份任务：%s（source=%s）", task["name"], source)
    return task


@router.put("/tasks/{task_id}")
async def update_task(task_id: str, req: UpdateTaskRequest):
    """更新备份任务（源/计划/轮转策略等），同步刷新 cron 计划任务。"""
    data = _load_backup()
    task = _find_task(data.get("tasks", []), task_id)

    if req.name is not None:
        task["name"] = (req.name or "").strip()
    if req.source is not None:
        task["source"] = _validate_source_path(req.source)
        task["safe"] = _sanitize_name(task["source"])
    if req.target is not None:
        task["target"] = _validate_target_dir(req.target) if req.target else ""
    if req.schedule is not None:
        task["schedule"] = (req.schedule or "").strip()
    if req.keep_count is not None:
        task["keep_count"] = req.keep_count
    if req.keep_days is not None:
        task["keep_days"] = req.keep_days
    if req.enabled is not None:
        task["enabled"] = bool(req.enabled)
    if req.remote_id is not None:
        new_remote = (req.remote_id or "").strip()
        if new_remote and not _find_remote(data, new_remote):
            raise HTTPException(status_code=400, detail="绑定的远程备份目标不存在")
        task["remote_id"] = new_remote

    try:
        task = await _sync_cron_task(task, data)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("更新计划任务失败: %s", e)
        raise HTTPException(status_code=500, detail=f"更新计划任务失败：{e}")

    _save_backup(data)
    logger.info("更新备份任务：%s", task["id"])
    return task


@router.delete("/tasks/{task_id}")
async def delete_task(task_id: str):
    """删除备份任务（不删除已有备份文件；同步删除其 cron 计划任务）。"""
    data = _load_backup()
    tasks = data.get("tasks", [])
    task = _find_task(tasks, task_id)

    if task.get("cron_task_id"):
        try:
            from app.routers.cron import delete_task as cron_delete_task

            await cron_delete_task(task["cron_task_id"])
        except Exception as e:
            logger.warning("删除计划任务 %s 失败: %s", task["cron_task_id"], e)
    data["tasks"] = [t for t in tasks if t.get("id") != task_id]
    _save_backup(data)
    logger.info("删除备份任务：%s", task_id)
    return {"ok": True}


@router.post("/tasks/{task_id}/run")
async def run_task(task_id: str):
    """手动立即备份一次。"""
    import asyncio

    data = _load_backup()
    task = _find_task(data.get("tasks", []), task_id)
    result = await asyncio.to_thread(_do_backup_sync, data, task)
    # 安全修复（第十四轮审计，Medium）：此前持有备份开始时的旧 data 快照，
    # 结束时整体 _save_backup(data) 回写——备份期间（WebDAV 上传可达 30s+）
    # 其他请求对 tasks/remotes 的增删改会被旧快照覆盖回滚（丢失更新，
    # 已删除的凭据/任务"复活"）。改为合并式更新：重读最新数据，只写入
    # 目标任务的状态字段，绝不整体回写旧快照。
    try:
        latest = _load_backup()
        t = _find_task(latest.get("tasks", []), task_id)
        if t is not None:
            t["last_backup_at"] = datetime.now().isoformat()
            t["last_status"] = "ok" if result.get("ok") else "error"
            t["last_error"] = ""
            _save_backup(latest)
    except Exception as e:  # noqa: BLE001
        logger.warning("更新任务最近状态失败: %s", e)
    return result


@router.post("/tasks/{task_id}/restore")
async def restore_task(task_id: str, req: RestoreRequest):
    """从指定备份文件恢复到目标目录。"""
    import asyncio

    data = _load_backup()
    task = _find_task(data.get("tasks", []), task_id)
    return await asyncio.to_thread(_do_restore_sync, task, (req.file or "").strip(), (req.target or "").strip())


@router.get("/records")
async def list_records():
    """备份记录列表（实时扫描，按时间倒序）。"""
    import asyncio

    data = _load_backup()
    records = await asyncio.to_thread(_scan_records_sync, data, data.get("tasks", []))
    return {"records": records}


@router.delete("/records")
async def delete_record(file: str):
    """删除一条备份记录（同时删除磁盘上的备份文件）。

    安全：仅允许删除符合命名约定且位于备份目录内的文件。
    """
    file = (file or "").strip()
    m = BACKUP_FILE_RE.match(file)
    if not m:
        raise HTTPException(status_code=400, detail="备份文件名非法")
    data = _load_backup()
    removed_any = False
    for cand in {_task_target(data, t) for t in data.get("tasks", [])} | {_backup_dir(data)}:
        fp = os.path.join(host_path(cand), file)
        if os.path.isfile(fp):
            try:
                os.remove(fp)
                removed_any = True
                logger.info("删除备份文件：%s", fp)
            except OSError as e:
                raise HTTPException(status_code=500, detail=f"删除备份文件失败：{e}")
    if not removed_any:
        raise HTTPException(status_code=404, detail="备份文件不存在")
    return {"ok": True}


# ---------------------------------------------------------------------------
# 远程备份目标（WebDAV）配置管理
# ---------------------------------------------------------------------------
def _mask_remote(remote: dict) -> dict:
    """脱敏远程配置：不返回明文密码。"""
    return {
        "id": remote.get("id", ""),
        "name": remote.get("name", ""),
        "type": remote.get("type", "webdav"),
        "base": remote.get("base", ""),
        "username": remote.get("username", ""),
        "has_password": bool(remote.get("password")),
    }


@router.get("/remotes")
async def list_remotes():
    """返回远程备份目标列表（密码脱敏）。"""
    data = _load_backup()
    return {"remotes": [_mask_remote(r) for r in data.get("remotes", [])]}


@router.post("/remotes")
async def create_remote(req: RemoteRequest):
    """创建远程备份目标（WebDAV）。"""
    data = _load_backup()
    # base 校验：http/https URL 且非空
    base = (req.base or "").strip().rstrip("/")
    if not base.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="WebDAV 地址必须为 http/https URL")
    remote = {
        "id": "rmt_" + uuid.uuid4().hex[:10],
        "name": (req.name or "").strip(),
        "type": req.type,
        "base": base,
        "username": (req.username or "").strip(),
        "password": req.password or "",
        "created_at": datetime.now().isoformat(),
    }
    data.setdefault("remotes", []).append(remote)
    _save_backup(data)
    logger.info("创建远程备份目标：%s", remote["name"])
    return _mask_remote(remote)


def _url_authority(url: str) -> str:
    """URL 的权威部分（scheme://host:port）：仅用于判断「目标服务器」是否变更。"""
    from urllib.parse import urlparse
    try:
        p = urlparse(url or "")
        if not p.hostname:
            return ""
        port = p.port or (443 if p.scheme == "https" else 80 if p.scheme == "http" else "")
        return f"{p.scheme.lower()}://{p.hostname.lower()}:{port}"
    except Exception:
        return ""


@router.put("/remotes/{remote_id}")
async def update_remote(remote_id: str, req: RemoteRequest):
    """更新远程备份目标（密码留空表示保持原密码）。"""
    data = _load_backup()
    remote = next((r for r in data.get("remotes", []) if r.get("id") == remote_id), None)
    if not remote:
        raise HTTPException(status_code=404, detail="远程备份目标不存在")
    base = (req.base or "").strip().rstrip("/")
    if not base.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="WebDAV 地址必须为 http/https URL")
    # 安全修复（第十四轮审计，Medium）：目标服务器（authority=scheme://host:port）
    # 变化而密码留空时，旧密码会被静默发往新服务器（凭据转发泄露）。
    # 仅路径变化（同服务器不同目录）时留空保持原密码是安全且合理的。
    if _url_authority(base) != _url_authority(remote.get("base") or "") and not req.password:
        raise HTTPException(
            status_code=400,
            detail="服务器地址已变更，请显式提供新密码（密码留空不再沿用旧密码，避免凭据泄露到新服务器）",
        )
    remote["name"] = (req.name or "").strip()
    remote["base"] = base
    remote["username"] = (req.username or "").strip()
    # 密码留空 = 保持原密码（仅当服务器地址未变时允许）
    if req.password:
        remote["password"] = req.password
    _save_backup(data)
    logger.info("更新远程备份目标：%s", remote["name"])
    return _mask_remote(remote)


@router.delete("/remotes/{remote_id}")
async def delete_remote(remote_id: str):
    """删除远程备份目标（已绑定该远程的任务解除绑定）。"""
    data = _load_backup()
    before = len(data.get("remotes", []))
    data["remotes"] = [r for r in data.get("remotes", []) if r.get("id") != remote_id]
    if len(data["remotes"]) == before:
        raise HTTPException(status_code=404, detail="远程备份目标不存在")
    # 解除已绑定该远程的任务
    for t in data.get("tasks", []):
        if t.get("remote_id") == remote_id:
            t["remote_id"] = ""
    _save_backup(data)
    logger.info("删除远程备份目标：%s", remote_id)
    return {"ok": True}


@router.post("/remotes/{remote_id}/test")
async def test_remote(remote_id: str):
    """测试远程备份目标连接（PROPFIND 根目录）。"""
    import asyncio

    data = _load_backup()
    remote = next((r for r in data.get("remotes", []) if r.get("id") == remote_id), None)
    if not remote:
        raise HTTPException(status_code=404, detail="远程备份目标不存在")
    await asyncio.to_thread(_test_remote, remote)
    return {"ok": True, "id": remote_id, "name": remote.get("name", "")}
