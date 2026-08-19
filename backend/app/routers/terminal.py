from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
import asyncio
import os
import platform
import re
import threading
import subprocess

from app.auth import get_current_user, get_current_user_ws_admin
from app.hostfs import get_host_root
from app import node_manager
from app import auditlog
from app.routers.docker_api import get_backend, _find_podman

router = APIRouter()

IS_WINDOWS = platform.system() == "Windows"

# Windows 11 22H2（build 22523）起 ConPTY 才支持把宿主侧输入的鼠标
# 序列转换为控制台 MOUSE_EVENT 投递给 TUI；Windows 10 及更早版本
# 会静默丢弃这些字节（microsoft/terminal#376），甚至可能触发 conhost
# 崩溃（psmux#457），因此低于该版本必须禁用 TUI 鼠标注入。
_WIN_MOUSE_MIN_BUILD = 22523

# 浏览器侧（xterm）发出的鼠标相关序列：SGR 报告 / X10 报告头 / DECSET
# 鼠标模式开关。在不支持鼠标输入的平台上，这些字节对 TUI 无效且有
# 崩溃风险，需要在下发到 ConPTY 前剔除。
_MOUSE_INPUT_RE = re.compile(
    # SGR 扩展鼠标报告，如 ESC[<0;28;1M（按下）/ ESC[<0;28;1m（释放）
    r"\x1b\[<(?:[0-9]+;)*[0-9]+[Mm]"
    # 传统 X10 鼠标报告头（ESC[M + 3 字节坐标）
    r"|\x1b\[M"
    # DECSET/DECRST 鼠标模式开关：?9 / ?1000 / ?1002 / ?1003 / ?1004 /
    # ?1005 / ?1006 / ?1015 / ?1016（h 启用 / l 禁用）
    r"|\x1b\[\?(?:9|1000|1002|1003|1004|1005|1006|1015|1016)[hl]"
)


def _conpty_mouse_supported() -> tuple:
    """检测当前平台是否支持向 ConPTY/PTY 注入 TUI 鼠标。

    返回 (supported, reason)：
    - 非 Windows（Linux/macOS）：pty 原生透传鼠标序列，恒支持。
    - Windows build >= 22523（Windows 11 22H2+）：支持。
    - Windows build < 22523（Windows 10 及更早）：不支持，返回原因。
    """
    if not IS_WINDOWS:
        return True, ""
    try:
        # platform.version() 在 Windows 形如 "10.0.19045"，取末段为 build
        build = int(platform.version().rsplit(".", 1)[-1])
    except (ValueError, IndexError):
        # 无法确认版本时按不支持处理，避免引入崩溃风险
        return False, "无法确认 Windows 版本，已禁用 TUI 鼠标输入"
    if build >= _WIN_MOUSE_MIN_BUILD:
        return True, ""
    return (
        False,
        f"当前 Windows 10（build {build}）的 ConPTY 不支持 TUI 鼠标"
        f"（需 Windows 11 22H2 build {_WIN_MOUSE_MIN_BUILD}+ 或 Linux 节点）",
    )

# 容器 ID / 名称白名单：container 参数最终会拼入 exec 命令串（经 ConPTY
# 或 shell 启动），必须校验格式，防止携带引号 / 分号等字符注入命令。
_CONTAINER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")

if IS_WINDOWS:
    try:
        from app.routers._wincon import ConPTY, ConPTYError
        _CONPTY_AVAILABLE = True
    except Exception:  # pragma: no cover - exotic/broken envs
        _CONPTY_AVAILABLE = False
else:
    _CONPTY_AVAILABLE = False


