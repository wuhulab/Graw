import json
import os
import platform
import re
import subprocess
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter()

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
CRON_FILE = os.path.join(DATA_DIR, "cron.json")
IS_WIN = platform.system() == "Windows"


def _load_tasks() -> list:
    if not os.path.exists(CRON_FILE):
        return []
    try:
        with open(CRON_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_tasks(tasks: list):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(CRON_FILE, "w", encoding="utf-8") as f:
        json.dump(tasks, f, ensure_ascii=False, indent=2)


def _parse_schtasks_xml(xml_text: str) -> list:
    tasks = []
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(xml_text)
        ns = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
        for task in root.findall(".//t:Task", ns):
            name_el = task.find("t:RegistrationInfo/t:URI", ns)
            name = name_el.text if name_el is not None else ""
            cmd_el = task.find("t:Actions/t:Exec/t:Command", ns)
            arg_el = task.find("t:Actions/t:Exec/t:Arguments", ns)
            cmd = cmd_el.text if cmd_el is not None else ""
            args = arg_el.text if arg_el is not None else ""
            trigs = task.findall("t:Triggers/*", ns)
            schedule = ""
            if trigs:
                sched = trigs[0]
                start = sched.find("t:StartBoundary", ns)
                if start is not None:
                    schedule = start.text
            enabled_el = task.find("t:Settings/t:Enabled", ns)
            enabled = enabled_el.text == "true" if enabled_el is not None else True
            tasks.append(
                {
                    "id": name.replace("\\", "_").strip("_"),
                    "name": name.split("\\")[-1],
                    "command": f"{cmd} {args}".strip(),
                    "schedule": schedule,
                    "enabled": enabled,
                }
            )
    except Exception:
        pass
    return tasks


def _list_windows_tasks():
    try:
        r = subprocess.run(
            ["schtasks", "/query", "/xml", "/fo", "list"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if r.returncode != 0:
            # fallback to table
            return []
        # xml output contains multiple XML docs concatenated; split them
        raw = r.stdout
        results = []
        for xml_doc in raw.split("\n<?xml"):
            if xml_doc and not xml_doc.startswith("<?xml"):
                xml_doc = "<?xml" + xml_doc
            results.extend(_parse_schtasks_xml(xml_doc))
        return results
    except Exception:
        return []


def _sync_from_windows():
    # Merge schtasks state into json store
    sys_tasks = _list_windows_tasks()
    stored = _load_tasks()
    stored_map = {s["id"]: s for s in stored}
    for t in sys_tasks:
        if t["id"] in stored_map:
            stored_map[t["id"]]["enabled"] = t["enabled"]
            stored_map[t["id"]]["schedule"] = t["schedule"]
    _save_tasks(stored)


def _create_windows_task(tid: str, name: str, command: str, schedule: str):
    # schedule is in cron-like string; for windows convert to daily at specific time for simplicity
    # Parse cron "minute hour * * *" -> /ST HH:MM
    parts = schedule.split()
    if len(parts) >= 5:
        minute = parts[0]
        hour = parts[1]
        st = f"{hour.zfill(2)}:{minute.zfill(2)}"
    else:
        st = "00:00"
    tmp_bat = os.path.join(DATA_DIR, f"{tid}_task.bat")
    with open(tmp_bat, "w", encoding="utf-8") as f:
        f.write(command)
    r = subprocess.run(
        [
            "schtasks",
            "/create",
            "/tn",
            name,
            "/tr",
            tmp_bat,
            "/sc",
            "daily",
            "/st",
            st,
            "/f",
        ],
        capture_output=True,
        text=True,
        timeout=15,
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr or r.stdout)


def _delete_windows_task(name: str):
    subprocess.run(
        ["schtasks", "/delete", "/tn", name, "/f"],
        capture_output=True,
        text=True,
        timeout=15,
    )


def _run_windows_task(name: str):
    subprocess.run(
        ["schtasks", "/run", "/tn", name], capture_output=True, text=True, timeout=30
    )


def _list_linux_cron():
    try:
        r = subprocess.run(
            ["crontab", "-l"], capture_output=True, text=True, timeout=10
        )
        if r.returncode != 0:
            return []
        lines = r.stdout.splitlines()
        tasks = []
        for line in lines:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r"^((?:\S+\s+){5})(.*)$", line)
            if m:
                schedule = m.group(1).strip()
                command = m.group(2).strip()
                tasks.append({"schedule": schedule, "command": command})
        return tasks
    except Exception:
        return []


def _rewrite_linux_cron(tasks: list):
    lines = []
    for t in tasks:
        lines.append(f"{t['schedule']} {t['command']}")
    text = "# Managed by Graw Panel\n" + "\n".join(lines) + "\n"
    p = subprocess.Popen(["crontab", "-"], stdin=subprocess.PIPE, text=True)
    p.communicate(text)
    if p.returncode != 0:
        raise RuntimeError("crontab update failed")


class CreateTask(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    schedule: str = Field(..., min_length=1)  # Cron format or compatible
    command: str = Field(..., min_length=1)
    enabled: Optional[bool] = True


class UpdateTask(BaseModel):
    schedule: Optional[str] = None
    command: Optional[str] = None
    enabled: Optional[bool] = None


@router.get("/list")
async def list_tasks():
    if IS_WIN:
        _sync_from_windows()
    tasks = _load_tasks()
    return {"tasks": tasks, "platform": "windows" if IS_WIN else "linux"}


@router.post("/create")
async def create_task(req: CreateTask):
    tasks = _load_tasks()
    tid = "task_" + str(uuid.uuid4())[:8]
    task = {
        "id": tid,
        "name": req.name,
        "schedule": req.schedule,
        "command": req.command,
        "enabled": req.enabled if req.enabled is not None else True,
        "created_at": datetime.now().isoformat(),
    }
    if IS_WIN:
        try:
            _create_windows_task(tid, req.name, req.command, req.schedule)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    else:
        crons = _list_linux_cron()
        crons.append({"schedule": req.schedule, "command": req.command})
        try:
            _rewrite_linux_cron(crons)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    tasks.append(task)
    _save_tasks(tasks)
    return task


@router.post("/{task_id}/update")
async def update_task(task_id: str, req: UpdateTask):
    tasks = _load_tasks()
    task = next((t for t in tasks if t["id"] == task_id), None)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if req.schedule is not None:
        task["schedule"] = req.schedule
    if req.command is not None:
        task["command"] = req.command
    if req.enabled is not None:
        task["enabled"] = req.enabled
    _save_tasks(tasks)
    return task


@router.post("/{task_id}/delete")
async def delete_task(task_id: str):
    tasks = _load_tasks()
    task = next((t for t in tasks if t["id"] == task_id), None)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if IS_WIN:
        _delete_windows_task(task["name"])
    else:
        crons = _list_linux_cron()
        crons = [c for c in crons if c["command"] != task["command"]]
        _rewrite_linux_cron(crons)
    tasks = [t for t in tasks if t["id"] != task_id]
    _save_tasks(tasks)
    return {"ok": True}


@router.post("/{task_id}/run")
async def run_task(task_id: str):
    tasks = _load_tasks()
    task = next((t for t in tasks if t["id"] == task_id), None)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    if IS_WIN:
        _run_windows_task(task["name"])
    else:
        subprocess.Popen(task["command"], shell=True)
    return {"ok": True}
