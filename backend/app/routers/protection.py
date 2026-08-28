# -*- coding: utf-8 -*-
"""
protection.py - Graw 保护机制路由

功能：
  1. 自动扫描 Docker 中「独立数据库容器」以及「内置数据库（如 SQLite）的应用容器」，
     对「没有设置永久数据卷映射」的容器发出警告；对未命中已知镜像的关键字但容器内
     探测到 SQLite 文件的自定义镜像同样告警（docker exec 文件探测），并提供「一键映射」
     （创建命名数据卷并重建容器，保留现有数据）与「暂时忽略」。
  2. 自动扫描宿主机上的数据库数据目录 / SQLite 数据库文件，对「没有配置自动备份」
     的数据库文件发出警告，并提供「加入备份」（自动创建计划任务）与「暂时忽略」。
  3. 「暂时忽略」是临时行为（默认 7 天后重新提醒），用于防止误操作丢失数据。

数据存储：
  backend/data/protection.json ：
    {
      "ignored":      [{"kind": "docker"|"db_file", "key": "...", "name": "...", "ignored_at": "ISO"}],
      "backup_items": [{"path": "...", "task_id": "...", "created_at": "ISO"}]
    }

说明：
  - 复用 docker_api 的引擎发现逻辑（podman / docker SDK / CLI），同时支持
    「本机直跑」与「Docker /host 挂载」两种部署方式（宿主机路径经 hostfs 映射）。
"""
import json
import logging
import os
import platform
import re
import shlex
import shutil
import threading
import uuid
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.hostfs import host_path
from app.routers.docker_api import (
    get_backend,
    _find_podman,
    _podman_json,
    _run,
    _clean_reason,
    _item_name,
    _item_id,
)

logger = logging.getLogger("graw.protection")

router = APIRouter()

# ---------------------------------------------------------------------------
# 常量与全局状态
# ---------------------------------------------------------------------------
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
PROTECTION_FILE = os.path.join(DATA_DIR, "protection.json")
CRON_FILE = os.path.join(DATA_DIR, "cron.json")

IS_WINDOWS = platform.system() == "Windows"

# 「暂时忽略」的有效期（天），到期后重新提醒
IGNORE_DAYS = 7

# 判定数据库容器的镜像关键字
DB_IMAGE_KEYWORDS = [
    "mysql", "mariadb", "postgres", "postgis", "mongo", "redis",
    "clickhouse", "elasticsearch", "cassandra", "neo4j", "couchdb",
    "influxdb", "cockroachdb", "timescaledb", "mssql", "oracle", "dynamodb",
]

# 内置数据库（嵌入式数据库，如 SQLite）应用的镜像关键字。
# 这类镜像并非独立数据库服务，但默认在容器内使用内置数据库（SQLite 等）保存数据，
# 若未做永久数据卷映射，删除/重建容器同样会丢失数据，需要与数据库容器一样告警。
EMBEDDED_DB_IMAGE_KEYWORDS = [
    "sqlite",
    "grafana", "homeassistant", "home-assistant", "jellyfin", "gitea",
    "vaultwarden", "bitwarden", "nextcloud", "matomo", "wallabag",
    "frigate", "photoprism", "paperless", "bookstack", "wikijs",
    "superset", "grocy", "mealie", "firefly", "freshrss", "duplicati",
    "calibre", "zigbee2mqtt", "z2m", "n8n", "ghost", "etherpad",
    "redmine", "mattermost", "miniflux", "hedgedoc",
]

# 数据库镜像 -> 默认数据目录（用于一键映射的挂载点）
DB_DATA_DIRS = [
    ("mongo", "/data/db"),
    ("mysql", "/var/lib/mysql"),
    ("mariadb", "/var/lib/mysql"),
    ("postgres", "/var/lib/postgresql/data"),
    ("postgis", "/var/lib/postgresql/data"),
    ("timescaledb", "/var/lib/postgresql/data"),
    ("clickhouse", "/var/lib/clickhouse"),
    ("elasticsearch", "/usr/share/elasticsearch/data"),
    ("cassandra", "/var/lib/cassandra"),
    ("redis", "/data"),
    ("neo4j", "/data"),
    ("couchdb", "/opt/couchdb/data"),
    ("influxdb", "/var/lib/influxdb"),
    ("cockroachdb", "/cockroach/cockroach-data"),
    ("mssql", "/var/opt/mssql"),
]

# 内置数据库镜像 -> 容器内默认数据目录（用于一键映射的挂载点）
EMBEDDED_DB_DATA_DIRS = [
    ("grafana", "/var/lib/grafana"),
    ("homeassistant", "/config"),
    ("home-assistant", "/config"),
    ("jellyfin", "/config"),
    ("gitea", "/data"),
    ("vaultwarden", "/data"),
    ("bitwarden", "/data"),
    ("nextcloud", "/var/www/html/data"),
    ("matomo", "/var/www/html"),
    ("wallabag", "/var/www/wallabag/data"),
    ("frigate", "/media/frigate"),
    ("photoprism", "/photoprism/originals"),
    ("paperless", "/usr/src/paperless/data"),
    ("bookstack", "/var/www/bookstack"),
    ("wikijs", "/wiki/data"),
    ("superset", "/app/superset_home"),
    ("grocy", "/var/www/html/data"),
    ("mealie", "/app/data"),
    ("firefly", "/var/www/html/storage"),
    ("freshrss", "/var/www/FreshRSS/data"),
    ("duplicati", "/config"),
    ("calibre", "/calibre-library"),
    ("zigbee2mqtt", "/app/data"),
    ("z2m", "/app/data"),
    ("n8n", "/home/node/.n8n"),
    ("ghost", "/var/lib/ghost/content"),
    ("etherpad", "/var/lib/etherpad-lite"),
    ("redmine", "/usr/src/redmine"),
    ("mattermost", "/mattermost/data"),
    ("miniflux", "/var/lib/miniflux"),
    ("hedgedoc", "/hedgedoc/data"),
]

