# -*- coding: utf-8 -*-
"""
test_terminal_mouse.py — 终端 TUI 鼠标能力检测与输入过滤单元测试

覆盖（无需后端运行，直接调用 terminal 路由模块函数）：
  1. _conpty_mouse_supported — 平台能力判定：
     - Windows build < 22523（Win10）→ 不支持并给出原因
     - Windows build >= 22523（Win11 22H2+）→ 支持
     - 非 Windows（Linux/macOS）→ 支持（pty 原生透传）
     - 版本解析失败 → 按不支持处理（fail-closed）
  2. _MOUSE_INPUT_RE — 在不支持平台上剔除浏览器发来的鼠标序列：
     - SGR 鼠标按下/释放（ESC[<…M / ESC[<…m）
     - 传统 X10 鼠标报告头（ESC[M）
     - DECSET/DECRST 鼠标模式开关（?1000/?1002/?1003/?1006 等）
     - 普通输入（文本 / 回车 / 方向键 / SGR 颜色重置 / 光标移动）不受影响

运行：.venv\\Scripts\\python.exe test_terminal_mouse.py
"""
import os
import platform
import sys

# 确保以 backend 目录为工作目录时可直接导入 app 包
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.routers import terminal  # noqa: E402

PASS = 0
FAIL = 0


def check(name: str, cond: bool, extra: str = ""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"[PASS] {name}")
    else:
        FAIL += 1
        print(f"[FAIL] {name} {extra}")


def with_platform(win: bool, version: str):
    """临时替换平台环境并调用 _conpty_mouse_supported，结束后自动恢复。"""
    old_is_windows = terminal.IS_WINDOWS
    old_platform_version = getattr(platform, "version", None)
    terminal.IS_WINDOWS = win
    if old_platform_version is not None:
        platform.version = lambda: version  # type: ignore[assignment]
    try:
        return terminal._conpty_mouse_supported()
    finally:
        terminal.IS_WINDOWS = old_is_windows
        if old_platform_version is not None:
            platform.version = old_platform_version  # type: ignore[assignment]


# --- 1. 平台能力判定 ---------------------------------------------------------
print("== 平台能力判定 ==")

# Windows 10（build 19045）→ 不支持
ok, reason = with_platform(True, "10.0.19045")
check("Win10 build 19045 判为不支持", ok is False and "22523" in reason, str((ok, reason)))

# Windows 11 22H2（build 22523）→ 支持
ok, reason = with_platform(True, "10.0.22621")
check("Win11 build 22621 判为支持", ok is True and reason == "", str((ok, reason)))

# 临界点 build 22523 → 支持
ok, reason = with_platform(True, "10.0.22523")
check("Win11 build 22523（临界值）判为支持", ok is True, str((ok, reason)))

# 非 Windows → 支持
ok, reason = with_platform(False, "10.0.19045")
check("非 Windows 判为支持", ok is True and reason == "", str((ok, reason)))

# 版本解析失败 → 不支持（fail-closed）
ok, reason = with_platform(True, "not-a-version")
check("版本解析失败按不支持处理", ok is False, str((ok, reason)))

# --- 2. 鼠标输入过滤 ---------------------------------------------------------
print("== 鼠标输入过滤 ==")

strip = lambda s: terminal._MOUSE_INPUT_RE.sub("", s)  # noqa: E731

# SGR 鼠标按下 / 释放（opencode 联想菜单点击产生的帧）
check(
    "剔除 SGR 按下帧 ESC[<0;28;1M",
    strip("\x1b[<0;28;1M") == "",
    repr(strip("\x1b[<0;28;1M")),
)
check(
    "剔除 SGR 释放帧 ESC[<0;28;1m",
    strip("\x1b[<0;28;1m") == "",
    repr(strip("\x1b[<0;28;1m")),
)
# 多段坐标（滚轮 / 拖拽）格式 ESC[<64;12;5M
check(
    "剔除 SGR 多段坐标帧",
    strip("\x1b[<64;12;5M") == "",
    repr(strip("\x1b[<64;12;5M")),
)
# 鼠标帧混在输入中：只剔除鼠标部分，保留普通字符
check(
    "剔除混入输入的鼠标帧并保留文本",
    strip("abc\x1b[<0;28;1Mxyz") == "abcxyz",
    repr(strip("abc\x1b[<0;28;1Mxyz")),
)

# 传统 X10 鼠标报告头
check("剔除 X10 鼠标报告头 ESC[M", strip("\x1b[M") == "", repr(strip("\x1b[M")))

# DECSET/DECRST 鼠标模式开关
for seq in ("\x1b[?1000h", "\x1b[?1000l", "\x1b[?1002h", "\x1b[?1003h",
            "\x1b[?1006h", "\x1b[?1006l", "\x1b[?1004h", "\x1b[?9h"):
    check(f"剔除鼠标模式开关 {seq!r}", strip(seq) == "", repr(strip(seq)))

# 普通输入不受影响：文本、回车、退格、方向键、SGR 颜色重置、光标定位
for payload in (
    "echo hello",
    "cd /d \"S:\\Graw\"\r\n",
    "\r",
    "\x1b[A",          # 上箭头
    "\x1b[0m",         # SGR 颜色重置（小写 m，非鼠标释放）
    "\x1b[2J\x1b[H",   # 清屏 + 光标归位
    "opencode\r\n",
):
    check(f"普通输入不被误删 {payload!r}", strip(payload) == payload, repr(strip(payload)))


print(f"\n结果：{PASS} 通过, {FAIL} 失败")
sys.exit(1 if FAIL else 0)
