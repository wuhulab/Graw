# -*- coding: utf-8 -*-
"""
tamper.py - ShunX 网页防篡改保护机制路由

功能：
  1. 定时备份网站目录中受保护的「特定文件」（生成哈希基线 + 文件副本备份）。
  2. 周期性监控受保护文件：检测到被篡改（哈希不一致 / 文件被删除）时，
     自动从备份副本恢复；对命中 ignore_patterns 的「生产环境正常变动的文件」
     （日志 / 缓存 / 上传 / 会话等）一律跳过，绝不改动。
  3. 篡改发生时，通过 WebSocket 向所有在线面板用户实时推送告警弹窗：
       - 快捷按钮「10 分钟内关闭防篡改」：临时关闭，到期自动恢复监控；
       - 高级按钮「完全关闭防篡改」：彻底关闭，需手动重新开启（弹窗警告）。
  4. 配置持久化在 backend/data/tamper.json，备份快照存放于
     backend/data/tamper_backups/<site_id>/。

安全设计：
  - 受保护文件 / 忽略规则仅允许「相对路径」，拒绝绝对路径、.. 穿越与空字节；
  - 实际读写路径做 realpath + commonpath 包含性校验，防止符号链接逃逸出站点根目录；
  - 不允许把面板数据目录或整个文件系统根目录作为防护范围；
  - 告警按「站点+文件」做冷却去重，避免同一文件反复触发弹窗刷屏；
  - 写接口（配置 / 关闭 / 启用 / 恢复）全部要求管理员权限。
"""

import asyncio
import fnmatch
import hashlib
import json
import logging
import os
import platform
import re
import shutil
import threading
import time
import uuid
from datetime import datetime, timedelta
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from app.auth import (
    get_current_user,
    require_admin,
    require_non_default_password,
    get_current_user_ws_checked,
)
from app.hostfs import host_path
from app.routers.sites import _load_sites as _load_sites_data

logger = logging.getLogger("graw.tamper")

router = APIRouter()

# ---------------------------------------------------------------------------
# 常量与全局状态
# ---------------------------------------------------------------------------
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
TAMPER_FILE = os.path.join(DATA_DIR, "tamper.json")
BACKUP_ROOT = os.path.join(DATA_DIR, "tamper_backups")  # 备份快照根目录

IS_WINDOWS = platform.system() == "Windows"

# 默认配置
DEFAULT_BACKUP_INTERVAL = 60    # 定时备份间隔（分钟）
DEFAULT_SCAN_INTERVAL = 15      # 监控扫描间隔（秒）
DEFAULT_DISABLE_MINUTES = 10    # 「10 分钟内关闭防篡改」的默认时长
MONITOR_TICK = 5                # 监控主循环心跳（秒）
HISTORY_LIMIT = 200             # 篡改历史保留条数
ALERT_COOLDOWN_SECONDS = 300    # 同一文件重复告警的最小间隔（秒），防止弹窗刷屏

# 内置默认忽略规则：生产环境中「正常变动」的常见文件类型（日志 / 数据库等），
# 这些文件会被程序运行时持续写入，必须默认排除在监控之外，避免被误判为篡改。
# 默认规则始终叠加在管理员自定义规则之上，即使未显式配置也一律不监控、不改动。
DEFAULT_IGNORE_PATTERNS = [
    "**/*.log",            # 日志文件
    "**/*.db",             # 常见数据库
    "**/*.sqlite",         # SQLite
    "**/*.sqlite3",        # SQLite 3
    "**/*.sqlitedb",       # SQLite
    "**/*.db3",            # SQLite 3
    "**/*.sdb",            # SQL Anywhere / 其他嵌入式数据库
    "**/*.sqlite-wal",     # SQLite WAL 模式预写日志
    "**/*.sqlite-shm",     # SQLite WAL 共享内存文件
    "**/*.wal",            # 数据库预写日志
    "**/*.shm",            # 数据库共享内存文件
    "**/*.tmp",            # 临时文件
    "**/*.swp",            # 编辑器临时交换文件
    "**/*.lock",           # 锁文件
]

# 只读接口：登录 + 非默认密码；写接口：管理员
_READ = [Depends(require_non_default_password)]
_WRITE = [Depends(require_admin)]

# 数据文件写锁（防止并发写坏 JSON）
_file_lock = threading.Lock()
# 内存告警冷却表：key = site_id|file -> 最近告警时间戳
_alert_cooldown: dict = {}
_alert_lock = threading.Lock()


