from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
import asyncio
import os
import platform
import re
import threading
import subprocess

from app.auth import get_current_user, get_current_user_ws_admin, ws_session_still_valid
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
        # CodeQL [py/empty-except] WebSocket 正常断开：清理由 finally 完成
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
async def terminal_ws(
    websocket: WebSocket,
    node: str = "",
    user=Depends(get_current_user_ws_admin),
):
    # get_current_user_ws_admin 在鉴权失败时会关闭连接并返回 None
    if user is None:
        return
    await websocket.accept()
    # 「统一面板兼容」：浏览器 WebSocket 无法携带自定义请求头，目标节点经查询参数下发。
    # 用请求级节点覆盖全局当前节点，使该终端会话连接「窗口绑定」的节点（而非全局）。
    prev_node = node_manager._req_ctx_node()
    if node and node.strip():
        node_manager.set_request_node(node.strip())
    try:
        # 记录远程终端开启：是否作用于远程节点由 node_manager 运行时决定
        target = "远程节点终端" if node_manager.is_remote() else "本机终端"
        auditlog.record(
            target,
            (user or {}).get("username", ""),
            websocket.client.host if websocket.client else "",
        )
        if node_manager.is_remote():
            # 多机：当前主机为 SSH 节点 → 进入远端交互终端。
            # Windows 控制端用 ConPTY 驱动 `ssh -tt`；Unix 用 pty.fork 走
            # remote_terminal_argv（_unix_terminal 内含远端分支）。
            if IS_WINDOWS:
                await _windows_remote_terminal(websocket)
            else:
                await _unix_terminal(websocket)
            return
        if IS_WINDOWS:
            await _windows_terminal(websocket)
        else:
            await _unix_terminal(websocket)
    except WebSocketDisconnect:
        # CodeQL [py/empty-except] WebSocket 正常断开：清理由 finally 完成
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
    finally:
        node_manager.set_request_node(prev_node)


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


def _windows_quote_argv(argv: list) -> str:
    """把 argv 拼成 CreateProcessW 可接受的 Windows 命令行字符串。

    ConPTY.start 接收一条命令行；这里对含空格/引号的参数用双引号包裹，
    并对内部双引号做转义，保证 ssh 参数（如密钥路径、host）能正确解析。
    """
    parts = []
    for a in argv:
        if a and all(c not in ' \t"&|<>()' for c in a):
            parts.append(a)
            continue
        escaped = a.replace('"', '\\"')
        parts.append(f'"{escaped}"')
    return " ".join(parts)


async def _windows_remote_terminal(websocket: WebSocket):
    """Windows 控制端进入远程节点的交互终端。

    优先用 paramiko `invoke_shell` 建立交互式 PTY 会话：直接复用节点已存密码
    （不向用户弹密码输入框），使「密码认证节点」的终端也能一键直连。
    仅当 paramiko 不可用或认证为密钥时，才退回 ConPTY/管道驱动 `ssh -tt`。
    """
    remote_node = node_manager.get_current_node()
    # 本机节点不应进入远端分支（防御性兜底）
    if remote_node.get("type") != "ssh":
        await _windows_terminal(websocket)
        return
    pm_reason = await _try_paramiko_interactive(websocket, remote_node)
    if pm_reason is None:
        return
    # paramiko 交互通道不可用（无 paramiko / 连接失败）→ 退回 ssh -tt
    try:
        await websocket.send_text(f"\r\n[paramiko 交互失败，回退 ssh -tt] {pm_reason}\r\n")
    except Exception:
        pass
    argv = node_manager.remote_terminal_argv(remote_node)
    cmdline = _windows_quote_argv(argv)
    try:
        if _CONPTY_AVAILABLE:
            try:
                await _windows_conpty_terminal(websocket, cmdline)
                return
            except ConPTYError:
                pass
    except Exception:
        pass
    await _windows_pipe_command_terminal(websocket, argv)