# 常见数据库数据目录（Linux 部署：宿主机视角）
KNOWN_DB_DIRS_LINUX = [
    "/var/lib/mysql",
    "/var/lib/mariadb",
    "/var/lib/postgresql",
    "/var/lib/redis",
    "/var/lib/mongodb",
    "/var/lib/mongo",
    "/var/lib/clickhouse",
    "/usr/share/elasticsearch/data",
]


def _known_db_dirs() -> list:
    """返回待检查的数据库数据目录（按平台自适应）。

    Linux 部署扫描系统数据库数据目录；Windows 开发机则补充常见数据盘目录，
    便于本地开发时也能真实扫描到数据库文件。
    """
    dirs = list(KNOWN_DB_DIRS_LINUX)
    if IS_WINDOWS:
        for p in (r"C:\data", r"D:\data", r"E:\data", r"C:\GrawData"):
            if os.path.isdir(p):
                dirs.append(p)
    return dirs


# SQLite 数据库文件扫描根目录（Linux 部署：宿主机视角）
SQLITE_ROOTS_LINUX = ["/root", "/home", "/srv", "/var/www", "/opt", "/data", "/srv/www"]


def _sqlite_roots() -> list:
    """返回 SQLite 扫描根目录（按平台自适应）。

    Windows 开发机扫描用户主目录与常见数据盘目录，跳过系统/缓存目录以控制开销。
    """
    if not IS_WINDOWS:
        return list(SQLITE_ROOTS_LINUX)
    roots = [os.path.expanduser("~")]
    for p in (r"C:\data", r"D:\data", r"E:\data", r"C:\GrawData"):
        if os.path.isdir(p):
            roots.append(p)
    return roots


SQLITE_EXTS = (".db", ".sqlite", ".sqlite3", ".db3", ".sqlitedb")
SQLITE_SKIP_DIRS = {
    "node_modules", ".git", ".cache", "lost+found", "venv", ".venv",
    "__pycache__", "proc", "sys", "dev", ".gradle", ".m2", ".npm",
    # Windows 开发机需要跳过的系统 / 缓存目录
    "appdata", "application data", "windows", "program files", "programdata",
    "recovery", "system volume information", "$recycle.bin", "perflogs",
    ".trae-cn", ".vscode", ".pycharm", ".idea",
}
SQLITE_MAX_DEPTH = 5
SQLITE_MAX_ITEMS = 300  # 最多收集的数据库文件数，避免扫描耗时过长

# 默认自动备份计划（分 时 日 月 周 = 每天 02:30）
DEFAULT_BACKUP_SCHEDULE = "30 2 * * *"
# 备份保留天数（清理超过该天数的旧备份）
BACKUP_KEEP_DAYS = 30

# 保护数据写锁（防止并发读写 JSON）
_protection_lock = threading.Lock()


# ---------------------------------------------------------------------------
# 保护数据存储
# ---------------------------------------------------------------------------
def _load_protection() -> dict:
    """读取保护机制数据文件，损坏时返回空结构。"""
    if not os.path.exists(PROTECTION_FILE):
        return {"ignored": [], "backup_items": []}
    try:
        with open(PROTECTION_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning("读取 protection.json 失败，按空数据处理: %s", e)
        return {"ignored": [], "backup_items": []}
    if not isinstance(data, dict):
        return {"ignored": [], "backup_items": []}
    data.setdefault("ignored", [])
    data.setdefault("backup_items", [])
    return data


def _save_protection(data: dict):
    """写回保护机制数据文件（原子写）。"""
    with _protection_lock:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = PROTECTION_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, PROTECTION_FILE)


def _load_cron_tasks() -> list:
    """读取已配置的计划任务，用于判断某数据库文件是否已被备份覆盖。"""
    if not os.path.exists(CRON_FILE):
        return []
    try:
        with open(CRON_FILE, "r", encoding="utf-8") as f:
            tasks = json.load(f)
        return tasks if isinstance(tasks, list) else []
    except Exception:
        return []


# ---------------------------------------------------------------------------
# 忽略管理
# ---------------------------------------------------------------------------
def _is_ignored(kind: str, key: str) -> bool:
    """判断某项是否处于忽略状态（永久忽略或 7 天临时忽略期内）。"""
    data = _load_protection()
    now = datetime.now()
    for item in data.get("ignored", []):
        if item.get("kind") != kind or item.get("key") != key:
            continue
        # 永久忽略：直接生效
        if item.get("permanent"):
            return True
        # 临时忽略：7 天内有效
        try:
            ignored_at = datetime.fromisoformat(item["ignored_at"])
        except Exception:
            return True
        if now - ignored_at < timedelta(days=IGNORE_DAYS):
            return True
    return False


def _current_ignored() -> list:
    """返回仍处于有效期内的忽略项（永久忽略无过期时间，临时忽略附带过期时间）。"""
    data = _load_protection()
    now = datetime.now()
    result = []
    for item in data.get("ignored", []):
        # 永久忽略：直接返回，无 expire_at
        if item.get("permanent"):
            result.append({**item, "expire_at": "永久"})
            continue
        # 临时忽略：检查是否过期
        try:
            ignored_at = datetime.fromisoformat(item["ignored_at"])
        except Exception:
            ignored_at = now
        expire_at = ignored_at + timedelta(days=IGNORE_DAYS)
        if now < expire_at:
            result.append({**item, "expire_at": expire_at.isoformat()})
    return result


# ---------------------------------------------------------------------------
# Docker 扫描与一键映射
# ---------------------------------------------------------------------------
def _is_db_image(image: str) -> bool:
    """通过镜像名关键字判断是否为独立数据库容器。"""
    low = (image or "").lower()
    return any(k in low for k in DB_IMAGE_KEYWORDS)


