from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
import asyncio
import json
import logging
import os
import platform
import re
import threading
import subprocess

from app.auth import (
    get_current_user,
    get_current_user_ws_admin,
    require_admin,
    ws_session_still_valid,
)
from app.hostfs import get_host_root
from app import node_manager
from app import auditlog
from app.routers.docker_api import get_backend, _find_podman

router = APIRouter()

logger = logging.getLogger("graw.terminal")

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


# 应用层心跳（与监控 WS 同协议，见 routers/system.py）：
# 浏览器 WebSocket 无法发送协议级 ping 帧，而长时间空闲的终端会被反向代理
# （Nginx/OpenResty 默认约 60s 空读超时）或中间 NAT 设备掐断。断连后连接
# 进入「半开」状态：前端 readyState 仍为 OPEN、send 也不报错，于是键盘输入
# 发不出去也不提示，表现为「挂久了输入不了东西」。前端每 20s 发一次心跳帧，
# 后端命中时不写入 pty、直接回 pong，前端据此判断连接是否仍真实可用。
HEARTBEAT_PING_PREFIX = '{"type":"ping"}'
HEARTBEAT_PONG = '{"type":"pong"}'


async def _consume_heartbeat(websocket: WebSocket, msg: str) -> bool:
    """识别并消费心跳帧。

    命中时回 pong（不写入 pty，避免干扰 shell），返回 True 表示调用方应
    continue；未命中返回 False。回 pong 失败（连接已断）时忽略，交由外层
    receive 循环的异常处理统一收尾。
    """
    if not msg.startswith(HEARTBEAT_PING_PREFIX):
        return False
    try:
        await websocket.send_text(HEARTBEAT_PONG)
    except Exception:  # lgtm[py/empty-except] 连接已断时回 pong 失败，交由外层 receive 循环统一收尾
        pass
    return True


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


@router.get("/persist")
async def list_persist_sessions(user=Depends(require_admin)):
    """列出当前常驻的「持久化终端」会话。

    仅返回脱敏状态（节点、创建时间、接入客户端数、缓冲字节数），不含任何
    命令输出与凭据。前端据此展示「持久终端」运行情况。
    """
    from app.tty_persist import get_manager

    return {"sessions": get_manager().list()}


@router.delete("/persist")
async def destroy_persist_session(node: str = "", user=Depends(require_admin)):
    """结束指定节点的持久化终端会话（进程随之退出）。

    前端在用户取消勾选「保留持久化终端」时调用：删除后该节点的常驻 shell
    被终止、回放缓冲丢弃，下次连接将创建全新会话。node 为空表示当前节点。
    """
    from app.tty_persist import get_manager

    nid = (node or "").strip() or node_manager.current_node_id()
    manager = get_manager()
    destroyed = manager.destroy(manager.key(nid))
    auditlog.record(
        "关闭持久终端",
        (user or {}).get("username", ""),
        "",
        nid,
    )
    return {"ok": destroyed, "node": nid}


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
            # 发送错误信息失败（连接已中断）时忽略，无需额外处理
            pass
        return
    try:
        if IS_WINDOWS:
            await _windows_conpty_terminal(websocket, command)
        else:
            await _unix_terminal(websocket)
    except WebSocketDisconnect:  # lgtm[py/empty-except] 正常断开，清理由 finally 完成
        pass
    except Exception as e:
        try:
            await websocket.send_text(f"\r\n[container terminal] {e}\r\n")
        except Exception:
            # 发送错误信息失败（连接已中断）时忽略
            pass
        try:
            await websocket.close()
        except Exception:
            # 关闭失败（连接已断开）时忽略
            pass


