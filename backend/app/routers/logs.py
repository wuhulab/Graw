import json
import os
import platform
import subprocess
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.hostfs import host_path

router = APIRouter()

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
LOGS_FILE = os.path.join(DATA_DIR, "logs.json")
IS_WIN = platform.system() == "Windows"
_DATA_DIR_NORM = os.path.normpath(os.path.abspath(DATA_DIR))
# 面板自身日志是预置日志源（位于 data/ 内），是 data 目录唯一放行的文件
_PANEL_LOG_NORM = os.path.normpath(os.path.join(_DATA_DIR_NORM, "panel.log"))


def _safe_log_path(path: str) -> str:
    """日志路径安全校验：规范化为绝对路径并拦截面板数据目录。

    data/ 目录内存放 users.json / secret.key / databases.json 等敏感文件：
    secret.key 泄露即可伪造任意用户 JWT，users.json 被清空会导致面板
    永久锁死（_load_users 返回空表后不会重新播种）。日志接口绝不能触达，
    唯一例外是面板自身日志 panel.log。
    """
    sp = os.path.normpath(os.path.abspath(path))
    if sp == _PANEL_LOG_NORM:
        return sp
    try:
        common = os.path.commonpath([sp, _DATA_DIR_NORM])
    except ValueError:
        # Windows 下跨盘符时 commonpath 抛 ValueError：必然不在 data 目录内
        return sp
    if common == _DATA_DIR_NORM:
        raise HTTPException(status_code=403, detail="无权访问面板数据目录")
    return sp

PREDEFINED = {
    "panel": {"path": os.path.join(DATA_DIR, "panel.log"), "desc": "面板日志"},
}

if IS_WIN:
    PREDEFINED.update(
        {
            "system": {
                "path": "C:\\Windows\\System32\\winevt\\Logs\\System.evtx",
                "desc": "系统事件日志",
            },
        }
    )
else:
    PREDEFINED.update(
        {
            "syslog": {"path": "/var/log/syslog", "desc": "系统日志"},
            "auth": {"path": "/var/log/auth.log", "desc": "认证日志"},
            "nginx_access": {
                "path": "/var/log/nginx/access.log",
                "desc": "Nginx 访问日志",
            },
            "nginx_error": {
                "path": "/var/log/nginx/error.log",
                "desc": "Nginx 错误日志",
            },
            "dmesg": {"path": "/var/log/dmesg", "desc": "内核日志"},
        }
    )


def _load_custom() -> list:
    if not os.path.exists(LOGS_FILE):
        return []
    try:
        with open(LOGS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_custom(items: list):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(LOGS_FILE, "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


class AddLog(BaseModel):
    name: str
    path: str
    desc: Optional[str] = ""


@router.get("/list")
async def list_logs():
    custom = _load_custom()
    items = []
    for key, meta in PREDEFINED.items():
        exists = os.path.isfile(host_path(meta["path"]))
        items.append(
            {
                "id": key,
                "name": meta["desc"],
                "path": meta["path"],
                "exists": exists,
                "builtin": True,
            }
        )
    for c in custom:
        exists = os.path.isfile(host_path(c["path"]))
        items.append(
            {
                "id": c["id"],
                "name": c["name"],
                "path": c["path"],
                "exists": exists,
                "builtin": False,
            }
        )
    return {"logs": items}


@router.get("/read")
async def read_log(path: str = Query(...), tail: int = Query(200, ge=1, le=5000)):
    # 容器模式下映射到 /host 前缀，读取宿主机日志；
    # 先经 _safe_log_path 拦截面板数据目录（防 secret.key/users.json 泄露）
    real = host_path(_safe_log_path(path))
    if not os.path.isfile(real):
        raise HTTPException(status_code=404, detail="Log file not found")
    try:
        # Limit file size first
        size = os.path.getsize(real)
        if size > 50 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Log file too large (>50MB)")
        if IS_WIN and real.endswith(".evtx"):
            return {
                "content": "Windows .evtx files are binary; use Event Viewer directly."
            }
        # Read last N lines using a ring buffer approach for large files
        lines = []
        with open(real, "r", encoding="utf-8", errors="replace") as f:
            if size < 2 * 1024 * 1024:
                all_lines = f.readlines()
                lines = all_lines[-tail:]
            else:
                import collections

                ring = collections.deque(maxlen=tail)
                for line in f:
                    ring.append(line)
                lines = list(ring)
        return {"path": path, "lines": lines, "count": len(lines)}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/add")
async def add_log(req: AddLog):
    custom = _load_custom()
    cid = f"custom_{int(datetime.now().timestamp())}"
    custom.append(
        {"id": cid, "name": req.name, "path": req.path, "desc": req.desc or ""}
    )
    _save_custom(custom)
    return {"ok": True}


class ClearLogRequest(BaseModel):
    path: str


@router.post("/clear")
async def clear_log(req: ClearLogRequest):
    path = req.path
    # 清空（截断）属于破坏性写操作：只允许操作日志列表中已登记的日志文件
    # （预置 + 自定义），且同样禁止面板数据目录，防止任意文件被清空。
    allowed = {meta["path"] for meta in PREDEFINED.values()}
    allowed.update(c.get("path", "") for c in _load_custom())
    if path not in allowed:
        raise HTTPException(status_code=400, detail="仅允许清空日志列表中登记的日志文件")
    real = host_path(_safe_log_path(path))
    if not os.path.isfile(real):
        raise HTTPException(status_code=404, detail="Log file not found")
    try:
        with open(real, "w", encoding="utf-8") as f:
            f.write("")
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