def _is_embedded_db_image(image: str) -> bool:
    """通过镜像名关键字判断是否内置数据库（如 SQLite）的应用容器。

    这类容器不是独立数据库，但默认在容器内使用内置数据库保存数据，
    未做永久数据卷映射时同样有数据丢失风险，因此也要参与持久化扫描。
    """
    low = (image or "").lower()
    return any(k in low for k in EMBEDDED_DB_IMAGE_KEYWORDS)


def _db_data_dir(image: str) -> str:
    """根据镜像名返回建议的数据挂载目录。"""
    low = (image or "").lower()
    for k, d in DB_DATA_DIRS:
        if k in low:
            return d
    for k, d in EMBEDDED_DB_DATA_DIRS:
        if k in low:
            return d
    return "/data"


def _looks_named(volume_name: str) -> bool:
    """粗略区分「命名卷」与「匿名卷」：匿名卷名通常为长十六进制哈希。"""
    if not volume_name:
        return False
    if len(volume_name) >= 60 and all(ch in "0123456789abcdef" for ch in volume_name.lower()):
        return False
    return True


def _sqlite_persisted(sqlite_files: Optional[list], persistent_mounts: list) -> bool:
    """判断 SQLite 文件是否真的落在某个持久挂载（bind/命名卷）目录下。

    仅当至少一个文件命中某个 persistent 挂载的 Destination 前缀时，才视为该数据库
    已持久化（数据安全）。否则即便容器存在其他 bind/命名卷，只要数据库文件实际仍在
    容器可写层（删除/重建即丢），仍应保持危险/警告，避免把非数据库数据的挂载误判为
    「数据库已永久映射」。
    """
    if not sqlite_files or not persistent_mounts:
        return False
    dests = [
        (m.get("Destination") or "").rstrip("/")
        for m in persistent_mounts
        if (m.get("Destination") or "").startswith("/")
    ]
    if not dests:
        return False
    for p in sqlite_files or []:
        p = (p or "").rstrip("/")
        if not p or not p.startswith("/"):
            continue
        for d in dests:
            if p == d or p.startswith(d + "/"):
                return True
    return False


def _evaluate_mounts(cid: str, name: str, image: str, status: str, mounts: list,
                     category: str = "db", sqlite_files: Optional[list] = None) -> Optional[dict]:
    """评估容器的数据持久化情况，返回警告条目；安全时返回 None。

    判定规则：
      - 存在 bind 挂载或命名卷 -> 视为已持久化，不警告；
      - 完全没有持久挂载       -> danger（数据在容器可写层，删除即丢）；
      - 仅有匿名卷             -> warning（可持久但脆弱、难管理）。

    category 用于区分「独立数据库容器」「内置数据库（如 SQLite）容器」与
    「容器内探测到 SQLite 文件」三类，以便给出更准确的告警文案。
    sqlite_files 为容器内探测到的 SQLite 文件路径列表（category="detected" 时使用）。
    """
    embedded = category == "embedded"
    detected = category == "detected"
    persistent = []
    anonymous = []
    for m in mounts or []:
        m_type = m.get("Type") or ""
        if m_type == "bind" and m.get("Source"):
            persistent.append(m)
        elif m_type == "volume":
            vname = m.get("Name") or ""
            if _looks_named(vname):
                persistent.append(m)
            else:
                anonymous.append(m)
    # 数据库 / 内置数据库容器若已持久化则数据安全，跳过；但「普通容器探测到 sqlite 且已持久化」
    # 仍需列入扫描结果（标记已持久），以便统一盘点容器内数据库。
    if persistent and not detected:
        return None

    if detected:
        # SQLite 已持久必须满足：文件路径确实位于某个持久挂载（bind/命名卷）的
        # Destination 目录下；反之即使容器存在其他持久挂载（如日志/配置目录），
        # 只要数据库文件实际仍在容器可写层，仍按未持久程度告警。
        if _sqlite_persisted(sqlite_files, persistent):
            level = "safe"
        else:
            level = "danger" if not anonymous else "warning"
    else:
        level = "danger" if not anonymous else "warning"

    if detected:
        # 容器内探测命中 SQLite 文件：展示前 3 个路径，便于用户定位
        shown = ", ".join((sqlite_files or [])[:3])
        if level == "safe":
            message = (
                f"检测到容器内存在 SQLite 数据库文件（{shown}），已设置永久数据卷映射，"
                "数据安全；建议在「数据库文件」中将其纳入自动备份以统一管理。"
            )
        elif level == "danger":
            message = (
                f"检测到容器内存在 SQLite 数据库文件（{shown}），但未设置永久数据卷映射，"
                "删除/重建容器将导致数据全部丢失！"
            )
        else:
            message = (
                f"检测到容器内存在 SQLite 数据库文件（{shown}），但仅有匿名数据卷"
                "（容器删除时可能被连带清除），建议改用命名数据卷以便持久化与管理"
            )
    elif embedded:
        message = (
            "容器内置数据库（如 SQLite）未设置永久数据卷映射，数据仅存于容器可写层，"
            "删除/重建容器将导致数据全部丢失！"
            if level == "danger"
            else "容器内置数据库（如 SQLite）仅有匿名数据卷（容器删除时可能被连带清除），"
                 "建议改用命名数据卷以便持久化与管理"
        )
    else:
        message = (
            "容器未设置永久数据卷映射，数据仅存于容器可写层，删除/重建容器将导致数据库数据全部丢失！"
            if level == "danger"
            else "容器仅有匿名数据卷（容器删除时可能被连带清除），建议改用命名数据卷以便持久化与管理"
        )
    return {
        "id": cid,
        "name": name,
        "image": image,
        "status": status,
        "level": level,
        "message": message,
        "data_dir": "" if (detected and level == "safe") else _db_data_dir(image),
        "sqlite_files": (sqlite_files or []) if detected else [],
        "mounts": [
            {
                "type": m.get("Type") or "",
                "destination": m.get("Destination") or "",
                "source": m.get("Source") or m.get("Name") or "",
            }
            for m in mounts or []
        ],
        "has_persistent": bool(persistent),
    }