@router.websocket("/ws")
async def terminal_ws(
    websocket: WebSocket,
    node: str = "",
    persist: int = 0,
    user=Depends(get_current_user_ws_admin),
):
    """交互终端 WebSocket。

    persist=1 时走「持久化终端」：shell 进程常驻后端，WS 断开（刷新面板 /
    重开窗口）只摘除客户端，重连按「节点」键接回同一会话并回放最近输出，
    用于长时间任务不因刷新而中断（实现见 app/tty_persist.py）。
    """
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
        if persist:
            # 持久化终端：会话生命周期与会话连接解耦（不在此处记录普通终端审计，
            # 由 _persistent_terminal 按「创建 / 接入」分别记录，避免重连刷屏）
            await _persistent_terminal(websocket, user)
            return
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
    except WebSocketDisconnect:  # lgtm[py/empty-except] 正常断开，清理由 finally 完成
        pass
    except Exception as e:
        try:
            await websocket.send_text(f"\r\n[terminal error] {e}\r\n")
        except Exception:
            # 发送错误信息失败（连接已中断）时忽略
            pass
        try:
            await websocket.close()
        except Exception:
            # 关闭失败（连接已断开）时忽略
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
            # 读取线程异常时结束读取，由 finally 通知队列结束
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
        # 提示信息发送失败（连接已中断）时忽略，继续回退
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
        # ConPTY 启动失败等异常时忽略，回退到管道终端
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
        import socket as _socket  # noqa: PLC0415 - 就地导入，便于识别 socket.timeout

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
                    try:
                        chunk = channel.recv(4096)
                    except _socket.timeout:
                        # 空闲超时（shell 长时间无输出）≠ 断连：连接仍健康，继续等待。
                        # 此前把 timeout 当普通异常退出读取线程，导致远端输出永久断流：
                        # WS 应用层心跳照常回 pong（前端误以为连接健康），但终端从此
                        # 没有回显、远端接收窗口背压后输入也被卡死——即「挂久了/同时
                        # 开多个终端时无法输入」的直接原因。
                        continue
                    if not chunk:
                        break
                    loop.call_soon_threadsafe(out_queue.put_nowait, chunk)
            except Exception:
                # 读取线程异常或通道断开时结束读取，由 finally 通知队列结束
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
                # 应用层心跳：命中 ping 帧时回 pong，不写入远端 shell
                if await _consume_heartbeat(websocket, data):
                    continue
                if data.startswith("\x1bRESIZE:"):
                    try:
                        _, dims = data.split(":", 1)
                        rows, cols = (int(x) for x in dims.split(","))
                        try:
                            channel.resize_pty(width=max(cols, 1), height=max(rows, 1))
                        except Exception:
                            # 调整 pty 尺寸失败（通道异常）时忽略，不影响会话
                            pass
                    except Exception:
                        # 解析 RESIZE 消息失败（畸形尺寸）时忽略
                        pass
                    continue
                try:
                    channel.send(data.encode("utf-8"))
                except _socket.timeout:
                    # send 短暂超时（远端窗口背压/网络抖动）：连接可能仍健康，
                    # 直接跳过本次输入而不杀死会话，避免「多终端时偶发无法输入」
                    continue
                except Exception:
                    break
        except WebSocketDisconnect:
            # 客户端正常断开，交由 finally 统一清理
            pass
        finally:
            stop_flag.set()
            try:
                channel.close()
            except Exception:
                # 关闭 channel 失败（通道已断开）时忽略
                pass
            try:
                client.close()
            except Exception:
                # 关闭 client 失败（连接已断开）时忽略
                pass
            output_task.cancel()
            try:
                await output_task
            except (asyncio.CancelledError, Exception):
                # 输出任务被取消或异常时忽略，会话已结束
                pass
        return None
    except Exception as e:  # noqa: BLE001 - 会话异常时主动关闭并返回错误
        try:
            client.close()
        except Exception:
            # 关闭 client 失败（连接已断开）时忽略
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
            # 应用层心跳：命中 ping 帧时回 pong，不写入进程管道
            if await _consume_heartbeat(websocket, data):
                continue
            if data.startswith("\x1bRESIZE:"):
                continue
            if proc.poll() is not None:
                break
            try:
                proc.stdin.write(data.encode("utf-8"))
                proc.stdin.flush()
            except Exception:
                break
    except WebSocketDisconnect:  # lgtm[py/empty-except] 正常断开，清理由 finally 完成
        pass
    finally:
        stop_flag.set()
        try:
            proc.stdin.close()
        except Exception:
            # 关闭 stdin 失败（进程已退出）时忽略
            pass
        try:
            proc.kill()
        except Exception:
            # 进程已退出导致 kill 失败时忽略
            pass
        try:
            output_task.cancel()
        except Exception:
            # 输出任务已结束导致取消失败时忽略
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
            # 应用层心跳：命中 ping 帧时回 pong，不写入 ConPTY
            if await _consume_heartbeat(websocket, data):
                continue
            if data.startswith("\x1bRESIZE:"):
                try:
                    _, dims = data.split(":", 1)
                    rows, cols = (int(x) for x in dims.split(","))
                    pty.resize(rows, cols)
                except Exception:
                    # 解析或应用尺寸失败（畸形 RESIZE 消息）时忽略
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
    except WebSocketDisconnect:  # lgtm[py/empty-except] 正常断开，清理由 finally 完成
        pass
    finally:
        stop_flag.set()
        try:
            pty.close()
        except Exception:
            # 关闭 pty 失败（已关闭）时忽略
            pass
        output_task.cancel()
        try:
            await output_task
        except (asyncio.CancelledError, Exception):
            # 输出任务被取消或异常时忽略，会话已结束
            pass


