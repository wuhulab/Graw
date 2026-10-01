import json
import os
import platform
import re
import shlex
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


def _validate_cron_command(command: str) -> None:
    """校验 Linux 下写入 crontab 的命令必须为单行。

    crontab 文件按行解析，命令中的换行符会把后续内容解析为独立
    任务行（若带 5 字段则成为新任务），属于存储型注入；schedule
    白名单只约束了首行。多命令请用 && 或 ; 连接为单行。
    Windows 计划任务写入独立 .bat 脚本文件，无此约束（多行即脚本）。
    """
    if IS_WIN:
        return
    if not command or "\n" in command or "\r" in command:
        raise HTTPException(
            status_code=400,
            detail="Linux 计划任务命令必须为单行（多命令请用 && 或 ; 连接），不支持换行",
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
        # 读取任务配置失败时返回空列表
        pass
    return tasks


def _list_windows_tasks():
    try:
        # encoding 兜底（第十四轮审计修复）：中文 Windows 下 schtasks 输出 GBK，
        # 而 PYTHONUTF8=1 环境（常见于容器/CI/此开发机）下 text=True 按 utf-8 解码
        # 必抛 UnicodeDecodeError，导致计划任务创建/列表接口 500。显式指定
        # utf-8 + errors=replace：可读字段正常解析，损坏字节降级为替换符不中断流程。
        r = subprocess.run(
            ["schtasks", "/query", "/xml", "/fo", "list"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
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
    minute = hour = None
    if len(parts) >= 5:
        minute = parts[0]
        hour = parts[1]
    # 兼容性修复（第十四轮审计）：通配符/列表/步进字段（* */5 0-6 等）无法
    # 映射为 schtasks /st 的 HH:MM，此前直接 f"{hour.zfill(2)}:{minute.zfill(2)}"
    # 得到 "00:*/5" 等非法值，schtasks /create 必失败（创建计划任务 500）。
    # 非纯数字时回退默认 09:00，保证任务能创建、可立即运行。
    if not (minute and hour and minute.isdigit() and hour.isdigit()):
        st = "09:00"
    else:
        st = f"{hour.zfill(2)}:{minute.zfill(2)}"
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
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )
    if r.returncode != 0:
        raise RuntimeError(r.stderr or r.stdout)


def _delete_windows_task(name: str):
    subprocess.run(
        ["schtasks", "/delete", "/tn", name, "/f"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
    )


def _sync_windows_task(task: dict, existed: bool = True) -> None:
    """把面板任务状态同步到系统计划任务（第十四轮审计修复）。

    - enabled=False：删除系统任务（此前"禁用"只改 JSON，schtasks 仍按
      旧命令定时执行 —— 处置失效）
    - 命令/时间变化：schtasks /create /f 覆盖重建为最新命令
    - 新增任务（existed=False）：直接创建
    """
    if not task.get("enabled", True):
        if existed:
            _delete_windows_task(task["name"])
        return
    _create_windows_task(
        task["id"], task["name"], task["command"], task.get("schedule", "0 9 * * *")
    )


def _run_windows_task(name: str):
    subprocess.run(
        ["schtasks", "/run", "/tn", name], capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=30,
    )


def _list_linux_cron():
    """读取宿主 crontab，返回 (tasks, preserved)。

    - tasks: 面板可管理的「5 字段」任务列表 [{schedule, command}]
    - preserved: 宿主 crontab 中无法用 5 字段解析的行（@reboot/@daily 等
      special 时间、PATH=/SHELL= 等环境变量、注释），重写时必须原样保留

    安全修复（第十四轮审计，Medium）：此前仅识别 5 字段行，面板任何一次
    增删任务都会全量重写 crontab 并静默删除宿主自有的 @reboot 任务与
    环境变量行（宿主任务被破坏）。
    """
    try:
        # 在宿主机环境执行 crontab（容器模式经 chroot 映射，读写宿主 crontab）
        r = host_cmd(["crontab", "-l"], capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            return [], []
        lines = r.stdout.splitlines()
        tasks = []
        preserved = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            m = re.match(r"^((?:\S+\s+){5})(.*)$", line)
            if m:
                schedule = m.group(1).strip()
                command = m.group(2).strip()
                tasks.append({"schedule": schedule, "command": command})
            else:
                # @reboot / 环境变量 / 注释等：不可面板化管理，保留原文
                preserved.append(line)
        return tasks, preserved
    except Exception:
        return [], []


def _rewrite_linux_cron(tasks: list, preserved: list):
    """全量重写宿主 crontab。

    - 仅写入 enabled 的任务（禁用 = 从 crontab 移除，确保"禁用"真实生效）
    - preserved 中的宿主自有行原样保留，不因面板操作而丢失
    """
    lines = list(preserved)
    for t in tasks:
        if not t.get("enabled", True):
            continue
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
# 说明：shell_command 直接使用脚本内容（绝权功能，等价于管理员手写 crontab）；
#       其余类型把 content 作为"参数"拼进命令，安全隐患：content 源自表单输入，
#       若含 shell 元字符（; | & $() 反引号 空格）会在 host_shell/schr 执行时
#       被对端 /bin/sh 二次解释为命令。故对这类参数统一 shlex.quote 转义——
#       与 node_manager.host_cmd 的远程语义一致，URL/路径/容器名均安全。
def _quote_param(s: str) -> str:
    """安全转义单条命令参数（POSIX sh 语义），空串返回普通引号仍安全。"""
    return shlex.quote(s or "")


_STANDARD_BUILDERS = {
    "shell_command": lambda content: content,
    "backup_container": lambda content: (
        f"docker export {_quote_param(content)} | gzip > 'backup_{datetime.now():%Y%m%d%H%M%S}.tar.gz'"
    ),
    "visit_url": lambda content: f"curl -sS -o /dev/null {_quote_param(content)}",
    "clean_logs": lambda content: (
        f"find {_quote_param(content)} -type f -name '*.log' -mtime +7 -delete"
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
    # 安全校验：Linux 下命令必须单行，防止换行注入额外 crontab 任务行
    _validate_cron_command(command)
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
            _sync_windows_task(task, existed=False)
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))
    else:
        crons, preserved = _list_linux_cron()
        crons.append({"schedule": req.schedule, "command": command, "enabled": task["enabled"]})
        try:
            _rewrite_linux_cron(crons, preserved)
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
    # 记录旧命令：更新后系统侧同步时用于移除 crontab 中的旧条目
    old_command = task.get("command", "")
    if req.schedule is not None:
        # 安全校验：更新调度表达式同样走白名单（防 crontab 注入）
        _validate_schedule(req.schedule)
        task["schedule"] = req.schedule
    if req.command is not None:
        # 安全校验：更新命令同样必须单行（Linux crontab 语义，防存储型注入）
        _validate_cron_command(req.command)
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
        regenerated = _resolve_command(
            task.get("task_type", "shell_command"), task.get("content", ""), task.get("command", "")
        )
        # 重新生成的命令同样走单行校验（Linux crontab 语义）
        _validate_cron_command(regenerated)
        task["command"] = regenerated
    _save_tasks(tasks)
    # 安全修复（第十四轮审计，High）：此前 update_task 只写 JSON 存储，
    # 从不同步系统计划任务——管理员"禁用任务"或"改为安全命令"后，
    # crontab/schtasks 仍按旧命令旧时间表持续执行（处置失效，CWE-672）。
    # 现同步到系统侧：Linux 全量重写（仅启用任务）+ 保留宿主自有行；
    # Windows 禁用则删除系统任务、变更则覆盖重建。
    try:
        if IS_WIN:
            _sync_windows_task(task, existed=True)
        else:
            crons, preserved = _list_linux_cron()
            # 移除该任务此前写入 crontab 的旧条目（按旧命令匹配）
            crons = [c for c in crons if c.get("command") != old_command]
            if task.get("enabled", True):
                crons.append({
                    "schedule": task["schedule"], "command": task["command"],
                    "enabled": True,
                })
            _rewrite_linux_cron(crons, preserved)
    except HTTPException:
        raise
    except Exception as e:
        # 系统同步失败不应回滚 JSON（面板状态已保存），但必须让管理员感知
        logger.error("计划任务系统侧同步失败: %s", e)
        raise HTTPException(
            status_code=500,
            detail=f"面板配置已保存，但系统计划任务同步失败（{e}）。"
                   f"请检查 crontab/schtasks 权限后重试。",
        )
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
        crons, preserved = _list_linux_cron()
        crons = [c for c in crons if c["command"] != task["command"]]
        _rewrite_linux_cron(crons, preserved)
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