# 容器内 SQLite 探测命令后缀：排除 /proc /sys /dev；可写层探测额外用 -xdev
# 跳过挂载点/卷，避免把镜像自带的库文件目录与大数据卷误当作应用数据扫描。
SQLITE_FIND_SUFFIX = (
    "\\( -name '*.db' -o -name '*.sqlite' -o -name '*.sqlite3' "
    "-o -name '*.db3' -o -name '*.sqlitedb' \\) "
    "-not -path '/proc/*' -not -path '/sys/*' -not -path '/dev/*' "
)

# 容器可写层探测命令：仅在容器可写层（-xdev 跳过挂载点）查找常见 SQLite 文件，
# 并排除镜像自带库文件目录（/usr/lib、/usr/share、/etc、/var/cache）。
SQLITE_FIND_CMD = (
    "find / -xdev " + SQLITE_FIND_SUFFIX +
    "-not -path '/usr/lib/*' -not -path '/usr/share/*' -not -path '/etc/*' "
    "-not -path '/var/cache/*' 2>/dev/null | head -10"
)


def _run_container_probe(cid: str, cmd: str, kind: str = "cli",
                         client=None, timeout: int = 20) -> Optional[list]:
    """在容器内执行一条探测命令，返回命中的路径列表。

    无法探测（容器无 shell / find / 权限不足）时返回 None，调用方应保守跳过，避免误报。
    """
    try:
        if kind == "cli":
            rc, out, _err = _run_engine(["exec", cid, "sh", "-c", cmd], timeout)
        else:
            if client is None:
                return None
            cont = client.containers.get(cid)
            rc, raw = cont.exec_run(["sh", "-c", cmd], timeout=timeout)
            out = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else (raw or "")
    except Exception as e:
        # 容器不可执行 exec（如 distroless 无 shell）或探测失败：无法判断，保守跳过
        logger.debug("容器 %s 探测失败: %s", cid, e)
        return None
    if rc != 0:
        return None
    return [ln.strip() for ln in (out or "").splitlines() if ln.strip()] or None


def _detect_sqlite_in_bind_mounts(mounts: list) -> Optional[list]:
    """在 bind 挂载的宿主目录上直接扫描 SQLite 文件，返回「容器内路径」列表。

    某些精简镜像（Go / scratch / distroless）容器内没有 find，无法 docker exec
    探测；但 bind 挂载的宿主 Source 目录可直接访问，故改为从宿主侧扫描，
    并把宿主相对路径映射到容器内路径（Destination/相对路径）便于前端展示。
    若为命名卷（无 Source）则跳过，交给容器内探测兜底。
    """
    found = []
    for m in mounts or []:
        if m.get("Type") != "bind" or not m.get("Source") or not m.get("Destination"):
            continue
        real = host_path(m.get("Source"))
        base_dest = (m.get("Destination") or "").rstrip("/")
        if not os.path.isdir(real):
            continue
        stack = [(real, base_dest, 0)]
        while stack:
            d, cdest, depth = stack.pop()
            if depth > SQLITE_MAX_DEPTH:
                continue
            try:
                entries = list(os.scandir(d))
            except OSError:
                continue
            for e in entries:
                try:
                    if e.is_dir(follow_symlinks=False):
                        if e.name.lower() in SQLITE_SKIP_DIRS or e.name.startswith("."):
                            continue
                        stack.append((e.path, f"{cdest}/{e.name}", depth + 1))
                    elif e.is_file(follow_symlinks=False):
                        if e.name.lower().endswith(SQLITE_EXTS):
                            try:
                                size = e.stat().st_size
                            except OSError:
                                size = 0
                            if size > 0:
                                found.append(f"{cdest}/{e.name}")
                                if len(found) >= SQLITE_MAX_ITEMS:
                                    return found or None
                except OSError:
                    continue
    return found or None


def _detect_sqlite_in_container(cid: str, kind: str = "cli",
                                client=None, persistent_dests: Optional[list] = None,
                                timeout: int = 20) -> Optional[list]:
    """在容器内探测 SQLite 数据库文件（弥补关键字列表无法覆盖的自定义镜像）。

    既探测可写层（-xdev 跳过挂载点），也对已持久映射的数据目录（如 /data）
    单独探测，从而把「普通容器且 sqlite 位于已持久 mount 中」的场景一并纳入，
    返回命中的文件路径列表；无法探测时返回 None。
    """
    found = []
    base = _run_container_probe(cid, SQLITE_FIND_CMD, kind, client, timeout)
    if base:
        found.extend(base)
    for dest in persistent_dests or []:
        if not dest or not isinstance(dest, str) or not dest.startswith("/"):
            continue
        cmd = f"find {shlex.quote(dest)} {SQLITE_FIND_SUFFIX}2>/dev/null | head -10"
        files = _run_container_probe(cid, cmd, kind, client, timeout)
        if files:
            found.extend(files)
    # 去重后返回（保持顺序）
    found = list(dict.fromkeys(found))
    return found or None


