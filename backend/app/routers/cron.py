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

from app.node_manager import host_cmd, host_shell

# Cron 调度表达式白名单：恰好 5 个字段（分 时 日 月 周），
# 每个字段仅允许数字 / * / , / - / / 与字母缩写（mon、jan 等）。
# schedule 最终会拼进 crontab 文件（f"{schedule} {command}"），
# 若允许换行等字符可注入额外的 cron 任务行。
_CRON_FIELD = r"[0-9A-Za-z*,-/]+"
_CRON_SCHEDULE_RE = re.compile(
    rf"^{_CRON_FIELD}(\s+{_CRON_FIELD}){{4}}$"
)


def _validate_schedule(schedule: str) -> None:
    """校验 cron 调度表达式格式，阻止向 crontab 注入额外任务行。"""
    if not schedule or not _CRON_SCHEDULE_RE.match(schedule.strip()):
        raise HTTPException(
            status_code=400,
            detail="调度表达式非法：需为 5 字段 cron 格式（分 时 日 月 周）",
        )

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
        # 在宿主机环境执行 crontab（容器模式经 chroot 映射，读写宿主 crontab）
        r = host_cmd(["crontab", "-l"], capture_output=True, text=True, timeout=10)
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
    r = host_cmd(
        ["crontab", "-"],
        input=text,
        capture_output=True,
        text=True,
        timeout=15,
    )
    if r.returncode != 0:
        raise RuntimeError("crontab update failed")


# 标准记录支持的任务类型 -> 由内容生成可执行命令的函数
# 说明：shell_command 直接使用脚本内容，其余类型按各自语义拼装命令。
_STANDARD_BUILDERS = {
    "shell_command": lambda content: content,
    "backup_container": lambda content: (
        f"docker export {content} | gzip > 'backup_{content}_"
        f"{datetime.now():%Y%m%d%H%M%S}.tar.gz'"
    ),
    "visit_url": lambda content: f"curl -sS -o /dev/null {content}",
    "clean_logs": lambda content: (
        f"find {content} -type f -name '*.log' -mtime +7 -delete"
    ),
    "sync_time": lambda content: (
        "w32tm /resync" if IS_WIN else "ntpdate pool.ntp.org"
    ),
}

# 可用的任务类型（用于后端校验，与前端下拉保持一致）
TASK_TYPES = list(_STANDARD_BUILDERS.keys())


class CreateTask(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    schedule: str = Field(..., min_length=1)  # Cron format or compatible
    # 常规记录：直接提供命令；标准记录可留空，由 task_type + content 生成
    command: str = Field("", max_length=10000)
    enabled: Optional[bool] = True
    # ---- 标准记录字段 ----
    task_type: str = Field("shell_command", pattern=r"^[a-z_]+$")
    content: str = Field("", max_length=10000)  # 脚本 / 容器名 / URL / 路径
    group: str = Field("默认", max_length=64)
    alert: bool = False  # 是否触发告警通知


class UpdateTask(BaseModel):
    schedule: Optional[str] = None
    command: Optional[str] = None
    enabled: Optional[bool] = None
    task_type: Optional[str] = None
    content: Optional[str] = None
    group: Optional[str] = None
    alert: Optional[bool] = None


def _resolve_command(task_type: str, content: str, fallback: str) -> str:
    """根据任务类型解析实际执行命令。

    标准记录（task_type + content）优先由类型构造器生成；
    否则回退到常规记录直接传入的 command。
    """
    content = (content or "").strip()
    if content or task_type != "shell_command":
        builder = _STANDARD_BUILDERS.get(task_type, lambda c: c)
        return builder(content) or fallback
    return fallback


@router.get("/list")
async def list_tasks():
    if IS_WIN:
        _sync_from_windows()
    tasks = _load_tasks()
    return {"tasks": tasks, "platform": "windows" if IS_WIN else "linux"}


@router.post("/create")
async def create_task(req: CreateTask):
    tasks = _load_tasks()
    # 安全校验：调度表达式白名单（防向 crontab 注入额外任务行）
    _validate_schedule(req.schedule)
    tid = "task_" + str(uuid.uuid4())[:8]
    # 校验任务类型合法性，避免非法枚举进入存储
    task_type = req.task_type if req.task_type in TASK_TYPES else "shell_command"
    # 解析最终执行命令：标准记录由类型构造，常规记录直接使用传入命令
    command = _resolve_command(task_type, req.content, req.command)
    if not command or not command.strip():
        raise HTTPException(status_code=400, detail="命令内容不能为空")
    task = {
        "id": tid,
        "name": req.name,
        "schedule": req.schedule,
        "command": command,
        "enabled": req.enabled if req.enabled is not None else True,
        # ---- 标准记录字段 ----
        "task_type": task_type,
        "content": (req.content or "").strip(),
        "group": (req.group or "默认").strip() or "默认",
        "alert": bool(req.alert),
        "created_at": datetime.now().isoformat(),
    }
    if IS_WIN:
        try:
            _create_windows_task(tid, req.name, command, req.schedule)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    else:
        crons = _list_linux_cron()
        crons.append({"schedule": req.schedule, "command": command})
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
        # 安全校验：更新调度表达式同样走白名单（防 crontab 注入）
        _validate_schedule(req.schedule)
        task["schedule"] = req.schedule
    if req.command is not None:
        task["command"] = req.command
    if req.enabled is not None:
        task["enabled"] = req.enabled
    # ---- 标准记录字段更新 ----
    if req.task_type is not None:
        task["task_type"] = req.task_type if req.task_type in TASK_TYPES else "shell_command"
    if req.content is not None:
        task["content"] = req.content
    if req.group is not None:
        task["group"] = (req.group or "默认").strip() or "默认"
    if req.alert is not None:
        task["alert"] = bool(req.alert)
    # 若更新了类型或内容但未显式提供 command，则重新生成命令
    if req.command is None and (req.task_type is not None or req.content is not None):
        task["command"] = _resolve_command(
            task.get("task_type", "shell_command"), task.get("content", ""), task.get("command", "")
        )
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
        # 在宿主机环境执行任务命令（容器模式经 chroot 映射）
        host_shell(task["command"])
    return {"ok": True}
