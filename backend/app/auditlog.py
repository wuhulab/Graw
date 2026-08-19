# -*- coding: utf-8 -*-
"""
auditlog.py - 面板操作审计日志模块

背景：
  面板的预置日志源「面板日志」(panel.log) 此前仅有占位（logs.py 的
  PREDEFINED 证明它存在），但实际无人写入，导致登录 / 退出 / 文件编辑 /
  命令执行等关键操作只打到控制台，无法在面板日志窗口回溯。
  本模块提供统一的审计写入入口，将这些操作以「一行一条」的形式追加到
  data/panel.log，供面板日志界面直接展示。

设计要点：
  - 线程安全：持有模块级锁 + 每次 open 追加，避免并发写入互相覆盖。
  - 容错：审计失败（磁盘满 / 权限拒绝）绝不影响业务请求，静默降级。
  - 幂等 & 低开销：日志量小（操作级而非数据级），逐条追加即可，无需缓冲。
  - 安全：不对日志内容做二次过滤——调用方负责脱敏（不记录密码/密钥等）。
"""

import os
import threading
from datetime import datetime

# 面板自身日志文件（与 logs.py 中的 PREDEFINED["panel"] 指向同一个文件）
PANEL_LOG = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "data", "panel.log")
)

# 单文件最大体积（约 5MB）：超出后重命名归档为 panel.log.1 并从空文件继续，
# 防止审计日志无限膨胀占用数据分区（防御性限额，正常操作级日志远达不到）。
_MAX_LOG_BYTES = 5 * 1024 * 1024

_lock = threading.Lock()


def _rotate_if_needed() -> None:
    """当面板日志超过体积上限时，将其归档为 panel.log.1 并从空文件继续。

    旧文件直接覆盖（只保留 1 份历史），避免审计日志无限累积。
    """
    try:
        if os.path.exists(PANEL_LOG) and os.path.getsize(PANEL_LOG) > _MAX_LOG_BYTES:
            os.replace(PANEL_LOG, PANEL_LOG + ".1")
    except OSError:
        # 归档失败不致命：继续追加（下一次启动仍会尝试 rotate）
        pass


def _append_line(line: str) -> None:
    """将一行日志追加写盘并确保落盘（写入失败静默降级，不影响业务）。"""
    try:
        os.makedirs(os.path.dirname(PANEL_LOG), exist_ok=True)
        with open(PANEL_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
            # flush 后由 Python/OS 决定何时真正落到磁盘；操作级日志无需逐条 fsync
    except Exception:
        # 审计失败不允许向业务层抛错——面板功能照常，仅缺失审计记录
        pass


def record(action: str, user: str = "", ip: str = "", detail: str = "") -> None:
    """记录一条面板操作审计日志。

    参数：
        action: 动作名称（如 "登录成功"、"写文件"、"结束进程"）
        user:   操作者用户名（为空表示系统后台任务）
        ip:     操作来源 IP（可为空）
        detail: 补充说明（路径 / 命令 / 目标等，调用方需自行脱敏）
    """
    # 日志时间统一使用本地时区（面板运行所在主机上的时间）
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    parts = [f"[{ts}]", f"[{action}]"]
    if user:
        parts.append(f"[用户:{user}]")
    if ip:
        parts.append(f"[IP:{ip}]")
    if detail:
        parts.append(detail)
    line = " ".join(parts)

    with _lock:
        # 每次写入前检查体积，超限先归档（锁内保证 rotate 与追加原子一致）
        _rotate_if_needed()
        _append_line(line)