def _evaluate_container(cid: str, name: str, image: str, status: str, mounts: list,
                        kind: str = "cli", client=None) -> Optional[dict]:
    """对单个容器做完整持久化评估，返回警告条目或 None。

    分派规则：
      - 命中独立数据库镜像 / 内置数据库（如 SQLite）镜像 -> 数据库告警（已持久化则跳过）；
      - 未命中任何关键字 -> 容器内探测 SQLite 文件（含已持久映射的数据目录），命中即纳入，
        若已持久化则标记为「已持久」（数据安全），否则按缺失持久化程度标记危险/警告。
        这样普通容器内置的 SQLite 也会出现在「永久映射」扫描告警里，便于统一盘点。
    """
    if _is_db_image(image):
        return _evaluate_mounts(cid, name, image, status, mounts, "db")
    if _is_embedded_db_image(image):
        return _evaluate_mounts(cid, name, image, status, mounts, "embedded")
    # 未命中关键字：联合探测可写层/持久挂载目标（容器 exec）与 bind 宿主目录
    dests = [
        m.get("Destination")
        for m in (mounts or [])
        if m.get("Type") in ("bind", "volume") and m.get("Destination")
    ]
    host_files = _detect_sqlite_in_bind_mounts(mounts)
    exec_files = _detect_sqlite_in_container(cid, kind, client, dests)
    files = []
    for lst in (host_files, exec_files):
        if lst:
            for p in lst:
                if p not in files:
                    files.append(p)
    if not files:
        return None
    return _evaluate_mounts(cid, name, image, status, mounts, "detected", sqlite_files=files)


def _engine_cli() -> Optional[list]:
    """返回可用的容器引擎 CLI 前缀（podman / docker）；不可用返回 None。"""
    cmd = _find_podman()
    if cmd is not None:
        return cmd
    if shutil.which("docker"):
        return ["docker"]
    return None


def _run_engine(args: list, timeout: int = 60):
    """在容器引擎 CLI 上执行命令，返回 (returncode, stdout, stderr)。"""
    cli = _engine_cli()
    if cli is None:
        raise RuntimeError("容器引擎 CLI（podman/docker）不可用，无法执行该操作")
    return _run(cli + list(args), timeout)


def _scan_docker_sync() -> dict:
    """同步扫描 Docker 数据库容器，返回警告列表。"""
    try:
        kind, client = get_backend()
    except HTTPException as e:
        return {"available": False, "reason": e.detail}

    warnings = []
    try:
        if kind == "cli":
            containers = _podman_json(["ps", "-a", "--format", "json"])
            for c in containers:
                cid = _item_id(c)
                name = _item_name(c)
                image = c.get("Image", "")
                status = c.get("Status", "")
                if not cid:
                    continue
                if _is_ignored("docker", name):
                    continue
                insp = _podman_json(["inspect", cid])
                info = insp[0] if insp else {}
                mounts = info.get("Mounts") or []
                warning = _evaluate_container(cid[:12], name, image, status, mounts, kind, client)
                if warning:
                    warnings.append(warning)
        else:
            for c in client.containers.list(all=True):
                attrs = c.attrs
                image = c.image.tags[0] if c.image.tags else c.image.short_id
                name = c.name
                if _is_ignored("docker", name):
                    continue
                mounts = attrs.get("Mounts") or []
                warning = _evaluate_container(c.short_id, name, image, c.status, mounts, kind, client)
                if warning:
                    warnings.append(warning)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Docker 保护扫描失败: %s", e)
        raise HTTPException(status_code=500, detail=_clean_reason(e))

    logger.info("Docker 保护扫描完成：发现 %s 个风险容器", len(warnings))
    return {"available": True, "warnings": warnings}


