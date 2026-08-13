from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
import asyncio
import os
import platform
import threading
import subprocess

from app.auth import get_current_user_ws
from app.hostfs import get_host_root

router = APIRouter()

IS_WINDOWS = platform.system() == "Windows"

if IS_WINDOWS:
    try:
        from app.routers._wincon import ConPTY, ConPTYError
        _CONPTY_AVAILABLE = True
    except Exception:  # pragma: no cover - exotic/broken envs
        _CONPTY_AVAILABLE = False
else:
    _CONPTY_AVAILABLE = False


@router.websocket("/ws")
async def terminal_ws(websocket: WebSocket, user=Depends(get_current_user_ws)):
    # get_current_user_ws 在鉴权失败时会关闭连接并返回 None
    if user is None:
        return
    await websocket.accept()
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


async def _windows_terminal(websocket: WebSocket):
    """Windows terminal backed by ConPTY (real pseudoconsole).

    Falls back to a plain ``cmd.exe`` subprocess pipe when ConPTY is not
    available (e.g. old Windows builds). The ConPTY path is strongly
    preferred: with a plain pipe, cmd.exe runs in redirected-input mode and
    neither echoes typed characters nor shows a prompt, which makes the
    terminal appear to ignore all input.
    """
    shell = os.environ.get("COMSPEC", "cmd.exe")

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
