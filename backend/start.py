"""
start.py — Graw 后端一键启动脚本

功能：
    1. 启动前自动清理占用 8000 端口的残留进程（解决 WinError 10013 端口占用问题）
    2. 使用项目虚拟环境启动 uvicorn 开发服务器（--reload）

用法：
    python start.py                # 清理端口占用后启动后端（默认）
    python start.py --kill-only    # 仅清理占用 8000 端口的进程，不启动后端
    python start.py --self-test    # 运行内置自检（验证 netstat 解析与端口探测逻辑）

说明：
    - 仅依赖 Python 标准库，无需安装额外包
    - Windows 下使用 netstat -ano 定位端口占用进程，taskkill /T /F 结束进程树
      （uvicorn --reload 会派生 watchdog 子进程，故必须杀进程树）
"""

from __future__ import annotations

import argparse
import logging
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

# 端口与路径常量（集中管理，避免散落魔法数字）
PORT = 8000
BACKEND_DIR = Path(__file__).resolve().parent
VENV_PYTHON = BACKEND_DIR / ".venv" / "Scripts" / "python.exe"

# 匹配 netstat 行尾的 PID 字段：以数字结尾（TIME_WAIT 等状态 PID 为 0，需排除）
_PID_RE = re.compile(r"(\d+)\s*$")
# 匹配 netstat 行中形如 ":8000 " 的本地地址端口段（避免误匹配远程端口）
_PORT_RE = re.compile(r":%d\s" % PORT)

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
log = logging.getLogger("start")


def _decode_text(raw: bytes) -> str:
    """
    将子进程输出解码为文本，兼容中英文 Windows。

    系统命令（netstat/taskkill）在中文 Windows 下输出 GBK 编码字节，
    按 utf-8 → gbk 依次尝试解码，兜底用 replace 模式保证不抛异常。
    """
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _run_capture(args: list[str]) -> tuple[int, str]:
    """执行命令并以“容忍解码”的方式捕获输出，返回 (returncode, 文本)。"""
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            timeout=15,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        text = _decode_text(proc.stdout or b"") + _decode_text(proc.stderr or b"")
        return proc.returncode, text.strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        log.error("执行 %s 失败：%s", args[0], e)
        return -1, ""


def find_pids_on_port(port: int) -> set[int]:
    """
    找出当前占用指定端口的所有进程 PID。

    通过 `netstat -ano` 输出解析，仅收集 TCP 行且 PID > 0 的进程，
    并排除脚本自身及父进程，防止误杀。返回去重后的 PID 集合。
    """
    rc, output = _run_capture(["netstat", "-ano"])
    if rc != 0:
        log.warning("netstat 返回非零状态（%d），按无占用处理", rc)
        return set()

    pids: set[int] = set()
    for line in output.splitlines():
        # 只关心 TCP 段，且本地地址段命中目标端口
        if "TCP" not in line or not _PORT_RE.search(line):
            continue
        m = _PID_RE.search(line)
        if not m:
            continue
        pid = int(m.group(1))
        # PID 0 为系统 TIME_WAIT 占位，无需处理；跳过脚本自身及其父进程
        if pid > 0 and pid != os.getpid() and pid != os.getppid():
            pids.add(pid)
    return pids


def kill_pids(pids: set[int]) -> None:
    """强制终止指定进程（含子进程树）。逐个终止并记录结果。"""
    for pid in sorted(pids):
        rc, text = _run_capture(["taskkill", "/PID", str(pid), "/T", "/F"])
        if rc == 0:
            log.info("已终止进程 %d（含子进程）", pid)
        else:
            log.warning("终止进程 %d 未成功：%s", pid, text[:200])


def wait_port_free(port: int, timeout: float = 8.0) -> bool:
    """
    等待端口释放（供启动前规避 TIME_WAIT/进程残留导致的绑定失败）。

    以“能否成功创建监听套接字”作为端口可用的判定依据，轮询探测，
    超时返回 False。性能优化：单次探测后短暂 sleep，避免忙等。
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            # 开启地址复用，贴近 uvicorn 的实际绑定行为
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            # 端口占用探测：必须绑定 0.0.0.0 才能反映 uvicorn 的实际监听（含局域网来源）
            sock.bind(("0.0.0.0", port))  # lgtm[py/bind-socket-all-network-interfaces]
            return True  # 能绑定即视为端口已释放
        except OSError:
            time.sleep(0.3)
        finally:
            sock.close()
    return False


def start_server() -> int:
    """使用项目虚拟环境启动 uvicorn，透传输出并阻塞直至退出。"""
    python = VENV_PYTHON if VENV_PYTHON.exists() else Path(sys.executable)
    cmd = [
        str(python),
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        "0.0.0.0",
        "--port",
        str(PORT),
        "--reload",
    ]
    log.info("启动后端：%s", " ".join(cmd))
    # 不捕获输出，让 uvicorn 日志实时显示在终端
    return subprocess.run(cmd, cwd=str(BACKEND_DIR)).returncode


def _self_test() -> int:
    """内置自检：用模拟的 netstat 输出验证解析逻辑与端口探测逻辑。"""
    sample = f"""  TCP    127.0.0.1:{PORT}   127.0.0.1:5000   ESTABLISHED    1234
  TCP    0.0.0.0:{PORT}           0.0.0.0:0              LISTENING       22496
  TCP    0.0.0.0:{PORT}           0.0.0.0:0              TIME_WAIT       0
  TCP    0.0.0.0:9999            0.0.0.0:0              LISTENING       5678
  UDP    0.0.0.0:{PORT}           *:*                                    9999"""
    found = {int(m.group(1)) for line in sample.splitlines() if "TCP" in line
             and _PORT_RE.search(line) if (m := _PID_RE.search(line))
             if int(m.group(1)) > 0}
    assert found == {1234, 22496}, f"解析结果异常：{found}"
    log.info("自检通过：端口 %d 解析命中 %s", PORT, sorted(found))

    # 端口探测：本机随机取一个临时端口，验证 wait_port_free 在空闲端口上返回 True
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # 临时端口探测（bind 0 由系统分配随机空闲端口），只用于测试 wait_port_free
    probe.bind(("0.0.0.0", 0))  # lgtm[py/bind-socket-all-network-interfaces]
    free_port = probe.getsockname()[1]
    probe.close()
    assert wait_port_free(free_port, timeout=2.0), "空闲端口探测应为 True"
    log.info("自检通过：端口可用性探测正常（端口 %d）", free_port)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Graw 后端启动脚本：自动清理 8000 端口占用后启动")
    parser.add_argument("--kill-only", action="store_true", help="仅清理占用 8000 端口的进程，不启动后端")
    parser.add_argument("--self-test", action="store_true", help="运行内置自检后退出")
    args = parser.parse_args(argv)

    if args.self_test:
        try:
            return _self_test()
        except AssertionError as e:
            log.error("自检失败：%s", e)
            return 1

    # 1) 清理端口占用
    pids = find_pids_on_port(PORT)
    if pids:
        log.info("发现 %d 个进程占用端口 %d：%s", len(pids), PORT, sorted(pids))
        kill_pids(pids)
    else:
        log.info("端口 %d 未被占用", PORT)

    if args.kill_only:
        return 0

    # 2) 等待端口释放后启动后端
    if not wait_port_free(PORT):
        log.error("端口 %d 在等待后仍无法绑定，请检查是否存在受保护进程", PORT)
        return 1
    return start_server()


if __name__ == "__main__":
    sys.exit(main())
