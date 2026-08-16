# -*- coding: utf-8 -*-
"""
tasks.py - 任务中心

管理长线任务（如应用商店安装）。任务记录持久化到 JSON 文件，
日志按任务写入独立日志文件。任务由后端线程在后台执行，页面刷新 /
客户端断开均不会中断任务，任务状态与日志随时可查。

端点：
    GET    /api/tasks                任务列表（按开始时间倒序）
    GET    /api/tasks/{id}           单个任务详情
    GET    /api/tasks/{id}/log       任务日志（解析后的 lines 数组 + 纯文本）
    DELETE /api/tasks/{id}           删除任务（含日志文件）

数据文件：
    backend/data/tasks.json          任务记录
    backend/data/tasks/<id>.log      任务 JSONL 日志
"""
import json
import logging
import os
import threading
import time
import uuid

from fastapi import APIRouter, HTTPException

logger = logging.getLogger("tasks")
router = APIRouter()

_ROUTERS_DIR = os.path.dirname(os.path.abspath(__file__))
# s:\Graw\backend\app\routers -> s:\Graw\backend\data
DATA_DIR = os.path.join(_ROUTERS_DIR, "..", "..", "data")
TASKS_FILE = os.path.join(DATA_DIR, "tasks.json")
TASKS_DIR = os.path.join(DATA_DIR, "tasks")

# 内存缓存 + 线程锁（安装工作线程与请求线程并发读写）
_lock = threading.Lock()
_tasks: dict = {}  # task_id -> record
_last_mtime: float = 0.0  # 磁盘文件最后读取时的 mtime


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _persist():
    """全量写回任务记录文件（仅在任务状态变化时调用，日志不在此列）。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = TASKS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(list(_tasks.values()), f, ensure_ascii=False, indent=2)
    os.replace(tmp, TASKS_FILE)


def _load():
    global _tasks, _last_mtime
    if not os.path.exists(TASKS_FILE):
        _tasks = {}
        _last_mtime = 0.0
        return
    try:
        # 磁盘文件未变化则跳过，避免每次请求都读盘
        mtime = os.path.getmtime(TASKS_FILE)
        if mtime == _last_mtime and _tasks:
            return
        with open(TASKS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            _tasks = {t["id"]: t for t in data if isinstance(t, dict) and t.get("id")}
            _last_mtime = mtime
    except Exception as e:
        logger.warning("读取任务文件失败: %s", e)


# 模块导入时加载一次
_load()


def list_tasks() -> list:
    with _lock:
        _load()
        return sorted(
            _tasks.values(), key=lambda t: t.get("started_at", ""), reverse=True
        )


def get_task(task_id: str) -> dict:
    with _lock:
        _load()
        return _tasks.get(task_id)


def create_task(record: dict) -> dict:
    """创建并持久化一条任务记录，返回带 id 的完整记录。"""
    task_id = record.get("id") or uuid.uuid4().hex[:8]
    record.setdefault("id", task_id)
    record.setdefault("status", "running")
    record.setdefault("started_at", _now())
    record.setdefault("finished_at", "")
    record.setdefault("result", None)
    record.setdefault("error", "")
    with _lock:
        _tasks[record["id"]] = record
        _persist()
    return record


def update_task(task_id: str, **fields):
    with _lock:
        task = _tasks.get(task_id)
        if not task:
            return
        task.update(fields)
        _persist()


def delete_task(task_id: str):
    with _lock:
        _tasks.pop(task_id, None)
        _persist()
    log_path = os.path.join(TASKS_DIR, f"{task_id}.log")
    try:
        if os.path.exists(log_path):
            os.remove(log_path)
    except Exception as e:
        logger.warning("删除任务日志失败 %s: %s", task_id, e)


def _log_path(task_id: str) -> str:
    return os.path.join(TASKS_DIR, f"{task_id}.log")


def append_log(task_id: str, evt: dict):
    """把安装事件（status/log/error/result）写入任务的 JSONL 日志文件。"""
    etype = evt.get("type")
    if etype == "status":
        line = {"type": "status", "text": evt.get("message", "")}
    elif etype == "log":
        line = {"type": "log", "text": evt.get("line", "")}
    elif etype == "error":
        line = {"type": "error", "text": evt.get("message", "")}
    elif etype == "result":
        line = {"type": "result", "text": json.dumps(evt.get("data", {}), ensure_ascii=False)}
    else:
        return
    if not line["text"]:
        return
    try:
        os.makedirs(TASKS_DIR, exist_ok=True)
        with open(_log_path(task_id), "a", encoding="utf-8") as f:
            f.write(json.dumps(line, ensure_ascii=False) + "\n")
    except Exception as e:
        logger.warning("写入任务日志失败 %s: %s", task_id, e)


def read_log(task_id: str) -> list:
    """解析任务的 JSONL 日志，返回 [{type, text}]，损坏行降级为普通 log 文本。"""
    lines = []
    path = _log_path(task_id)
    if not os.path.exists(path):
        return lines
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    obj = json.loads(raw)
                    lines.append(
                        {"type": obj.get("type", "log"), "text": obj.get("text", "")}
                    )
                except Exception:
                    lines.append({"type": "log", "text": raw})
    except Exception as e:
        logger.warning("读取任务日志失败 %s: %s", task_id, e)
    return lines


@router.get("")
async def list_endpoint():
    return {"tasks": list_tasks()}


@router.get("/{task_id}")
async def get_endpoint(task_id: str):
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


@router.get("/{task_id}/log")
async def log_endpoint(task_id: str):
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    lines = read_log(task_id)
    return {
        "task_id": task_id,
        "lines": lines,
        "text": "\n".join(l["text"] for l in lines),
    }


@router.delete("/{task_id}")
async def delete_endpoint(task_id: str):
    task = get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    delete_task(task_id)
    return {"ok": True}