def _map_docker_container_sync(name: str) -> dict:
    """一键映射：为数据库容器创建命名数据卷并重建容器（保留现有数据）。

    步骤：
      1. 停止原容器；
      2. 创建命名卷 graw-data-<容器名>-<时间戳>；
      3. 用原镜像 create 一个临时容器挂载该卷，docker cp 把原数据拷入卷中；
      4. 用与原来一致的配置（重启策略/环境变量/端口/网络/主机名）创建新容器；
      5. 删除旧容器、重命名新容器并启动。
    任意步骤失败都会尽量把旧容器重新启动，避免数据与服务不可用。
    """
    cli = _engine_cli()
    if cli is None:
        raise RuntimeError("容器引擎 CLI（podman/docker）不可用，无法执行一键映射")

    # 容器名会拼入引擎 CLI argv 与新卷/临时容器名：拦截以 "-" 开头等
    # 选项注入与非法字符（与 docker_api._safe_docker_ref 同一基线）
    from app.routers.docker_api import _safe_docker_ref

    try:
        name = _safe_docker_ref(name, "容器名")
    except HTTPException as e:
        raise RuntimeError(str(e.detail))

    def cli_run(args: list, timeout: int = 60):
        rc, out, err = _run_engine(args, timeout)
        if rc != 0:
            raise RuntimeError((err or out).strip() or "容器引擎命令执行失败")
        return out

    # 获取容器信息
    rc, out, err = _run_engine(["inspect", name], 30)
    if rc != 0:
        raise RuntimeError((err or out).strip() or "无法获取容器信息")
    try:
        info = json.loads(out)[0]
    except Exception:
        raise RuntimeError("解析容器信息失败")
    if not isinstance(info, dict):
        raise RuntimeError("解析容器信息失败")

    cfg = info.get("Config", {}) or {}
    hcfg = info.get("HostConfig", {}) or {}
    cid = info.get("Id", "")[:12]
    cname = info.get("Name", "").lstrip("/") or cid
    image = cfg.get("Image", "")
    if not image or image.startswith("<"):
        raise RuntimeError("容器镜像是悬空镜像，无法重建，请先为容器重新打标签")

    data_dir = _db_data_dir(image)
    ts = datetime.now().strftime("%Y%m%d%H%M%S")
    vol = f"graw-data-{cname}-{ts}"
    helper = f"graw-helper-{ts}"
    new_tmp_name = f"{cname}-graw-new-{ts}"

    restart = (hcfg.get("RestartPolicy") or {}).get("Name", "no")
    env = cfg.get("Env") or []
    port_bindings = hcfg.get("PortBindings") or {}
    network_mode = hcfg.get("NetworkMode") or ""
    networks = (info.get("NetworkSettings") or {}).get("Networks") or {}
    network = list(networks.keys())[0] if networks else None
    hostname = cfg.get("Hostname")
    privileged = bool(hcfg.get("Privileged"))

    try:
        # 1. 停止容器，避免数据写入与拷贝冲突
        cli_run(["stop", cid], 120)
        # 2. 创建命名数据卷
        cli_run(["volume", "create", vol], 30)
        try:
            # 3. 临时容器：挂载卷并创建文件系统，用于接收原数据
            cli_run(["create", "--name", helper, "-v", f"{vol}:{data_dir}", image], 60)
            try:
                cli_run(["cp", f"{cid}:{data_dir}/.", f"{helper}:{data_dir}/"], 600)
            finally:
                try:
                    cli_run(["rm", "-f", helper], 30)
                except Exception:
                    logger.warning("清理临时容器 %s 失败", helper)
        except Exception:
            # 拷贝失败则删除新卷，避免残留空卷
            try:
                cli_run(["volume", "rm", "-f", vol], 30)
            except Exception:
                pass
            raise

        # 4. 组装新容器创建参数（尽量还原原配置）
        create_args = [
            "create", "--name", new_tmp_name,
            "--label", "graw.migrated=1",
        ]
        if restart and restart != "no":
            create_args += ["--restart", restart]
        if privileged:
            create_args += ["--privileged"]
        for e in env:
            create_args += ["--env", e]
        if hostname:
            create_args += ["--hostname", hostname]

        if network_mode in ("host", "none"):
            create_args += ["--network", network_mode]
        elif network_mode.startswith("container:"):
            create_args += ["--network", network_mode]
        else:
            if network:
                create_args += ["--network", network]
            for cport, bindings in port_bindings.items():
                for b in bindings or []:
                    hp = b.get("HostPort", "")
                    hi = b.get("HostIp", "")
                    flag = f"{hp}:{cport}" if hp else cport
                    if hi:
                        flag = f"{hi}:{flag}"
                    create_args += ["-p", flag]

        create_args += ["-v", f"{vol}:{data_dir}", image]
        cli_run(create_args, 60)

        # 5. 删除旧容器、重命名新容器并启动
        cli_run(["rm", cid], 120)
        cli_run(["rename", new_tmp_name, cname], 30)
        cli_run(["start", cname], 120)
    except Exception as e:
        # 回滚：尽力把原容器重新启动，避免服务不可用
        try:
            cli_run(["start", cid], 60)
        except Exception:
            pass
        logger.error("一键映射容器 %s 失败: %s", cname, e)
        raise RuntimeError(f"一键映射失败：{e}")

    logger.info("已为容器 %s 完成一键映射（卷：%s，数据目录：%s）", cname, vol, data_dir)
    return {
        "ok": True,
        "container": cname,
        "volume": vol,
        "data_dir": data_dir,
        "image": image,
    }


# ---------------------------------------------------------------------------
# 数据库文件扫描与备份
# ---------------------------------------------------------------------------
def _dir_size(real_path: str, cap_entries: int = 5000) -> Optional[int]:
    """估算目录大小；遍历条目超过上限时返回 None（避免扫描卡顿）。"""
    total = 0
    count = 0
    try:
        for root, dirs, files in os.walk(real_path):
            dirs[:] = [d for d in dirs if d.lower() not in SQLITE_SKIP_DIRS and not d.startswith(".")]
            for fn in files:
                try:
                    total += os.path.getsize(os.path.join(root, fn))
                except OSError:
                    pass
                count += 1
                if count > cap_entries:
                    return None
    except OSError:
        return None
    return total


def _walk_sqlite(real_root: str, sp_root: str, depth: int, items: list):
    """递归收集 SQLite 数据库文件（带深度与跳过目录限制）。"""
    if depth > SQLITE_MAX_DEPTH:
        return
    if len(items) >= SQLITE_MAX_ITEMS:
        return
    try:
        entries = list(os.scandir(real_root))
    except OSError:
        return
    for e in entries:
        try:
            if e.is_dir(follow_symlinks=False):
                if e.name.lower() in SQLITE_SKIP_DIRS or e.name.startswith("."):
                    continue
                if depth + 1 <= SQLITE_MAX_DEPTH:
                    _walk_sqlite(e.path, os.path.join(sp_root, e.name), depth + 1, items)
            elif e.is_file(follow_symlinks=False):
                if e.name.lower().endswith(SQLITE_EXTS):
                    try:
                        size = e.stat().st_size
                    except OSError:
                        size = 0
                    if size > 0:
                        items.append(
                            {
                                "path": os.path.join(sp_root, e.name),
                                "type": "file",
                                "size": size,
                                "covered": _is_covered(os.path.join(sp_root, e.name)),
                            }
                        )
                        if len(items) >= SQLITE_MAX_ITEMS:
                            return
        except OSError:
            continue


def _is_covered(path: str) -> bool:
    """判断某数据库路径是否已被自动备份覆盖（protection 备份清单或计划任务命令）。"""
    p = path.rstrip("/")
    targets = [p]
    for b in _load_protection().get("backup_items", []):
        bp = (b.get("path") or "").rstrip("/")
        if bp and (p == bp or p.startswith(bp + "/")):
            return True
        if bp:
            targets.append(bp)
    commands = " ".join((t.get("command") or "") for t in _load_cron_tasks())
    return any(t and (t in commands) for t in targets)