async def _try_paramiko_interactive(websocket: WebSocket, node: dict) -> str:
    """尝试用 paramiko 建立远程交互终端（invoke_shell）。

    成功时接管整个会话直到断开并返回 None；任何失败返回可读错误串。

    安全（第十四轮审计修复，High）：此前 set_missing_host_key_policy(
    AutoAddPolicy()) 对任意主机密钥无条件接受且不持久化，网络位置攻击者
    可冒充 SSH 节点收割面板存储的节点密码（MITM）。现改为 TOFU 策略
    （app.ssh_host_keys.HostKeyPolicy）：首次连接记录主机密钥指纹，
    之后密钥变更即拒绝连接。
    """
    import paramiko

    try:
        client = paramiko.SSHClient()
        # TOFU 主机密钥校验：首次记录、之后必须一致（替代无校验的 AutoAddPolicy）
        from app.ssh_host_keys import HostKeyPolicy

        client.set_missing_host_key_policy(HostKeyPolicy())
        connect_kw = {
            "hostname": str(node.get("host") or ""),
            "port": int(node.get("port") or 22),
            "username": str(node.get("user") or ""),
            "timeout": 10,
            "look_for_keys": False,
            "allow_agent": False,
        }
        if node.get("auth") == "key" and node.get("key_path"):
            connect_kw["key_filename"] = node.get("key_path")
            connect_kw["password"] = None
        else:
            connect_kw["password"] = node.get("password") or ""
        client.connect(**connect_kw)
    except Exception as e:  # noqa: BLE001 - 连接失败返回可读原因
        return str(e).strip() or "paramiko 连接失败"

    try:
        shell = client.invoke_shell(term="xterm", width=80, height=24)
        shell.settimeout(10)
        channel = shell
        loop = asyncio.get_running_loop()
        out_queue: "asyncio.Queue[bytes]" = asyncio.Queue()
        stop_flag = threading.Event()
        # 远端 shell 输出 -> WS
        def _reader():
            try:
                while not stop_flag.is_set():
                    chunk = channel.recv(4096)
                    if not chunk:
                        break
                    loop.call_soon_threadsafe(out_queue.put_nowait, chunk)
            except Exception:
                pass
            finally:
                loop.call_soon_threadsafe(out_queue.put_nowait, b"")
        threading.Thread(target=_reader, daemon=True).start()
        output_task = asyncio.create_task(_pump_output(out_queue, websocket, encoding="utf-8"))
        try:
            while True:
                data = await websocket.receive_text()
                # 会话复检（第十四轮审计修复）：改密/踢出后立即中断终端
                if not ws_session_still_valid(websocket):
                    break
                if data.startswith("\x1bRESIZE:"):
                    try:
                        _, dims = data.split(":", 1)
                        rows, cols = (int(x) for x in dims.split(","))
                        try:
                            channel.resize_pty(width=max(cols, 1), height=max(rows, 1))
                        except Exception:
                            pass
                    except Exception:
                        pass
                    continue
                try:
                    channel.send(data.encode("utf-8"))
                except Exception:
                    break
        except WebSocketDisconnect:
            pass
        finally:
            stop_flag.set()
            try:
                channel.close()
            except Exception:
                pass
            try:
                client.close()
            except Exception:
                pass
            output_task.cancel()
            try:
                await output_task
            except (asyncio.CancelledError, Exception):
                pass
        return None
    except Exception as e:  # noqa: BLE001 - 会话异常时主动关闭并返回错误
        try:
            client.close()
        except Exception:
            pass
        return str(e).strip() or "paramiko 会话异常"


async def _windows_pipe_command_terminal(websocket: WebSocket, argv: list):
    """ConPTY 不可用时的兜底：以管道方式启动任意命令（用于远端 ssh 终端）。

    与 _windows_pipe_terminal 类似，但直接 exec argv（不做 cmd /K 壳），
    以保持 ssh -tt 的交互语义。
    """
    import subprocess as _sp

    proc = _sp.Popen(
        argv,
        stdin=_sp.PIPE,
        stdout=_sp.PIPE,
        stderr=_sp.STDOUT,
        bufsize=0,
        creationflags=getattr(_sp, "CREATE_NEW_PROCESS_GROUP", 0),
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
            # 会话复检（第十四轮审计修复）：改密/踢出后立即中断终端
            if not ws_session_still_valid(websocket):
                break
            if data.startswith("\x1bRESIZE:"):
                continue
            if proc.poll() is not None:
                break
            try:
                proc.stdin.write(data.encode("utf-8"))
                proc.stdin.flush()
            except Exception:
                break
    except WebSocketDisconnect:
        # CodeQL [py/empty-except] WebSocket 正常断开：清理由 finally 完成
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
            # 会话复检（第十四轮审计修复）：改密/踢出后立即中断终端
            if not ws_session_still_valid(websocket):
                break
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
        # CodeQL [py/empty-except] WebSocket 正常断开：清理由 finally 完成
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
            # 会话复检（第十四轮审计修复）：改密/踢出后立即中断终端
            if not ws_session_still_valid(websocket):
                break
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
        # CodeQL [py/empty-except] WebSocket 正常断开：清理由 finally 完成
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
            # 会话复检（第十四轮审计修复）：改密/踢出后立即中断终端
            if not ws_session_still_valid(websocket):
                break
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
        # CodeQL [py/empty-except] WebSocket 正常断开：清理由 finally 完成
        pass
    finally:
        stop_flag.set()
        output_task.cancel()
        try:
            # CodeQL [py/empty-except] 进程可能已随连接退出，kill 失败无害
            os.kill(pid, signal.SIGTERM)
        except Exception:
            pass
        try:
            # CodeQL [py/empty-except] fd 可能已被子进程继承关闭，close 失败无害
            os.close(fd)
        except Exception:
            pass