def _default_tamper() -> dict:
    """返回默认配置结构。"""
    return {"enabled": True, "disabled_until": None, "sites": [], "history": []}


def _load_tamper() -> dict:
    """读取防篡改配置；文件不存在或损坏时返回默认配置。"""
    if not os.path.exists(TAMPER_FILE):
        return _default_tamper()
    try:
        with open(TAMPER_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return _default_tamper()
        data.setdefault("enabled", True)
        data.setdefault("disabled_until", None)
        data.setdefault("sites", [])
        data.setdefault("history", [])
        return data
    except Exception as e:
        logger.warning("读取 tamper.json 失败，按默认配置处理: %s", e)
        return _default_tamper()


def _save_tamper(data: dict) -> None:
    """原子写入防篡改配置，避免并发写坏文件。"""
    with _file_lock:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = TAMPER_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, TAMPER_FILE)


# ---------------------------------------------------------------------------
# 路径校验与包含性防护
# ---------------------------------------------------------------------------
def _valid_rel(rel: str) -> bool:
    """相对路径 / 模式校验：禁止绝对路径、.. 穿越、空字节与空段。

    返回 False 时调用方应跳过或拒绝该条目。
    """
    raw = (rel or "").strip()
    if not raw or "\x00" in raw:
        return False
    # 绝对路径判定需基于原始字符串：
    #   - Linux：以 / 开头；
    #   - Windows：盘符（C:\）或 / 开头。
    # 若先剥掉首斜杠再判断会误把 /etc/passwd 当成相对路径放过。
    if os.path.isabs(raw) or raw.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:[\\/]", raw):
        return False
    rel = raw.strip("/\\")
    if not rel:
        return False
    parts = rel.replace("\\", "/").split("/")
    if any(p in ("", "..") for p in parts):
        return False
    return True


def _validate_root(root: str) -> str:
    """校验网站根目录（宿主机视角绝对路径），非法时抛出 400。

    Windows 加固（第六轮审计修复）：
    - 拒绝 \\?\\ / \\\\.\\ 设备命名空间前缀：其盘符解析差异会使
      commonpath 抛 ValueError 走 fail-open 分支，可借此把面板数据目录
      设为防篡改范围进而读写其中文件；
    - 比较前统一 normcase：否则大小写变体（S:\\GRaw\\...\\DATA）可绕过
      包含性判断。
    """
    root = (root or "").strip()
    if not root or not os.path.isabs(root):
        raise HTTPException(status_code=400, detail="网站根目录必须是绝对路径")
    if root.startswith("\\\\?\\") or root.startswith("\\\\.\\"):
        raise HTTPException(status_code=400, detail="非法的网站根目录（不支持设备命名空间路径）")
    norm = os.path.normpath(root)
    if norm in ("/", "\\"):
        raise HTTPException(status_code=400, detail="不允许将整个文件系统根目录作为防护范围")
    data_norm = os.path.normcase(os.path.normpath(DATA_DIR))
    try:
        if os.path.commonpath([data_norm, os.path.normcase(norm)]) == data_norm:
            raise HTTPException(status_code=400, detail="不允许将面板数据目录作为防护范围")
    except ValueError:
        # 跨盘符 / UNC 路径与本地 data 目录不可能同根
        pass
    return root


def _validate_file_list(items: List[str], label: str) -> List[str]:
    """清洗并校验受保护文件 / 忽略规则列表；非法条目直接拒绝。"""
    result = []
    for it in items or []:
        it = (it or "").strip().strip("/\\")
        if not it:
            continue
        if not _valid_rel(it):
            raise HTTPException(
                status_code=400,
                detail=f"{label}包含非法路径（禁止绝对路径、.. 穿越）：{it}",
            )
        result.append(it)
    return result


def _resolve_site_file(root: str, rel: str) -> Optional[str]:
    """把「站点根目录 + 相对路径」解析为容器内真实路径，并做符号链接逃逸防护。

    校验逻辑：将根目录与目标都 realpath 化（解析符号链接）后，
    目标必须仍位于根目录之内，否则返回 None（视为不可达，跳过处理）。
    """
    if not _valid_rel(rel):
        return None
    root_real = os.path.realpath(host_path(root))
    target = os.path.realpath(os.path.join(root_real, *rel.replace("\\", "/").split("/")))
    try:
        if os.path.commonpath([root_real, target]) != root_real:
            return None
    except ValueError:
        return None
    return target


