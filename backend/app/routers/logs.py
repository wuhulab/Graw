import json
import os
import platform
import subprocess
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter()

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
LOGS_FILE = os.path.join(DATA_DIR, "logs.json")
IS_WIN = platform.system() == "Windows"

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
        exists = os.path.isfile(meta["path"])
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
        exists = os.path.isfile(c["path"])
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
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Log file not found")
    try:
        # Limit file size first
        size = os.path.getsize(path)
        if size > 50 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Log file too large (>50MB)")
        if IS_WIN and path.endswith(".evtx"):
            return {
                "content": "Windows .evtx files are binary; use Event Viewer directly."
            }
        # Read last N lines using a ring buffer approach for large files
        lines = []
        with open(path, "r", encoding="utf-8", errors="replace") as f:
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


@router.post("/clear")
async def clear_log(path: str):
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="Log file not found")
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write("")
        return {"ok": True}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