def _container_exec_command(container_id: str) -> str:
    """构造进入容器内执行命令的命令串（供 ConPTY 使用）。

    优先使用 podman/docker 的 exec -it 进入容器 shell；
    容器未运行或引擎不可用时抛出 RuntimeError。
    """
    # container_id 拼入命令串执行：白名单校验，阻止引号/分号等注入
    if not _CONTAINER_RE.match(container_id or ""):
        raise RuntimeError("非法的容器标识")
    try:
        kind, _client = get_backend()
    except Exception as e:
        raise RuntimeError(f"容器引擎不可用: {e}")
    if kind == "cli":
        cli = _find_podman()
        if not cli:
            raise RuntimeError("未检测到可用的容器引擎")
        # 校验容器存在（running 才可 exec）
        rc, out, _err = _run_subprocess(cli + ["inspect", container_id])
        if rc != 0:
            raise RuntimeError("容器不存在或无法访问")
        import json as _json
        try:
            data = _json.loads(out)
            if isinstance(data, list) and data and data[0].get("State", {}).get("Running"):
                pass
            else:
                raise RuntimeError("容器未运行，无法打开终端")
        except RuntimeError:
            raise
        except Exception:
            raise RuntimeError("容器未运行，无法打开终端")
        if IS_WINDOWS:
            # Windows 上容器引擎跑在 WSL 中，需要经过 wsl -u root
            return "wsl -u root -- podman exec -it " + container_id + " /bin/sh"
        return "podman exec -it " + container_id + " /bin/sh"
    # Docker SDK 模式：使用 docker CLI 执行（容器通过 SDK 访问时一般也有 CLI）
    return "docker exec -it " + container_id + " /bin/sh"


def _run_subprocess(args, timeout=30):
    """运行子进程并返回 (returncode, stdout, stderr)，避免与事件循环冲突。"""
    import subprocess as _sp
    try:
        p = _sp.run(args, capture_output=True, timeout=timeout)
    except Exception:
        return -1, "", ""
    return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")


@router.get("/mouse-capability")
async def terminal_mouse_capability(user=Depends(get_current_user)):
    """查询当前平台是否支持终端 TUI 鼠标点击。

    前端据此决定是否展示/启用「鼠标」开关：Windows 10 及更早版本的
    ConPTY 无法把鼠标序列投递给 TUI，强行注入无效且有崩溃风险，
    需在 UI 上禁用并给出原因。
    """
    supported, reason = _conpty_mouse_supported()
    return {"supported": supported, "reason": reason}


@router.websocket("/ws/container")
async def container_terminal_ws(
    websocket: WebSocket,
    container: str,
    user=Depends(get_current_user_ws_admin),
):
    """进入指定容器的交互终端（exec -it /bin/sh）。

    container 参数为容器 ID 或名称；仅运行中的容器可打开终端。
    """
    if user is None:
        return
    await websocket.accept()
    auditlog.record(
        "进入容器终端",
        (user or {}).get("username", ""),
        websocket.client.host if websocket.client else "",
        container,
    )
    try:
        command = _container_exec_command(container)
    except RuntimeError as e:
        try:
            await websocket.send_text(f"\r\n[container terminal] {e}\r\n")
            await websocket.close()
        except Exception:
            pass
        return
    try:
        if IS_WINDOWS:
            await _windows_conpty_terminal(websocket, command)
        else:
            await _unix_terminal(websocket)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await websocket.send_text(f"\r\n[container terminal] {e}\r\n")
        except Exception:
            pass
        try:
            await websocket.close()
        except Exception:
            pass


@router.websocket("/ws")
async def terminal_ws(websocket: WebSocket, user=Depends(get_current_user_ws_admin)):
    # get_current_user_ws_admin 在鉴权失败时会关闭连接并返回 None
    if user is None:
        return
    await websocket.accept()
    # 记录远程终端开启：是否作用于远程节点由 node_manager 运行时决定
    target = "远程节点终端" if node_manager.is_remote() else "本机终端"
    auditlog.record(
        target,
        (user or {}).get("username", ""),
        websocket.client.host if websocket.client else "",
    )
    try:
        if IS_WINDOWS:
            await _windows_terminal(websocket)
        else:
            await _unix_terminal(websocket)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await websocket.send_text(f"\r\n[terminal error] {e}\r\n")
        except Exception:
            pass
        try:
            await websocket.close()
        except Exception:
            pass