def _site_backup_dir(site_id: str) -> str:
    """返回某站点的备份快照目录（按安全化后的 site_id 隔离）。"""
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", site_id) or "site"
    return os.path.join(BACKUP_ROOT, safe)


# ---------------------------------------------------------------------------
# 忽略规则匹配（支持 * ? ** 与目录前缀）
# ---------------------------------------------------------------------------
def _pattern_matches(pattern: str, rel_path: str) -> bool:
    """判断相对路径是否命中单个 glob 模式。

    支持 *（单段内任意）、?（单字符）与 **（任意层级目录）。
    模式耗尽时若路径仍有多余层级，仅当最后一段是 ** 才算命中。
    """
    pattern = (pattern or "").strip().strip("/")
    rel = rel_path.strip("/")
    if not pattern:
        return False
    pat_parts = [p for p in pattern.split("/") if p]
    rel_parts = [p for p in rel.split("/") if p]

    def rec(pi: int, ri: int) -> bool:
        if pi == len(pat_parts):
            return ri == len(rel_parts) or (pi > 0 and pat_parts[pi - 1] == "**")
        if ri == len(rel_parts):
            # 路径已耗尽：剩余模式必须全为 **（目录级匹配）
            return all(p == "**" for p in pat_parts[pi:])
        p = pat_parts[pi]
        if p == "**":
            # ** 匹配 0 层或 1 层，交给递归枚举
            return rec(pi + 1, ri) or rec(pi, ri + 1)
        if fnmatch.fnmatch(rel_parts[ri], p):
            return rec(pi + 1, ri + 1)
        return False

    return rec(0, 0)


def _matches_ignore(patterns: List[str], rel: str) -> bool:
    """判断相对路径是否命中任意忽略规则；纯目录名（如 logs）也覆盖其整棵子树。"""
    for p in patterns or []:
        if _pattern_matches(p, rel):
            return True
        if _pattern_matches(p.rstrip("/") + "/**", rel):
            return True
    return False