def _windows_pipe_args(shell: str) -> list:
    """构造管道回退启动参数，参数必须与 shell 类型匹配。

    /Q /K 是 cmd.exe 专属参数；当默认 shell 检测到的是 PowerShell 时，
    直接拼接 /Q /K 会被 PowerShell 当作命令执行（报 '/Q' 不是 cmdlet），
    因此按 shell 类型分发正确参数，保证管道回退能正常开启交互 shell。
    """
    base = os.path.basename(shell).lower()
    if "power" in base or base == "pwsh.exe":
        # PowerShell-NoExit 保持会话（管道模式读取 stdin 后不退出）
        return ["-NoExit"]
    return ["/Q", "/K"]  # cmd.exe：静音启动并保持窗口不退出


async def _windows_pipe_terminal(websocket: WebSocket, shell: str):
    """Fallback: plain cmd/powershell subprocess over pipes (no echo, no PTY)."""
    proc = subprocess.Popen(
        [shell, *_windows_pipe_args(shell)],
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
            # 应用层心跳：命中 ping 帧时回 pong，不写入进程管道
            if await _consume_heartbeat(websocket, data):
                continue
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
    except WebSocketDisconnect:  # lgtm[py/empty-except] 正常断开，清理由 finally 完成
        pass
    finally:
        stop_flag.set()
        try:
            proc.stdin.close()
        except Exception:
            # 关闭 stdin 失败（进程已退出）时忽略
            pass
        try:
            proc.kill()
        except Exception:
            # 进程已退出导致 kill 失败时忽略
            pass
        try:
            output_task.cancel()
        except Exception:
            # 输出任务已结束导致取消失败时忽略
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
            # 设置 pty 窗口尺寸失败（fd 失效）时忽略，不影响会话
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
            # 应用层心跳：命中 ping 帧时回 pong，不写入 pty
            if await _consume_heartbeat(websocket, msg):
                continue
            if msg.startswith("\x1bRESIZE:"):
                try:
                    _, dims = msg.split(":", 1)
                    rows, cols = [int(x) for x in dims.split(",")]
                    set_winsize(rows, cols)
                except Exception:  # lgtm[py/empty-except] 客户端可能发送畸变尺寸，忽略即可
                    pass
                continue
            os.write(fd, msg.encode("utf-8"))
    except WebSocketDisconnect:  # lgtm[py/empty-except] 正常断开，清理由 finally 完成
        pass
    finally:
        stop_flag.set()
        output_task.cancel()
        try:
            # 进程可能已随连接退出，kill 失败无害
            os.kill(pid, signal.SIGTERM)
        except Exception:  # lgtm[py/empty-except]
            pass
        try:
            # fd 可能已被子进程继承关闭，close 失败无害
            os.close(fd)
        except Exception:  # lgtm[py/empty-except]
            pass


# ======================================================================
# 持久化终端（终端工具栏「保留持久化终端」）
#
# 与上面的一次性终端不同：这里创建的 shell 进程不随 WebSocket 断开而退出，而是
# 常驻在面板进程内（会话管理见 app/tty_persist.py）。刷新页面 / 重开窗口会接回
# 同一会话并回放最近 256KB 输出，用于「长时间任务不因刷新而中断」的场景。
# ======================================================================


def _persist_title() -> str:
    """持久会话的展示标题（用于 GET /api/terminal/persist 列表）。"""
    node = node_manager.get_current_node() or {}
    name = node.get("name") or node.get("id") or "本机"
    return f"{name} · 持久终端"


def _pty_read(fd) -> bytes:
    """阻塞读 pty 主端；子进程退出后读取会 EIO，按 EOF（b""）处理。"""
    try:
        return os.read(fd, 4096)
    except OSError:
        return b""


def _pty_resize(fd, rows: int, cols: int) -> None:
    """设置 pty 窗口尺寸（TIOCSWINSZ）；fd 已失效等异常忽略。"""
    import fcntl
    import struct
    import termios

    try:
        fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
    except Exception:
        logger.debug("持久终端设置 pty 尺寸失败", exc_info=True)


def _pty_close(pid: int, fd: int) -> None:
    """结束持久会话的子进程并关闭 pty（已退出 / 已关闭的情况一律忽略）。"""
    import signal

    try:
        os.kill(pid, signal.SIGTERM)
    except Exception:
        # 进程可能已自行退出：忽略
        pass
    try:
        os.close(fd)
    except Exception:
        # fd 可能已被子进程继承关闭：忽略
        pass
    try:
        # WNOHANG 回收僵尸进程：已退出则立即回收，未退出不阻塞
        os.waitpid(pid, os.WNOHANG)
    except Exception:
        # 非本进程子进程 / 已被回收：忽略
        pass


def _local_pty_persist_io():
    """本机 Linux/macOS 持久终端：pty.fork 出一个常驻交互 shell。"""
    import pty

    from app.tty_persist import SessionIO

    shell = os.environ.get("SHELL", "/bin/bash")
    pid, fd = pty.fork()
    if pid == 0:
        # 子进程分支：容器模式下 chroot 到宿主机根目录，让终端直接操作宿主机
        host_root = get_host_root()
        if host_root:
            try:
                os.chroot(host_root)
                os.chdir("/")
            except OSError:
                # chroot 失败（非特权）时退回容器内 shell
                pass
        try:
            os.execv(shell, [shell])
        except OSError:
            # exec 失败（shell 不存在等）时走下面的强制退出
            pass
        os._exit(1)   # execv 失败必须立刻结束子进程，绝不能继续执行父进程逻辑
    return SessionIO(
        read=lambda: _pty_read(fd),
        write=lambda data: os.write(fd, data),
        resize=lambda rows, cols: _pty_resize(fd, rows, cols),
        close=lambda: _pty_close(pid, fd),
    )


def _ssh_pty_persist_io(node: dict):
    """远程节点持久终端（Unix 控制端）：pty 承载常驻 `ssh -tt` 连接。"""
    import pty

    from app.tty_persist import SessionIO

    argv = node_manager.remote_terminal_argv(node)
    pid, fd = pty.fork()
    if pid == 0:
        try:
            os.execv(argv[0], argv)
        except OSError:
            # exec 失败（缺少 ssh 客户端等）时走下面的强制退出
            pass
        os._exit(1)   # execv 失败必须立刻结束子进程
    return SessionIO(
        read=lambda: _pty_read(fd),
        write=lambda data: os.write(fd, data),
        resize=lambda rows, cols: _pty_resize(fd, rows, cols),
        close=lambda: _pty_close(pid, fd),
    )


def _conpty_persist_io(command: str):
    """Windows 持久终端：ConPTY 承载常驻 shell 或 `ssh -tt` 命令行。"""
    from app.tty_persist import SessionIO

    console = ConPTY(rows=24, cols=80)
    console.start(command)
    return SessionIO(
        read=lambda: console.read(4096),
        write=lambda data: console.write(data),
        resize=lambda rows, cols: console.resize(rows, cols),
        close=lambda: console.close(),
    )


def _pipe_persist_io(shell: str):
    """ConPTY 不可用时的兜底持久终端：管道驱动常驻 shell（无回显，语义较弱）。

    仅作为最后退路：管道模式下 shell 处于重定向输入状态，不回显输入、不显式
    输出提示符（与一次性终端的管道回退一致），但常驻与回放能力仍然成立。
    """
    from app.tty_persist import SessionIO

    proc = subprocess.Popen(
        [shell, *_windows_pipe_args(shell)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=0,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )

    def _read_proc() -> bytes:
        try:
            if hasattr(proc.stdout, "read1"):
                return proc.stdout.read1(1024)
            return proc.stdout.read(1024)
        except Exception:
            # 进程退出 / 管道关闭：按 EOF 处理
            return b""

    def _write_proc(data: bytes) -> None:
        # 管道模式下 cmd.exe 需要 CRLF 行尾（与 _windows_pipe_terminal 一致）
        text = data.decode("utf-8", "replace")
        text = text.replace("\r", "\r\n").replace("\r\n\r\n", "\r\n")
        proc.stdin.write(text.encode("utf-8"))
        proc.stdin.flush()

    def _close_proc() -> None:
        try:
            proc.stdin.close()
        except Exception:
            # stdin 已关闭：忽略
            pass
        try:
            proc.kill()
        except Exception:
            # 进程已退出：忽略
            pass

    return SessionIO(read=_read_proc, write=_write_proc, resize=lambda rows, cols: None, close=_close_proc)


def _paramiko_persist_io(node: dict):
    """远程节点持久终端（Windows 控制端）：paramiko 交互通道。

    复用面板已存的节点凭据（无需用户再次输入密码），并沿用 TOFU 主机密钥
    校验（app.ssh_host_keys.HostKeyPolicy），避免 MITM 收割节点密码。
    """
    import paramiko

    from app.ssh_host_keys import HostKeyPolicy
    from app.tty_persist import SessionIO

    client = paramiko.SSHClient()
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
    try:
        client.connect(**connect_kw)
        channel = client.invoke_shell(term="xterm", width=80, height=24)
    except Exception:
        # 连接或开通道失败：先关掉可能已建立的 SSH 客户端再抛给上层回退
        try:
            client.close()
        except Exception:
            pass
        raise
    channel.settimeout(10)

    def _read_channel() -> bytes:
        """阻塞读通道：空闲超时（长时间无输出）≠ 断开，继续等待。

        远端正跑长任务时可能几十分钟没有输出；若把 timeout 当断开处理，
        持久会话会被误判为结束，正好违背本功能「长任务常驻」的目的。
        """
        import socket as _socket

        while True:
            try:
                return channel.recv(4096)
            except _socket.timeout:
                continue

    def _close_channel() -> None:
        try:
            channel.close()
        except Exception:
            # 通道已断开：忽略
            pass
        try:
            client.close()
        except Exception:
            # 客户端已关闭：忽略
            pass

    return SessionIO(
        read=_read_channel,
        write=lambda data: channel.sendall(data),
        resize=lambda rows, cols: channel.resize_pty(width=max(cols, 1), height=max(rows, 1)),
        close=_close_channel,
    )


def _create_persist_io():
    """按当前节点上下文与平台创建持久会话底层通道。

    分支（能力范围与一次性终端保持一致）：
      - 本机 Linux/macOS：pty.fork 常驻交互 shell（容器内可 chroot 宿主）；
      - 本机 Windows：ConPTY 常驻 PowerShell / cmd；
      - 远程节点 + Unix 控制端：pty 承载常驻 `ssh -tt`；
      - 远程节点 + Windows 控制端：优先 paramiko，失败回退 ConPTY 驱动 `ssh -tt`。
    创建失败抛 RuntimeError，由调用方回传前端展示可读原因。
    """
    if node_manager.is_remote():
        node = node_manager.get_current_node()
        if IS_WINDOWS:
            try:
                return _paramiko_persist_io(node)
            except Exception as e:  # noqa: BLE001 - 需要回退到 ssh -tt，需捕获全部异常
                logger.warning("持久终端 paramiko 通道失败，回退 ssh -tt: %s", e)
                if not _CONPTY_AVAILABLE:
                    raise RuntimeError(f"无法建立远程终端通道: {e}") from e
                cmdline = _windows_quote_argv(node_manager.remote_terminal_argv(node))
                return _conpty_persist_io(cmdline)
        return _ssh_pty_persist_io(node)
    if IS_WINDOWS:
        if _CONPTY_AVAILABLE:
            try:
                return _conpty_persist_io(_windows_shell_cmd())
            except Exception as e:  # noqa: BLE001 - 启动失败需回退管道，保证终端仍可用
                logger.warning("持久终端 ConPTY 启动失败，回退管道模式: %s", e)
        return _pipe_persist_io(_windows_shell_cmd())
    return _local_pty_persist_io()


async def _persistent_terminal(websocket: WebSocket, user: dict):
    """接入（或创建）持久化终端会话，直到本客户端断开。

    流程：
      1. 按「节点」取会话；不存在则创建常驻通道（本地 PTY / ConPTY / 远程
         ssh / paramiko）并启动进程；
      2. 接入：注册输出队列，先发控制帧（JSON，前端不写入 xterm）再回放
         最近输出快照；
      3. 双向泵：输入写进程、输出发 WS，任一方向结束即摘除客户端并关闭 WS。
         注意 WS 断开只摘客户端，**进程继续在后台运行**（本功能的核心语义），
         下次连接按同一会话键接回。
    """
    from app.tty_persist import get_manager

    manager = get_manager()
    key = manager.key(node_manager.current_node_id())
    session = manager.get(key)
    fresh = session is None
    if session is None:
        try:
            io = _create_persist_io()
        except Exception as e:  # noqa: BLE001 - 创建失败需回传可读原因给前端
            logger.warning("持久终端创建失败 key=%s: %s", key, e)
            try:
                await websocket.send_text(f"\r\n[持久终端创建失败] {e}\r\n")
                await websocket.close()
            except Exception:
                # 提示发送失败（连接已断开）时忽略
                pass
            return
        session = manager.create(key, _persist_title(), io)
        auditlog.record(
            "创建持久终端",
            (user or {}).get("username", ""),
            websocket.client.host if websocket.client else "",
            key,
        )

    qid, out_queue, replay = session.attach()
    # 鼠标注入能力（Windows 10 ConPTY 不支持）：决定输入侧是否剔除鼠标序列
    mouse_ok = _conpty_mouse_supported()[0]

    async def _pump_input() -> None:
        """输入泵：WS -> 常驻进程。"""
        try:
            while True:
                data = await websocket.receive_text()
                # 会话复检（与一次性终端一致）：改密 / 踢出后立即中断本客户端
                if not ws_session_still_valid(websocket):
                    return
                # 应用层心跳：回 pong 且不写入 pty
                if await _consume_heartbeat(websocket, data):
                    continue
                if not session.alive():
                    # 常驻进程已退出：结束本客户端，由重连创建新会话
                    return
                if data.startswith("\x1bRESIZE:"):
                    try:
                        _, dims = data.split(":", 1)
                        rows, cols = (int(x) for x in dims.split(","))
                        session.resize(rows, cols)
                    except Exception:
                        # 畸形尺寸消息（客户端可能发送异常数据）：忽略本次调整
                        pass
                    continue
                if not mouse_ok:
                    # 剔除鼠标序列；整帧都是鼠标字节时直接跳过
                    data = _MOUSE_INPUT_RE.sub("", data)
                    if not data:
                        continue
                session.write(data.encode("utf-8"))
        except WebSocketDisconnect:
            # 客户端正常断开：交由外层收尾（会话保留，进程继续跑）
            return
        except Exception:
            logger.debug("持久终端输入循环结束 key=%s", key, exc_info=True)
            return

    try:
        # 控制帧：告知前端已接入持久会话（前端据此更新状态栏，不写入终端）
        await websocket.send_text(
            json.dumps(
                {
                    "type": "persist",
                    "key": key,
                    "fresh": fresh,
                    "replay": len(replay),
                    "clients": session.client_count,
                },
                ensure_ascii=False,
            )
        )
        # 回放最近输出：刷新 / 重开窗口后立刻看到历史与长任务当前进度
        if replay:
            await websocket.send_text(replay.decode("utf-8", "replace"))
    except Exception:
        # 回放发送失败（连接已断开）：摘除客户端即可，会话与进程保留
        session.detach(qid)
        return

    output_task = asyncio.create_task(_pump_output(out_queue, websocket))
    input_task = asyncio.create_task(_pump_input())
    try:
        # 任一方向结束即收尾：输出结束 = 常驻进程退出；输入结束 = 客户端断开
        await asyncio.wait({input_task, output_task}, return_when=asyncio.FIRST_COMPLETED)
    except Exception:
        # 等待期间异常（事件循环关停等）：走统一清理
        logger.debug("持久终端会话循环异常 key=%s", key, exc_info=True)
    finally:
        # 只摘除本客户端：进程继续常驻，供下次接入（本功能的核心语义）
        session.detach(qid)
        for task in (input_task, output_task):
            if not task.done():
                task.cancel()
        await asyncio.gather(input_task, output_task, return_exceptions=True)
        try:
            await websocket.close()
        except Exception:
            # 连接可能已断开：关闭失败忽略
            pass