def _make_reader(read_fn, loop, out_queue: "asyncio.Queue[bytes]", stop_flag):
    """Bridge a blocking read callback to an asyncio Queue via the loop.

    The reader runs on a daemon thread and pushes chunks to the loop using
    ``call_soon_threadsafe``. This avoids ``run_in_executor`` (which would
    pin a thread-pool thread for the entire session and cause large output
    latency when the pool is busy) and delivers output with sub-millisecond
    latency.
    """

    def _reader():
        try:
            while not stop_flag.is_set():
                chunk = read_fn()
                if not chunk:
                    break
                loop.call_soon_threadsafe(out_queue.put_nowait, chunk)
        except Exception:
            pass
        finally:
            loop.call_soon_threadsafe(out_queue.put_nowait, b"")

    return _reader


async def _pump_output(out_queue: "asyncio.Queue[bytes]", websocket: WebSocket, encoding="utf-8"):
    while True:
        chunk = await out_queue.get()
        if not chunk:
            break
        try:
            text = chunk.decode(encoding)
        except UnicodeDecodeError:
            text = chunk.decode(encoding, errors="replace")
        await websocket.send_text(text)


def _windows_shell_cmd() -> str:
    """返回 Windows 交互终端的默认 shell。

    优先使用 PowerShell（先找 pwsh 即 PowerShell 7，再找 powershell 即
    Windows PowerShell），都没有时回退到 COMSPEC / cmd.exe，保证终端可用。
    """
    import shutil

    for candidate in ("pwsh", "powershell"):
        exe = shutil.which(candidate)
        if exe:
            return exe
    return os.environ.get("COMSPEC", "cmd.exe")


async def _windows_terminal(websocket: WebSocket):
    """Windows terminal backed by ConPTY (real pseudoconsole).

    Falls back to a plain ``cmd.exe`` subprocess pipe when ConPTY is not
    available (e.g. old Windows builds). The ConPTY path is strongly
    preferred: with a plain pipe, the shell runs in redirected-input mode and
    neither echoes typed characters nor shows a prompt, which makes the
    terminal appear to ignore all input.
    """
    # 默认交互 shell 改为 PowerShell（无则回退 cmd），便于运行交互式 TUI
    shell = _windows_shell_cmd()

    if _CONPTY_AVAILABLE:
        try:
            await _windows_conpty_terminal(websocket, shell)
            return
        except ConPTYError:
            # ConPTY unavailable on this build / environment; fall through.
            pass
    await _windows_pipe_terminal(websocket, shell)


async def _windows_conpty_terminal(websocket: WebSocket, shell: str):
    pty = ConPTY(rows=24, cols=80)
    pty.start(shell)

    # 平台是否支持 TUI 鼠标注入：Windows 10 及更早的 ConPTY 不支持，
    # 需要剔除浏览器发来的鼠标序列，避免无效写入与 conhost 崩溃风险。
    mouse_ok = _conpty_mouse_supported()[0]

    loop = asyncio.get_running_loop()
    out_queue: "asyncio.Queue[bytes]" = asyncio.Queue()
    stop_flag = threading.Event()

    reader = _make_reader(lambda: pty.read(4096), loop, out_queue, stop_flag)
    threading.Thread(target=reader, daemon=True).start()

    output_task = asyncio.create_task(_pump_output(out_queue, websocket))

    try:
        while True:
            data = await websocket.receive_text()
            if data.startswith("\x1bRESIZE:"):
                try:
                    _, dims = data.split(":", 1)
                    rows, cols = (int(x) for x in dims.split(","))
                    pty.resize(rows, cols)
                except Exception:
                    pass
                continue
            if not mouse_ok:
                # 剔除鼠标相关序列；若整帧都是鼠标字节则直接跳过
                data = _MOUSE_INPUT_RE.sub("", data)
                if not data:
                    continue
            if not pty.is_alive():
                break
            try:
                pty.write(data.encode("utf-8"))
            except Exception:
                break
    except WebSocketDisconnect:
        pass
    finally:
        stop_flag.set()
        try:
            pty.close()
        except Exception:
            pass
        output_task.cancel()
        try:
            await output_task
        except (asyncio.CancelledError, Exception):
            pass