def _scan_db_files_sync() -> list:
    """扫描宿主机数据库数据目录与 SQLite 文件，返回待评估条目。"""
    items = []

    # 1. 常见数据库数据目录
    for d in _known_db_dirs():
        real = host_path(d)
        if not os.path.isdir(real):
            continue
        try:
            has_data = any(os.scandir(real))
        except OSError:
            has_data = False
        if has_data:
            items.append(
                {
                    "path": d,
                    "type": "dir",
                    "size": _dir_size(real),
                    "covered": _is_covered(d),
                }
            )

    # 2. SQLite 数据库文件
    for root in _sqlite_roots():
        real_root = host_path(root)
        if os.path.isdir(real_root):
            _walk_sqlite(real_root, root, 0, items)

    # 过滤已忽略项
    items = [it for it in items if not _is_ignored("db_file", it["path"])]
    logger.info("数据库文件保护扫描完成：共 %s 个候选条目", len(items))
    return items


def _sanitize_name(path: str) -> str:
    """把路径转成安全的备份文件名前缀。"""
    base = os.path.basename(path.rstrip("/")) or "db"
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", base)
    return safe or "db"


def _backup_dir() -> str:
    """返回自动备份目录（与 _build_backup_command 中的落盘位置保持一致）。

    Windows 备份到 C:\\GrawBackups，Linux 备份到 /data/graw-backups。
    供前端「打开备份目录」跳转到文件管理器使用。
    """
    return r"C:\GrawBackups" if IS_WINDOWS else "/data/graw-backups"


def _ps_quote(s: str) -> str:
    """PowerShell 单引号字符串转义：内部单引号双写。"""
    return "'" + s.replace("'", "''") + "'"


def _build_backup_command(path: str) -> str:
    """根据平台生成自动备份命令（Linux crontab / Windows 计划任务）。

    安全说明：path 的 parent / base 会拼入 shell / PowerShell 命令串，
    必须经过严格转义（Linux 用 shlex.quote，Windows 用 PowerShell 单引号
    双写），否则路径中携带 ``;``、``&&``、``|`` 等字符即可注入额外命令，
    且该命令会被写入 crontab 属于存储型注入（以宿主权限反复执行）。
    """
    safe = _sanitize_name(path)
    path = path.rstrip("/")
    # 校验：必须为绝对路径且不含控制字符（空字节等）
    if not path or not os.path.isabs(path) or "\x00" in path:
        raise HTTPException(status_code=400, detail="备份路径必须为绝对路径且不包含非法字符")
    parent, base = os.path.split(path)
    if not base:
        base = safe
    if not parent:
        parent = os.sep
    # 安全：basename 以 "-" 开头会被 tar 解析为选项而非文件名（argv 选项
    # 注入，如 --checkpoint-action=exec=... 可致存储型 RCE，经 crontab 以
    # 宿主权限反复执行）。shlex.quote / PowerShell 单引号只防 shell 元字符，
    # 不防选项注入，故必须在源头直接拒绝（Windows PowerShell 5.1 调用原生
    # 命令时还会吞掉 "--" 分隔符，不能只依赖分隔符防御）。
    if base.startswith("-"):
        raise HTTPException(status_code=400, detail="备份路径的文件/目录名不能以 - 开头")
    if IS_WINDOWS:
        # Windows：用 PowerShell + tar（Win10 自带 bsdtar）。
        # parent / base 用 PowerShell 单引号字面量包裹（内部单引号双写），
        # 保证其中的特殊字符（; & | $ 等）一律按字面量处理，不被解释执行。
        return (
            "powershell -NoProfile -Command "
            '"New-Item -ItemType Directory -Force -Path C:\\GrawBackups | Out-Null; '
            "$d=Get-Date -Format yyyyMMdd_HHmmss; "
            'tar -czf \\"C:\\GrawBackups\\{safe}_$d.tar.gz\\" -C {parent} {base}"'.format(
                safe=safe, parent=_ps_quote(parent), base=_ps_quote(base)
            )
        )
    import shlex

    # Linux：tar 打包到 /data/graw-backups，并清理超过保留天数的旧备份
    # 注意：crontab 中 % 需转义为 \%；base 前置 "--" 终结选项解析，
    # 与入口处 base.startswith("-") 拒绝形成双保险（纵深防御）。
    return (
        f"mkdir -p /data/graw-backups && "
        f"tar -czf /data/graw-backups/{safe}_$(date +\\%Y\\%m\\%d_\\%H\\%M\\%S).tar.gz "
        f"-C {shlex.quote(parent)} -- {shlex.quote(base)} && "
        f"find /data/graw-backups -name '{safe}_*' -mtime +{BACKUP_KEEP_DAYS} -delete"
    )


# ---------------------------------------------------------------------------
# API 端点
# ---------------------------------------------------------------------------
class IgnoreRequest(BaseModel):
    kind: str  # docker | db_file
    key: str
    name: Optional[str] = ""
    permanent: Optional[bool] = False  # 永久忽略：永不提醒


class UnignoreRequest(BaseModel):
    kind: str
    key: str


class BackupRequest(BaseModel):
    path: str
    schedule: Optional[str] = None


class BatchBackupRequest(BaseModel):
    """批量加入备份请求。"""
    paths: List[str]


@router.get("/status")
async def status():
    """保护机制状态摘要。"""
    try:
        get_backend()
        docker_available = True
    except Exception:
        docker_available = False
    data = _load_protection()
    return {
        "docker_available": docker_available,
        "ignored_count": len(_current_ignored()),
        "backup_count": len(data.get("backup_items", [])),
        "backup_dir": _backup_dir(),
    }


@router.get("/docker")
async def scan_docker():
    """扫描 Docker 数据库容器，返回未设置永久映射的警告列表。"""
    import asyncio

    return await asyncio.to_thread(_scan_docker_sync)


@router.post("/docker/{name}/map")
async def map_docker_container(name: str):
    """一键映射：为指定数据库容器创建命名数据卷并重建（保留数据）。"""
    import asyncio

    try:
        return await asyncio.to_thread(_map_docker_container_sync, name)
    except RuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/db-files")
async def scan_db_files():
    """扫描数据库文件，返回条目（前端仅展示未覆盖备份的警告项）。"""
    import asyncio

    return await asyncio.to_thread(_scan_db_files_sync)