# ---------------------------------------------------------------------------
# 快照 / 校验 / 恢复
# ---------------------------------------------------------------------------
def _file_hash(path: str, chunk_size: int = 65536) -> str:
    """计算文件 SHA-256（分块读取，避免大文件占用过多内存）。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _snapshot_site(site: dict) -> dict:
    """为站点生成快照：计算受保护文件的哈希基线，并把文件副本复制到备份目录。

    返回新的基线字典：{相对路径: {"hash", "size", "mtime"}}。
    """
    baseline = {}
    root = site.get("root", "")
    backup_dir = _site_backup_dir(site.get("site_id", ""))
    for rel in site.get("protected_files", []):
        if not _valid_rel(rel):
            continue
        real = _resolve_site_file(root, rel)
        if real is None or not os.path.isfile(real):
            continue
        try:
            st = os.stat(real)
            digest = _file_hash(real)
        except OSError as e:
            logger.warning("快照失败 %s: %s", rel, e)
            continue
        baseline[rel] = {"hash": digest, "size": st.st_size, "mtime": st.st_mtime}
        # 复制文件副本（copy2 保留时间与权限，恢复后可直接命中基线）
        dst = os.path.join(backup_dir, *rel.replace("\\", "/").split("/"))
        try:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(real, dst)
        except OSError as e:
            logger.warning("备份副本失败 %s: %s", rel, e)
    return baseline


def _restore_site_file(site: dict, rel: str) -> bool:
    """从备份副本恢复受保护文件；成功返回 True。"""
    if not _valid_rel(rel):
        return False
    parts = rel.replace("\\", "/").split("/")
    src = os.path.join(_site_backup_dir(site.get("site_id", "")), *parts)
    target = _resolve_site_file(site.get("root", ""), rel)
    if target is None or not os.path.isfile(src):
        return False
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        shutil.copy2(src, target)
        logger.warning("已自动恢复被篡改文件: %s/%s", site.get("root", ""), rel)
        return True
    except OSError as e:
        logger.error("恢复文件失败 %s: %s", rel, e)
        return False


def _make_event(site: dict, rel: str, reason: str, restored: bool) -> dict:
    """构造一条篡改事件记录（含唯一 id，供前端去重/关闭）。"""
    return {
        "id": uuid.uuid4().hex[:12],
        "time": datetime.now().isoformat(),
        "site_id": site.get("site_id", ""),
        "site_name": site.get("site_name", site.get("site_id", "")),
        "root": site.get("root", ""),
        "file": rel,
        "action": "restored" if restored else "failed",
        "reason": reason,
        "restored": restored,
    }


def _scan_site(site: dict) -> List[dict]:
    """扫描单个站点的受保护文件，检测篡改并自动恢复，返回事件列表。

    命中忽略规则的文件一律跳过（生产环境正常变动，绝不改动）。
    忽略判断 = 内置默认规则（.log/.db/.sqlite 等）+ 管理员自定义规则。
    """
    events = []
    root = site.get("root", "")
    baseline = site.get("baseline") or {}
    # 内置默认规则始终兜底生效，管理员自定义规则为额外补充
    ignore_patterns = list(DEFAULT_IGNORE_PATTERNS) + list(site.get("ignore_patterns", []))

    for rel in site.get("protected_files", []):
        if not _valid_rel(rel):
            continue
        # 生产环境正常变动的文件（命中忽略规则）→ 跳过，不监控也不改动
        if _matches_ignore(ignore_patterns, rel):
            continue
        entry = baseline.get(rel)
        if not entry:
            # 基线缺失（快照之后新增的受保护文件）→ 暂不处理，等待下次快照纳入
            continue
        real = _resolve_site_file(root, rel)
        if real is None:
            continue
        if not os.path.isfile(real):
            # 受保护文件被删除 → 视为篡改，从备份恢复
            restored = _restore_site_file(site, rel)
            events.append(_make_event(site, rel, "missing", restored))
            continue
        try:
            cur_hash = _file_hash(real)
        except OSError as e:
            logger.warning("读取文件失败 %s: %s", rel, e)
            continue
        if cur_hash == entry.get("hash"):
            continue
        # 哈希与基线不一致 → 被篡改，自动恢复
        restored = _restore_site_file(site, rel)
        events.append(_make_event(site, rel, "hash_mismatch", restored))

    if events:
        site["last_tamper_at"] = datetime.now().isoformat()
    return events


# ---------------------------------------------------------------------------
# 定时与状态判断
# ---------------------------------------------------------------------------
def _backup_due(site: dict, now: datetime) -> bool:
    """判断站点是否到达定时备份时间（首次或超过间隔）。"""
    last = site.get("last_backup_at")
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last)
    except Exception:
        return True
    interval = int(site.get("backup_interval_minutes", DEFAULT_BACKUP_INTERVAL) or DEFAULT_BACKUP_INTERVAL)
    return now - last_dt >= timedelta(minutes=interval)


def _scan_due(site: dict, now: datetime) -> bool:
    """判断站点是否到达监控扫描时间（首次或超过间隔）。"""
    last = site.get("last_scan_at")
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last)
    except Exception:
        return True
    interval = int(site.get("scan_interval_seconds", DEFAULT_SCAN_INTERVAL) or DEFAULT_SCAN_INTERVAL)
    return now - last_dt >= timedelta(seconds=interval)


def _is_temporarily_disabled(data: dict) -> bool:
    """是否处于「临时关闭」状态（10 分钟内关闭，未到期）。"""
    until = data.get("disabled_until")
    if not until:
        return False
    try:
        return datetime.fromisoformat(until) > datetime.now()
    except Exception:
        return False


# ---------------------------------------------------------------------------
# 后台监控
# ---------------------------------------------------------------------------
def _monitor_once():
    """执行一次后台监控（在 worker 线程内运行）。

    返回 (events, status_changed)：events 为待推送的篡改事件，
    status_changed 表示开关状态发生变化（如临时关闭到期自动恢复）。
    注意：先扫描后备份——若先备份可能把已篡改内容固化为新基线。
    """
    data = _load_tamper()
    now = datetime.now()
    events: List[dict] = []
    status_changed = False

    if not data.get("enabled"):
        return events, status_changed

    # 全局临时关闭：未到期则整体跳过；到期后自动恢复监控
    disabled_until = data.get("disabled_until")
    if disabled_until:
        try:
            if datetime.fromisoformat(disabled_until) > now:
                return events, status_changed
        except Exception:
            pass
        data["disabled_until"] = None
        status_changed = True

    dirty = bool(status_changed)
    for site in data.get("sites", []):
        if not site.get("enabled"):
            continue
        try:
            # 1) 扫描：检测篡改并自动回滚（用既有基线比对）
            if _scan_due(site, now):
                evs = _scan_site(site)
                site["last_scan_at"] = now.isoformat()
                dirty = True
                if evs:
                    events.extend(evs)
            # 2) 定时备份：重建基线 + 复制副本（先回滚再快照，保证基线可信）
            if _backup_due(site, now):
                site["baseline"] = _snapshot_site(site)
                site["last_backup_at"] = now.isoformat()
                dirty = True
        except Exception:
            logger.exception("监控站点 %s 失败", site.get("site_id"))

    if events:
        data.setdefault("history", []).extend(events)
        data["history"] = data["history"][-HISTORY_LIMIT:]
        dirty = True
    if dirty:
        _save_tamper(data)
    return events, status_changed


_monitor_task: Optional[asyncio.Task] = None
# 已连接的告警推送 WebSocket 客户端（仅事件循环内访问，无需加锁）
_tamper_clients: set = set()


def _should_alert(ev: dict) -> bool:
    """同一文件在冷却期内不重复推送告警，避免弹窗刷屏。"""
    key = f"{ev.get('site_id')}|{ev.get('file')}"
    now = time.time()
    with _alert_lock:
        last = _alert_cooldown.get(key, 0)
        if now - last < ALERT_COOLDOWN_SECONDS:
            return False
        _alert_cooldown[key] = now
        # 防止键空间无限增长：清理长时间无活动的记录
        if len(_alert_cooldown) > 10000:
            cutoff = now - ALERT_COOLDOWN_SECONDS * 10
            stale = [k for k, v in _alert_cooldown.items() if v < cutoff]
            for k in stale:
                _alert_cooldown.pop(k, None)
    return True


async def _broadcast_alert(ev: dict):
    """向所有在线面板用户推送篡改告警。"""
    dead = []
    payload = {"type": "tamper_alert", "data": ev}
    for ws in list(_tamper_clients):
        try:
            await ws.send_json(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _tamper_clients.discard(ws)


async def _broadcast_status():
    """向所有在线面板用户推送开关状态（临时关闭 / 恢复 / 完全关闭等）。"""
    dead = []
    payload = {"type": "status", "data": _status_payload()}
    for ws in list(_tamper_clients):
        try:
            await ws.send_json(payload)
        except Exception:
            dead.append(ws)
    for ws in dead:
        _tamper_clients.discard(ws)


async def _tamper_monitor():
    """后台监控协程：周期调用 _monitor_once，并推送告警与状态变化。"""
    while True:
        try:
            events, status_changed = await asyncio.to_thread(_monitor_once)
            if status_changed:
                await _broadcast_status()
            for ev in events:
                if _should_alert(ev):
                    await _broadcast_alert(ev)
        except Exception:
            logger.exception("网页防篡改监控循环异常")
        await asyncio.sleep(MONITOR_TICK)


async def start_tamper_monitor():
    """启动后台监控协程（幂等）。"""
    global _monitor_task
    if _monitor_task is None or _monitor_task.done():
        _monitor_task = asyncio.create_task(_tamper_monitor())


async def stop_tamper_monitor():
    """停止后台监控协程。"""
    global _monitor_task
    if _monitor_task is not None:
        _monitor_task.cancel()
        try:
            await _monitor_task
        except asyncio.CancelledError:
            pass
        _monitor_task = None


# ---------------------------------------------------------------------------
# WebSocket：篡改告警实时推送
# ---------------------------------------------------------------------------
@router.websocket("/ws")
async def tamper_ws(websocket: WebSocket, user: Optional[dict] = Depends(get_current_user_ws_checked)):
    """网页防篡改告警推送 WebSocket（?token= 鉴权，登录用户可订阅）。

    安全修复（第十四轮审计，Medium）：此前用 get_current_user_ws（仅校验登录），
    与 HTTP 只读接口（require_non_default_password）口径不一致——默认密码账号
    （未完成强制改密）及低权限账号仍可订阅防篡改状态与篡改事件（含站点根路径、
    受保护文件清单）。改用 get_current_user_ws_checked（登录 + 非默认密码），
    与 HTTP 只读口径对齐，避免经 WS 绕过默认密码拦截获取防护元数据。
    """
    if user is None:
        # get_current_user_ws_checked 内部已在鉴权失败时关闭连接
        return
    await websocket.accept()
    _tamper_clients.add(websocket)
    try:
        # 连入即回放当前状态（开关状态 / 最近事件），前端可据此恢复 UI
        await websocket.send_json({"type": "status", "data": _status_payload()})
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        _tamper_clients.discard(websocket)
        try:
            await websocket.close()
        except Exception:
            # 关闭失败（连接已断开）时忽略
            pass


# ---------------------------------------------------------------------------
# 状态与站点管理接口
# ---------------------------------------------------------------------------
def _status_payload() -> dict:
    """汇总防篡改全局状态（供 REST / WS 复用）。"""
    data = _load_tamper()
    history = data.get("history") or []
    return {
        "enabled": bool(data.get("enabled")),
        "disabled_until": data.get("disabled_until"),
        "temporarily_disabled": _is_temporarily_disabled(data),
        "site_count": len(data.get("sites", [])),
        "enabled_site_count": sum(1 for s in data.get("sites", []) if s.get("enabled")),
        "last_event": history[-1] if history else None,
    }


def _site_summary(site: dict) -> dict:
    """单站点防护配置摘要（用于列表展示与编辑回填）。"""
    root = site.get("root", "")
    return {
        "site_id": site.get("site_id", ""),
        "site_name": site.get("site_name", site.get("site_id", "")),
        "root": root,
        "enabled": bool(site.get("enabled")),
        "protected_files": site.get("protected_files", []),
        "protected_count": len(site.get("protected_files", [])),
        "ignore_patterns": site.get("ignore_patterns", []),
        "ignore_count": len(site.get("ignore_patterns", [])),
        "default_ignore_patterns": list(DEFAULT_IGNORE_PATTERNS),
        "backup_interval_minutes": site.get("backup_interval_minutes", DEFAULT_BACKUP_INTERVAL),
        "scan_interval_seconds": site.get("scan_interval_seconds", DEFAULT_SCAN_INTERVAL),
        "last_backup_at": site.get("last_backup_at"),
        "last_scan_at": site.get("last_scan_at"),
        "last_tamper_at": site.get("last_tamper_at"),
        "root_exists": os.path.isdir(host_path(root)) if root else False,
    }


@router.get("/status", dependencies=_READ)
async def get_status():
    """防篡改全局状态 + 各站点防护摘要。"""
    data = _load_tamper()
    payload = _status_payload()
    payload["sites"] = [_site_summary(s) for s in data.get("sites", [])]
    return payload


@router.get("/sites", dependencies=_READ)
async def list_protections():
    """已配置的防篡改站点列表 + 可防护的网站候选（来自 sites.json）。"""
    data = _load_tamper()
    protections = [_site_summary(s) for s in data.get("sites", [])]
    candidates = [
        {
            "site_id": st.get("id", ""),
            "name": st.get("name", st.get("id", "")),
            "type": st.get("type", "static"),
            "root": st.get("root", ""),
            "enabled": st.get("enabled", False),
        }
        for st in _load_sites_data()
    ]
    return {"protections": protections, "candidates": candidates}


@router.get("/sites/{site_id}", dependencies=_READ)
async def get_protection(site_id: str):
    """单个站点防护配置详情。"""
    data = _load_tamper()
    site = next((s for s in data.get("sites", []) if s.get("site_id") == site_id), None)
    if not site:
        raise HTTPException(status_code=404, detail="未找到该站点的防篡改配置")
    return _site_summary(site)


class CreateProtection(BaseModel):
    site_id: str = Field(..., min_length=1, max_length=64)
    site_name: str = Field("", max_length=128)
    root: str = Field(..., max_length=2048)
    protected_files: List[str] = Field(default_factory=list)
    ignore_patterns: List[str] = Field(default_factory=list)
    backup_interval_minutes: int = Field(DEFAULT_BACKUP_INTERVAL, ge=1, le=10080)
    scan_interval_seconds: int = Field(DEFAULT_SCAN_INTERVAL, ge=5, le=3600)


@router.post("/sites", dependencies=_WRITE)
async def create_protection(req: CreateProtection):
    """为站点启用网页防篡改：立即建立基线快照并复制备份。"""
    data = _load_tamper()
    site_id = (req.site_id or "").strip()
    if not site_id:
        raise HTTPException(status_code=400, detail="site_id 不能为空")
    if any(s.get("site_id") == site_id for s in data.get("sites", [])):
        raise HTTPException(status_code=400, detail="该站点已配置防篡改")

    root = _validate_root(req.root)
    protected = _validate_file_list(req.protected_files, "受保护文件")
    if not protected:
        raise HTTPException(status_code=400, detail="请至少配置一个受保护文件")
    ignores = _validate_file_list(req.ignore_patterns, "忽略规则")

    site = {
        "site_id": site_id,
        "site_name": (req.site_name or "").strip() or site_id,
        "root": root,
        "protected_files": protected,
        "ignore_patterns": ignores,
        "backup_interval_minutes": req.backup_interval_minutes,
        "scan_interval_seconds": req.scan_interval_seconds,
        "enabled": True,
        "baseline": {},
        "created_at": datetime.now().isoformat(),
        "last_backup_at": None,
        "last_scan_at": None,
        "last_tamper_at": None,
    }
    # 首次启用立即建立基线 + 备份（线程中执行文件 IO，避免阻塞事件循环）
    site["baseline"] = await asyncio.to_thread(_snapshot_site, site)
    site["last_backup_at"] = datetime.now().isoformat()
    site["last_scan_at"] = datetime.now().isoformat()
    data.setdefault("sites", []).append(site)
    _save_tamper(data)
    logger.info("已启用网页防篡改：%s（root=%s，受保护文件 %s 个）", site_id, root, len(protected))
    await _broadcast_status()
    return _site_summary(site)


class UpdateProtection(BaseModel):
    site_name: Optional[str] = None
    root: Optional[str] = None
    protected_files: Optional[List[str]] = None
    ignore_patterns: Optional[List[str]] = None
    backup_interval_minutes: Optional[int] = Field(None, ge=1, le=10080)
    scan_interval_seconds: Optional[int] = Field(None, ge=5, le=3600)
    enabled: Optional[bool] = None


@router.put("/sites/{site_id}", dependencies=_WRITE)
async def update_protection(site_id: str, req: UpdateProtection):
    """更新站点防篡改配置；配置变更后立即重建基线（避免沿用旧基线）。"""
    data = _load_tamper()
    site = next((s for s in data.get("sites", []) if s.get("site_id") == site_id), None)
    if not site:
        raise HTTPException(status_code=404, detail="未找到该站点的防篡改配置")

    if req.root is not None:
        site["root"] = _validate_root(req.root)
    if req.site_name is not None:
        site["site_name"] = (req.site_name or "").strip() or site_id
    if req.protected_files is not None:
        protected = _validate_file_list(req.protected_files, "受保护文件")
        if not protected:
            raise HTTPException(status_code=400, detail="请至少配置一个受保护文件")
        site["protected_files"] = protected
    if req.ignore_patterns is not None:
        site["ignore_patterns"] = _validate_file_list(req.ignore_patterns, "忽略规则")
    if req.backup_interval_minutes is not None:
        site["backup_interval_minutes"] = req.backup_interval_minutes
    if req.scan_interval_seconds is not None:
        site["scan_interval_seconds"] = req.scan_interval_seconds
    if req.enabled is not None:
        site["enabled"] = bool(req.enabled)

    # 配置变更后重建基线与备份，确保基线反映最新受保护文件集合
    site["baseline"] = await asyncio.to_thread(_snapshot_site, site)
    site["last_backup_at"] = datetime.now().isoformat()
    site["last_scan_at"] = datetime.now().isoformat()
    _save_tamper(data)
    logger.info("已更新网页防篡改配置：%s", site_id)
    await _broadcast_status()
    return _site_summary(site)


@router.delete("/sites/{site_id}", dependencies=_WRITE)
async def delete_protection(site_id: str):
    """删除站点防篡改配置，并清理其备份快照。"""
    data = _load_tamper()
    before = len(data.get("sites", []))
    data["sites"] = [s for s in data.get("sites", []) if s.get("site_id") != site_id]
    if len(data["sites"]) == before:
        raise HTTPException(status_code=404, detail="未找到该站点的防篡改配置")
    _save_tamper(data)
    bdir = _site_backup_dir(site_id)
    if os.path.isdir(bdir):
        shutil.rmtree(bdir, ignore_errors=True)
    logger.info("已删除网页防篡改配置：%s", site_id)
    await _broadcast_status()
    return {"ok": True}


@router.post("/sites/{site_id}/backup-now", dependencies=_WRITE)
async def backup_now(site_id: str):
    """手动立即备份：重建基线 + 复制备份副本。"""
    data = _load_tamper()
    site = next((s for s in data.get("sites", []) if s.get("site_id") == site_id), None)
    if not site:
        raise HTTPException(status_code=404, detail="未找到该站点的防篡改配置")
    site["baseline"] = await asyncio.to_thread(_snapshot_site, site)
    site["last_backup_at"] = datetime.now().isoformat()
    _save_tamper(data)
    logger.info("手动备份完成：%s", site_id)
    return {"ok": True, "protected_count": len(site.get("protected_files", [])), "last_backup_at": site["last_backup_at"]}


@router.post("/sites/{site_id}/scan-now", dependencies=_WRITE)
async def scan_now(site_id: str):
    """手动立即扫描：检测篡改并自动回滚，返回本次事件并推送告警。"""
    data = _load_tamper()
    site = next((s for s in data.get("sites", []) if s.get("site_id") == site_id), None)
    if not site:
        raise HTTPException(status_code=404, detail="未找到该站点的防篡改配置")
    events = await asyncio.to_thread(_scan_site, site)
    site["last_scan_at"] = datetime.now().isoformat()
    if events:
        data.setdefault("history", []).extend(events)
        data["history"] = data["history"][-HISTORY_LIMIT:]
    _save_tamper(data)
    # 手动触发扫描发现篡改时同样向在线用户推送告警
    for ev in events:
        if _should_alert(ev):
            await _broadcast_alert(ev)
    return {"ok": True, "tampered_count": len(events), "events": events}


class RestoreRequest(BaseModel):
    file: str = Field(..., max_length=1024)


@router.post("/sites/{site_id}/restore", dependencies=_WRITE)
async def restore_file(site_id: str, req: RestoreRequest):
    """手动从备份恢复站点内单个文件。"""
    data = _load_tamper()
    site = next((s for s in data.get("sites", []) if s.get("site_id") == site_id), None)
    if not site:
        raise HTTPException(status_code=404, detail="未找到该站点的防篡改配置")
    file_rel = (req.file or "").strip().strip("/\\")
    if not _valid_rel(file_rel):
        raise HTTPException(status_code=400, detail="非法文件路径")
    ok = await asyncio.to_thread(_restore_site_file, site, file_rel)
    if not ok:
        raise HTTPException(status_code=400, detail="恢复失败：备份不存在或路径无效")
    logger.warning("手动恢复文件：%s/%s", repr(site.get("root")), repr(file_rel))
    return {"ok": True, "file": file_rel}


# ---------------------------------------------------------------------------
# 全局开关：临时关闭 / 完全关闭 / 重新启用
# ---------------------------------------------------------------------------
class DisableRequest(BaseModel):
    minutes: Optional[int] = Field(None, ge=1, le=1440)
    mode: str = Field("temporary", pattern=r"^(temporary|manual)$")


@router.post("/disable", dependencies=_WRITE)
async def disable_protection(req: DisableRequest):
    """关闭防篡改。

    - mode=temporary（默认）：临时关闭 minutes 分钟（前端按钮为 10 分钟），到期自动恢复；
    - mode=manual：完全关闭，必须手动调用 /enable 重新开启。
    """
    data = _load_tamper()
    if req.mode == "manual":
        data["enabled"] = False
        data["disabled_until"] = None
        logger.warning("管理员已完全关闭网页防篡改（需手动重新开启）")
    else:
        minutes = req.minutes or DEFAULT_DISABLE_MINUTES
        data["enabled"] = True
        data["disabled_until"] = (datetime.now() + timedelta(minutes=minutes)).isoformat()
        logger.warning("管理员临时关闭网页防篡改 %s 分钟（到期自动恢复）", repr(minutes))
    _save_tamper(data)
    await _broadcast_status()
    return _status_payload()


@router.post("/enable", dependencies=_WRITE)
async def enable_protection():
    """重新启用网页防篡改（清除临时关闭与完全关闭状态）。"""
    data = _load_tamper()
    data["enabled"] = True
    data["disabled_until"] = None
    _save_tamper(data)
    logger.info("管理员已重新开启网页防篡改")
    await _broadcast_status()
    return _status_payload()


@router.get("/history", dependencies=_READ)
async def get_history(limit: int = 100):
    """篡改历史记录（最新在前）。"""
    data = _load_tamper()
    history = data.get("history") or []
    if limit and limit > 0:
        history = history[-int(limit):]
    return {"history": history[::-1]}