async def _windows_pipe_terminal(websocket: WebSocket, shell: str):
    """Fallback: plain cmd.exe subprocess over pipes (no echo, no PTY)."""
    proc = subprocess.Popen(
        [shell, "/Q", "/K"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=0,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )

    loop = asyncio.get_running_loop()
    out_queue: "asyncio.Queue[bytes]" = asyncio.Queue()
    stop_flag = threading.Event()

    def _read():
        if hasattr(proc.stdout, "read1"):
            return proc.stdout.read1(1024)
        return proc.stdout.read(1024)

    reader = _make_reader(_read, loop, out_queue, stop_flag)
    threading.Thread(target=reader, daemon=True).start()

    output_task = asyncio.create_task(_pump_output(out_queue, websocket, encoding="utf-8"))

    try:
        while True:
            data = await websocket.receive_text()
            if data.startswith("\x1bRESIZE:"):
                continue
            if proc.poll() is not None:
                break
            # cmd.exe with redirected stdin expects CRLF line endings.
            data = data.replace("\r", "\r\n").replace("\r\n\r\n", "\r\n")
            try:
                proc.stdin.write(data.encode("utf-8"))
                proc.stdin.flush()
            except Exception:
                break
    except WebSocketDisconnect:
        pass
    finally:
        stop_flag.set()
        try:
            proc.stdin.close()
        except Exception:
            pass
        try:
            proc.kill()
        except Exception:
            pass
        try:
            output_task.cancel()
        except Exception:
            pass


async def _unix_terminal(websocket: WebSocket):
    import pty
    import fcntl
    import termios
    import struct
    import signal

    shell = os.environ.get("SHELL", "/bin/bash")

    # 多机：当当前管理主机为远程节点时，直接 `ssh -tt` 进入该节点交互终端
    if node_manager.is_remote():
        remote_node = node_manager.get_current_node()
        argv = node_manager.remote_terminal_argv(remote_node)
        pid, fd = pty.fork()
        if pid == 0:
            os.execv(argv[0], argv)
            return
        await _interactive_tty(websocket, fd, pid)
        return

    pid, fd = pty.fork()
    if pid == 0:
        # 容器模式下 chroot 到宿主机根目录，让终端直接操作宿主机
        host_root = get_host_root()
        if host_root:
            try:
                os.chroot(host_root)
                os.chdir("/")
            except OSError:
                # chroot 失败（非特权）时退回容器内 shell
                pass
        os.execv(shell, [shell])
        return

    await _interactive_tty(websocket, fd, pid)


async def _interactive_tty(websocket: WebSocket, fd, pid):
    """共享的 pty 读写循环：驱动一个已 fork 出的伪终端会话。"""
    import fcntl
    import termios
    import struct
    import signal

    loop = asyncio.get_running_loop()
    out_queue: "asyncio.Queue[bytes]" = asyncio.Queue()
    stop_flag = threading.Event()

    def set_winsize(rows, cols):
        try:
            fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        except Exception:
            pass

    set_winsize(24, 80)

    reader = _make_reader(lambda: os.read(fd, 1024), loop, out_queue, stop_flag)
    threading.Thread(target=reader, daemon=True).start()

    output_task = asyncio.create_task(_pump_output(out_queue, websocket))

    try:
        while True:
            msg = await websocket.receive_text()
            if msg.startswith("\x1bRESIZE:"):
                try:
                    _, dims = msg.split(":", 1)
                    rows, cols = [int(x) for x in dims.split(",")]
                    set_winsize(rows, cols)
                except Exception:
                    pass
                continue
            os.write(fd, msg.encode("utf-8"))
    except WebSocketDisconnect:
        pass
    finally:
        stop_flag.set()
        output_task.cancel()
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception:
            pass
        try:
            os.close(fd)
        except Exception:
            pass