@router.post("/db-files/backup")
async def add_db_backup(req: BackupRequest):
    """把数据库文件/目录加入自动备份：记录到清单并创建计划任务。"""
    import asyncio

    path = (req.path or "").strip()
    if not path:
        raise HTTPException(status_code=400, detail="路径不能为空")

    data = _load_protection()
    for b in data.get("backup_items", []):
        if b.get("path") == path:
            return {"ok": True, "already": True, "path": path}

    schedule = (req.schedule or "").strip() or DEFAULT_BACKUP_SCHEDULE
    safe = _sanitize_name(path)
    name = f"Graw备份-{safe}"

    # 复用 cron 模块创建计划任务（写入宿主 crontab / Windows 计划任务）
    try:
        from app.routers.cron import create_task as cron_create_task
        from app.routers.cron import CreateTask

        task = await cron_create_task(
            CreateTask(
                name=name,
                schedule=schedule,
                command=_build_backup_command(path),
                enabled=True,
            )
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error("创建自动备份计划失败 %s: %s", path, e)
        raise HTTPException(status_code=500, detail=f"创建自动备份计划失败：{e}")

    data.setdefault("backup_items", []).append(
        {
            "path": path,
            "task_id": task.get("id", ""),
            "task_name": name,
            "schedule": schedule,
            "created_at": datetime.now().isoformat(),
        }
    )
    _save_protection(data)
    logger.info("已将数据库路径加入自动备份：%s（任务：%s）", path, name)
    return {"ok": True, "already": False, "path": path, "task": task}


@router.post("/db-files/unbackup")
async def remove_db_backup(req: BackupRequest):
    """把数据库路径移出自动备份清单（不删除已创建的计划任务）。"""
    data = _load_protection()
    before = len(data.get("backup_items", []))
    data["backup_items"] = [b for b in data.get("backup_items", []) if b.get("path") != req.path]
    if len(data["backup_items"]) != before:
        _save_protection(data)
        logger.info("已将数据库路径移出自动备份：%s", req.path)
    return {"ok": True}


@router.post("/db-files/batch-backup")
async def batch_add_backup(req: BatchBackupRequest):
    """批量把多个数据库路径加入自动备份。"""
    results = []
    for path in req.paths:
        path = (path or "").strip()
        if not path:
            continue
        data = _load_protection()
        already = any(b.get("path") == path for b in data.get("backup_items", []))
        if already:
            results.append({"path": path, "ok": True, "already": True})
            continue
        schedule = DEFAULT_BACKUP_SCHEDULE
        safe = _sanitize_name(path)
        name = f"Graw备份-{safe}"
        try:
            from app.routers.cron import create_task as cron_create_task
            from app.routers.cron import CreateTask

            task = await cron_create_task(
                CreateTask(
                    name=name,
                    schedule=schedule,
                    command=_build_backup_command(path),
                    enabled=True,
                )
            )
            data.setdefault("backup_items", []).append(
                {
                    "path": path,
                    "task_id": task.get("id", ""),
                    "task_name": name,
                    "schedule": schedule,
                    "created_at": datetime.now().isoformat(),
                }
            )
            _save_protection(data)
            results.append({"path": path, "ok": True, "already": False, "task_id": task.get("id", "")})
        except Exception as e:
            logger.error("批量备份中创建 %s 失败: %s", path, e)
            # 安全（code-scanning py/stack-trace-exposure）：错误详情仅记日志
            results.append({"path": path, "ok": False, "error": "加入备份失败"})
    logger.info("批量加入备份完成：%s 项", len(results))
    return {"results": results}


@router.post("/ignore")
async def ignore_item(req: IgnoreRequest):
    """把某项警告标记为忽略（临时 7 天，或永久）。"""
    if req.kind not in ("docker", "db_file"):
        raise HTTPException(status_code=400, detail="kind 必须是 docker 或 db_file")
    key = (req.key or "").strip()
    if not key:
        raise HTTPException(status_code=400, detail="key 不能为空")

    permanent = bool(req.permanent)
    data = _load_protection()
    now = datetime.now()
    for item in data.get("ignored", []):
        if item.get("kind") == req.kind and item.get("key") == key:
            # 更新已有忽略项：刷新时间 + 更新永久标志
            item["ignored_at"] = now.isoformat()
            item["permanent"] = permanent
            if req.name:
                item["name"] = req.name
            _save_protection(data)
            logger.warning("用户%s忽略了保护警告：kind=%s key=%s", "永久" if permanent else "暂时", req.kind, key)
            return {"ok": True, "already": True, "permanent": permanent}
    data.setdefault("ignored", []).append(
        {
            "kind": req.kind,
            "key": key,
            "name": req.name or "",
            "ignored_at": now.isoformat(),
            "permanent": permanent,
        }
    )
    _save_protection(data)
    logger.warning("用户%s忽略了保护警告：kind=%s key=%s", "永久" if permanent else "暂时", req.kind, key)
    return {"ok": True, "already": False, "permanent": permanent}


@router.post("/unignore")
async def unignore_item(req: UnignoreRequest):
    """恢复对某项警告的提醒。"""
    data = _load_protection()
    before = len(data.get("ignored", []))
    data["ignored"] = [
        i for i in data.get("ignored", [])
        if not (i.get("kind") == req.kind and i.get("key") == req.key)
    ]
    if len(data["ignored"]) != before:
        _save_protection(data)
        logger.info("已恢复保护提醒：kind=%s key=%s", req.kind, req.key)
    return {"ok": True}


@router.get("/ignored")
async def list_ignored():
    """返回当前生效的忽略列表（含过期时间）。"""
    return {"ignored": _current_ignored()}


@router.get("/backups")
async def list_backups():
    """返回已加入自动备份的数据库清单。"""
    data = _load_protection()
    return {"backups": data.get("backup_items", [])}
