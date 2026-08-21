# -*- coding: utf-8 -*-
"""
node_manager.py - 多节点（多机）管理

背景：
  面板原生只管理自己所在的宿主机（本机直跑，或 Docker /host 挂载，见 hostfs）。
  多机管理让面板可以通过 SSH 远程管理其他服务器节点，并在「设置」里切换
  「当前管理的主机」。选中远程节点后，业务路由的命令/文件操作会改走 SSH；
  选中本地节点（local）时行为与改造前完全一致，不产生任何远程副作用。

设计约定：
  - 节点元数据持久化在 backend/data/nodes.json（含"当前选中节点"）。
  - 本地节点永远存在（id = "local"）；SSH 节点由管理员增删改。
  - 认证支持：密码（经 sshpass -e 注入，避免密码出现在进程参数）与
    SSH 密钥（-i key_path）。密码认证依赖控制器主机已安装 sshpass。
  - 对外统一暴露面向"当前节点"的 host_cmd / host_shell / host_which 以及
    一组文件操作原语（isfile / read_text / write_text / remove / getsize ...），
    本地时透传 hostfs（保留 HOST_ROOT chroot），远程时经 SSH 执行。
"""

import json
import os
import re
import shlex
import shutil
import subprocess
import threading
import logging
from typing import Optional

from app import hostfs

logger = logging.getLogger("graw.nodes")

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
NODES_FILE = os.path.join(DATA_DIR, "nodes.json")

# 内置本地节点 ID
LOCAL_ID = "local"

# SSH 连接默认值与超时（秒）
_DEFAULT_PORT = 22
_SSH_CONNECT_TIMEOUT = 10

# SSH 目标格式白名单：host 允许主机名 / IPv4 / IPv6（含端口形式以外的冒号），
# user 允许常规 POSIX 用户名字符。二者最终会拼入 ssh 的 argv：
#   ssh -p <port> <user>@<host> <remote_cmd>
# 若 host / user 以 "-" 开头或携带空白，会被 ssh 解析为额外选项
# （如 -oProxyCommand=...），造成 SSH 参数注入，故入口即校验。
_SSH_HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.\-:]*[A-Za-z0-9])?$")
_SSH_USER_RE = re.compile(r"^[A-Za-z0-9_]([A-Za-z0-9_.\-]*[A-Za-z0-9_\-])?$")


def _validate_ssh_target(host: str, user: str, key_path: str = "") -> None:
    """校验 SSH 节点的 host / user / key_path，阻止 ssh 参数注入。"""
    if not host or not _SSH_HOST_RE.match(host):
        raise ValueError("host 格式非法：仅允许主机名 / IP 字符，且不能以 - 开头")
    if not user or not _SSH_USER_RE.match(user):
        raise ValueError("user 格式非法：仅允许字母 / 数字 / _ . -，且不能以 - 开头")
    if key_path and ("\r" in key_path or "\n" in key_path or "\x00" in key_path):
        raise ValueError("key_path 含非法字符")

_lock = threading.RLock()
# 内存缓存，避免每次调用都读文件
_store_cache: Optional[dict] = None

# 控制器主机本地是否安装了 sshpass（密码认证首选方式），惰性探测
_sshpass_checked = False
_sshpass_available = False
# paramiko 是否可用（sshpass 缺位时的密码认证兜底，纯 Python 跨平台）
_paramiko_checked = False
_paramiko_available = False


# ------------------------------------------------------------
# 存储读写（线程安全 + 内存缓存）
# ------------------------------------------------------------
def _default_store() -> dict:
    """默认存储结构：仅包含本地节点。"""
    return {
        "version": 1,
        "current": LOCAL_ID,
        "nodes": {
            LOCAL_ID: {"id": LOCAL_ID, "name": "本机", "type": "local"},
        },
    }


def _get_store() -> dict:
    """读取节点存储（带内存缓存）。"""
    global _store_cache
    with _lock:
        if _store_cache is not None:
            return _store_cache
        if not os.path.exists(NODES_FILE):
            _store_cache = _default_store()
            return _store_cache
        try:
            with open(NODES_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            if (
                isinstance(d, dict)
                and isinstance(d.get("nodes"), dict)
                and LOCAL_ID in d["nodes"]
            ):
                d.setdefault("current", LOCAL_ID)
                d.setdefault("version", 1)
                _store_cache = d
                return d
        except Exception:
            logger.warning("nodes.json 读取失败，回退默认存储", exc_info=True)
        _store_cache = _default_store()
        return _store_cache


def _save_store(store: dict) -> None:
    """保存节点存储（原子写 + 更新内存缓存）。"""
    global _store_cache
    with _lock:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = NODES_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(store, f, ensure_ascii=False, indent=2)
        os.replace(tmp, NODES_FILE)
        _store_cache = store


# ------------------------------------------------------------
# 节点基础信息
# ------------------------------------------------------------
def list_nodes() -> list:
    """返回所有节点（脱敏，绝不返回密码）。"""
    store = _get_store()
    out = []
    for nid, node in store["nodes"].items():
        pub = {"id": nid, "name": node.get("name") or nid, "type": node.get("type")}
        if node.get("type") == "ssh":
            pub.update(
                {
                    "host": node.get("host") or "",
                    "port": node.get("port") or _DEFAULT_PORT,
                    "user": node.get("user") or "",
                    "auth": node.get("auth") or "password",
                    # 只回传是否配置了凭据，不回传真实密钥/密码
                    "has_password": bool(node.get("password")),
                    "key_path": node.get("key_path") or "",
                }
            )
        out.append(pub)
    return out


def get_node(node_id: str) -> Optional[dict]:
    """按 ID 取节点原始配置（含密码，仅供内部使用）。"""
    if not node_id:
        return None
    return _get_store()["nodes"].get(node_id)


def get_current_node() -> dict:
    """返回当前选中的节点（始终存在）。"""
    store = _get_store()
    nid = store.get("current") or LOCAL_ID
    node = store["nodes"].get(nid)
    if node is None:
        node = store["nodes"].get(LOCAL_ID)
    return node


def current_node_id() -> str:
    """返回当前选中节点的 ID。"""
    return get_current_node().get("id", LOCAL_ID)


def is_remote() -> bool:
    """当前管理的主机是否为远程（非本地）节点。"""
    return get_current_node().get("type") == "ssh"


def set_current(node_id: str) -> dict:
    """切换当前管理的主机。返回切换后的节点脱敏信息。"""
    store = _get_store()
    if node_id not in store["nodes"]:
        raise ValueError(f"节点不存在: {node_id}")
    store["current"] = node_id
    _save_store(store)
    logger.info("切换当前管理主机 -> %s", node_id)
    return next((node for node in list_nodes() if node["id"] == node_id), None)


def upsert_ssh_node(node: dict) -> dict:
    """新增或更新一个 SSH 节点。

    入参（均为可脱敏字段）：
      id         可选；新增时后台生成。
      name / host / port / user / auth ("password"|"key")
      password   认证方式为 password 时使用（更新时可留空表示保持原值）
      key_path   认证方式为 key 时使用
    返回保存后的节点脱敏信息。
    """
    store = _get_store()
    node_id = (node.get("id") or "").strip() or ("node_" + os.urandom(4).hex())
    existing = store["nodes"].get(node_id) or {}

    cleaned = {
        "id": node_id,
        "name": (node.get("name") or "").strip() or node_id,
        "host": (node.get("host") or "").strip(),
        "port": int(node.get("port") or _DEFAULT_PORT),
        "user": (node.get("user") or "").strip(),
        "auth": "key" if node.get("auth") == "key" else "password",
        "key_path": (node.get("key_path") or "").strip(),
        "type": "ssh",
    }
    # 密码：仅当显式传入非空时才更新；留空则保留旧值（便于编辑时不重输密码）
    new_password = (node.get("password") or "").strip()
    if new_password:
        cleaned["password"] = new_password
    else:
        cleaned["password"] = existing.get("password", "")

    # 基础字段校验
    if not cleaned["host"] or not cleaned["user"]:
        raise ValueError("host 与 user 不能为空")
    # SSH 参数注入防护：host/user 以 - 开头或携带空白会被 ssh 解析为选项
    _validate_ssh_target(cleaned["host"], cleaned["user"], cleaned["key_path"])
    if cleaned["auth"] == "key" and not cleaned["key_path"]:
        raise ValueError("密钥认证必须提供 key_path")

    store["nodes"][node_id] = cleaned
    _save_store(store)
    logger.info("已保存 SSH 节点 %s (%s@%s)", node_id, cleaned["user"], cleaned["host"])
    return next((n for n in list_nodes() if n["id"] == node_id), None)


def delete_node(node_id: str) -> bool:
    """删除一个 SSH 节点。本地节点不可删除；删除当前节点时自动回落到本地。"""
    if node_id == LOCAL_ID:
        raise ValueError("本地节点不可删除")
    store = _get_store()
    if node_id not in store["nodes"]:
        return False
    del store["nodes"][node_id]
    if store.get("current") == node_id:
        store["current"] = LOCAL_ID
        logger.info("当前主机节点被删除，回落到本机")
    _save_store(store)
    return True


# ------------------------------------------------------------
# SSH 执行底层
# ------------------------------------------------------------
def _sshpass_ok() -> bool:
    """sshpass 是否可用（密码认证的前置条件），结果缓存。"""
    global _sshpass_checked, _sshpass_available
    if not _sshpass_checked:
        _sshpass_checked = True
        _sshpass_available = shutil.which("sshpass") is not None
        if not _sshpass_available:
            logger.warning("控制器主机未安装 sshpass，将回退到 paramiko 做密码认证")
    return _sshpass_available


def _paramiko_ok() -> bool:
    """paramiko 是否可用（sshpass 缺位时密码认证的兜底），结果缓存。"""
    global _paramiko_checked, _paramiko_available
    if not _paramiko_checked:
        _paramiko_checked = True
        try:
            import paramiko  # noqa: F401

            _paramiko_available = True
        except Exception:
            _paramiko_available = False
    return _paramiko_available


def _paramiko_run(node: dict, remote_cmd: str, **kwargs) -> subprocess.CompletedProcess:
    """用 paramiko 在远程节点执行一条命令，返回与 subprocess.CompletedProcess 兼容结果。

    仅当控制器缺少 sshpass 时作为密码认证的兜底（paramiko 为纯 Python、跨平台，
    解决 Windows 控制器无法安装 sshpass 导致的「密码认证节点无法连接」问题）。
    密钥认证仍优先走系统 ssh（保留退出码/信号语义与交互终端）。

    兼容参数：timeout（秒）、text（输出按 UTF-8 解码为 str）、input（写往远端 stdin）。
    """
    import paramiko

    timeout = kwargs.get("timeout") or (_SSH_CONNECT_TIMEOUT + 15)
    text = bool(kwargs.get("text", False))
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    connect_kw = {
        "hostname": str(node.get("host") or ""),
        "port": int(node.get("port") or _DEFAULT_PORT),
        "username": str(node.get("user") or ""),
        "timeout": timeout,
        # 只使用显式凭据，避免扫描本机 ~/.ssh 或调用 agent
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
        stdin, stdout, stderr = client.exec_command(remote_cmd, timeout=timeout)
        # 命令执行读超时保护，避免远端卡死导致面板请求挂起
        stdout.channel.settimeout(timeout)
        input_data = kwargs.get("input")
        if input_data is not None:
            stdin.write(input_data.encode("utf-8") if isinstance(input_data, str) else input_data)
            try:
                stdin.channel.shutdown_write()
            except Exception:
                pass
        out = stdout.read()
        errb = stderr.read()
        code = stdout.channel.recv_exit_status()
    finally:
        try:
            client.close()
        except Exception:
            pass
    if text:
        out = out.decode("utf-8", "replace")
        errb = errb.decode("utf-8", "replace")
    return subprocess.CompletedProcess([], code, out, errb)


def _paramiko_connect_test(node: dict) -> dict:
    """用 paramiko 测试节点 SSH 连通性，返回 {"ok": bool, "message": str}。"""
    try:
        _paramiko_run(node, "echo ok", timeout=_SSH_CONNECT_TIMEOUT + 5, text=True)
        return {"ok": True, "message": "ok"}
    except Exception as e:  # noqa: BLE001 - 连接失败要向上抛可读信息
        return {"ok": False, "message": str(e).strip() or "连接失败"}


def _ssh_argv(node: dict, remote_cmd: str) -> tuple:
    """构造 ssh 命令的 argv 与所需环境。

    返回 (argv, env_extra)。env_extra 为需注入的额外环境变量 dict（密码时携带
    SSHPASS），否则为空 dict。
    """
    port = int(node.get("port") or _DEFAULT_PORT)
    target = f"{node.get('user')}@{node.get('host')}"
    auth = node.get("auth", "password")

    base = [
        "ssh",
        "-p",
        str(port),
        # 自动接受首次连接的主机指纹，避免交互卡住
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        f"ConnectTimeout={_SSH_CONNECT_TIMEOUT}",
        "-o",
        "NumberOfPasswordPrompts=1",
    ]
    env_extra: dict = {}
    use_sshpass = False

    if auth == "key":
        base += ["-i", node.get("key_path") or "", "-o", "BatchMode=yes"]
    else:
        # 密码认证：用 sshpass -e 注入（密码放环境变量，不出现在命令行）
        use_sshpass = True
        env_extra["SSHPASS"] = node.get("password") or ""

    cmd = base + [target, remote_cmd]
    if use_sshpass:
        cmd = ["sshpass", "-e"] + cmd
    return cmd, env_extra


def _run_ssh(node: dict, remote_cmd: str, **kwargs) -> subprocess.CompletedProcess:
    """在当前远程节点上执行一条远程命令（保持与 subprocess.run 一致语义）。

    密码认证优先用 sshpass（若控制器已安装）；否则回退 paramiko（跨平台）。
    两者皆不可用时返回 exit=127 的可读错误，绝不抛异常。
    """
    if node.get("auth") != "key":
        if not _sshpass_ok():
            if _paramiko_ok():
                return _paramiko_run(node, remote_cmd, **kwargs)
            return subprocess.CompletedProcess(
                [], 127, b"", b"Controller has neither sshpass nor paramiko for password auth"
            )
    argv, env_extra = _ssh_argv(node, remote_cmd)
    env = None
    if env_extra:
        env = dict(os.environ)
        env.update(env_extra)
    try:
        return subprocess.run(argv, env=env, **kwargs)
    except FileNotFoundError as e:
        return subprocess.CompletedProcess(argv, 127, b"", str(e).encode())


# ------------------------------------------------------------
# 当前节点感知的命令执行（业务路由统一入口）
# ------------------------------------------------------------
def host_cmd(args, **kwargs) -> subprocess.CompletedProcess:
    """在当前管理主机上执行一条命令（argv 形式）。

    本地节点 -> 透传 hostfs.host_cmd（保留 HOST_ROOT 容器挂载语义）；
    远程节点 -> 将各参数按 shell 语义转义后经 ssh 执行（等价于无 shell 的 argv）。
    """
    node = get_current_node()
    if node.get("type") != "ssh":
        return hostfs.host_cmd(list(args), **kwargs)
    # 远程：把 argv 拼成一条安全的远程 shell 命令
    remote_cmd = " ".join(shlex.quote(str(a)) for a in args)
    return _run_ssh(node, remote_cmd, **kwargs)


def host_shell(command: str, **kwargs) -> subprocess.CompletedProcess:
    """在当前管理主机上以 shell 形式执行一条命令字符串。"""
    node = get_current_node()
    if node.get("type") != "ssh":
        return hostfs.host_shell(command, **kwargs)
    return _run_ssh(node, command, **kwargs)


def host_which(cmd: str) -> Optional[str]:
    """在当前管理主机上探测命令是否存在（返回固定占位路径或 None）。

    SSH 远程节点无法轻易返回绝对路径，仅在存在时返回占位 "/usr/bin/<cmd>"，
    None 表示不存在。
    """
    node = get_current_node()
    if node.get("type") != "ssh":
        return hostfs.host_which(cmd)
    r = _run_ssh(node, f"command -v {shlex.quote(cmd)} || true", capture_output=True, text=True, timeout=_SSH_CONNECT_TIMEOUT + 5)
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip().splitlines()[0]
    return None


def host_path(path: str) -> str:
    """宿主机视角路径 -> 实际可访问路径。

    本地节点按 hostfs 映射；远程节点路径即远程绝对路径，原样返回。
    """
    node = get_current_node()
    if node.get("type") != "ssh":
        return hostfs.host_path(path)
    return path


def unhost_path(real_path: str) -> str:
    node = get_current_node()
    if node.get("type") != "ssh":
        return hostfs.unhost_path(real_path)
    return real_path


# ------------------------------------------------------------
# 当前节点感知的文件操作原语
# ------------------------------------------------------------
def isfile(path: str) -> bool:
    """判断当前管理主机上 path 是否为文件。"""
    if not is_remote():
        return os.path.isfile(hostfs.host_path(path))
    r = host_shell(
        f"test -f {shlex.quote(path)} && echo 1 || echo 0",
        capture_output=True,
        text=True,
        timeout=_SSH_CONNECT_TIMEOUT + 5,
    )
    return r.stdout.strip() == "1"


def exists(path: str) -> bool:
    if not is_remote():
        return os.path.exists(hostfs.host_path(path))
    r = host_shell(
        f"test -e {shlex.quote(path)} && echo 1 || echo 0",
        capture_output=True,
        text=True,
        timeout=_SSH_CONNECT_TIMEOUT + 5,
    )
    return r.stdout.strip() == "1"


def getsize(path: str) -> int:
    if not is_remote():
        return os.path.getsize(hostfs.host_path(path))
    r = host_shell(
        f"stat -c %s {shlex.quote(path)} 2>/dev/null || echo 0",
        capture_output=True,
        text=True,
        timeout=_SSH_CONNECT_TIMEOUT + 5,
    )
    try:
        return int(r.stdout.strip().splitlines()[-1])
    except Exception:
        return 0


def read_text(path: str, errors="replace") -> str:
    """读取当前管理主机上文本文件内容。"""
    if not is_remote():
        with open(hostfs.host_path(path), "r", encoding="utf-8", errors=errors) as f:
            return f.read()
    r = host_shell(
        f"cat {shlex.quote(path)} 2>/dev/null || true",
        capture_output=True,
        text=True,
        timeout=_SSH_CONNECT_TIMEOUT + 15,
    )
    return r.stdout or ""


def read_bytes(path: str) -> bytes:
    """读取当前管理主机上二进制文件内容。"""
    if not is_remote():
        with open(hostfs.host_path(path), "rb") as f:
            return f.read()
    r = host_shell(
        f"cat {shlex.quote(path)} 2>/dev/null || true",
        capture_output=True,
        timeout=_SSH_CONNECT_TIMEOUT + 30,
    )
    return r.stdout


def write_text(path: str, content: str) -> None:
    """把字符串写到当前管理主机的文件（覆盖）。"""
    if not is_remote():
        os.makedirs(os.path.dirname(hostfs.host_path(path)) or ".", exist_ok=True)
        with open(hostfs.host_path(path), "w", encoding="utf-8") as f:
            f.write(content)
        return
    # 远程：把内容经 ssh stdin 喂给远程 cat > file
    host_shell(
        f"mkdir -p {shlex.quote(os.path.dirname(path) or '.')} && cat > {shlex.quote(path)}",
        input=content,
        timeout=_SSH_CONNECT_TIMEOUT + 30,
    )


def write_bytes(path: str, data: bytes) -> None:
    """把字节写到当前管理主机的文件（覆盖）。"""
    if not is_remote():
        os.makedirs(os.path.dirname(hostfs.host_path(path)) or ".", exist_ok=True)
        with open(hostfs.host_path(path), "wb") as f:
            f.write(data)
        return
    host_shell(
        f"mkdir -p {shlex.quote(os.path.dirname(path) or '.')} && cat > {shlex.quote(path)}",
        input=data,
        timeout=_SSH_CONNECT_TIMEOUT + 30,
    )


def remove(path: str) -> None:
    """删除当前管理主机上的文件/目录。"""
    if not is_remote():
        real = hostfs.host_path(path)
        if os.path.isdir(real) and not os.path.islink(real):
            shutil.rmtree(real, ignore_errors=True)
        else:
            try:
                os.remove(real)
            except OSError:
                pass
        return
    # 远程：rm -rf（仅给出明确路径，路径部分上层已做安全校验）
    host_shell(f"rm -rf {shlex.quote(path)}", timeout=_SSH_CONNECT_TIMEOUT + 15)


def connect_test(node: dict) -> dict:
    """测试与某个节点的 SSH 连通性。返回 {"ok": bool, "message": str}。"""
    probe = node if node.get("type") == "ssh" else None
    if probe is None:
        return {"ok": True, "message": "local"}
    if probe.get("auth") != "key":
        if not _sshpass_ok():
            # sshpass 缺失：回退 paramiko 做密码认证（跨平台，Windows 控制器可直用）
            if _paramiko_ok():
                return _paramiko_connect_test(probe)
            return {"ok": False, "message": "控制器主机既未安装 sshpass，也未安装 paramiko，无法进行密码认证"}
    argv, env_extra = _ssh_argv(probe, "echo ok")
    env = None
    if env_extra:
        env = dict(os.environ)
        env.update(env_extra)
    try:
        r = subprocess.run(argv, env=env, capture_output=True, timeout=_SSH_CONNECT_TIMEOUT + 5)
        if r.returncode == 0:
            return {"ok": True, "message": "ok"}
        err = (r.stderr or r.stdout or "").decode("utf-8", "replace").strip()
        return {"ok": False, "message": err or f"连接失败(exit {r.returncode})"}
    except FileNotFoundError as e:
        return {"ok": False, "message": str(e)}
    except subprocess.TimeoutExpired:
        return {"ok": False, "message": "连接超时"}


def remote_terminal_argv(node: dict) -> list:
    """构造进入远程节点的交互终端 exec argv（强制分配 TTY）。

    用于 WebSocket 终端的子进程 exec。密码认证节点在终端中仍由用户手动输入
    密码（先于登录面板时配置的密码），因此这里不包 sshpass；密钥认证则可无缝直连。
    """
    port = int(node.get("port") or _DEFAULT_PORT)
    target = f"{node.get('user')}@{node.get('host')}"
    argv = [
        "ssh",
        "-tt",  # 强制伪终端，避免远程非交互环境缺失 tty
        "-p",
        str(port),
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        f"ConnectTimeout={_SSH_CONNECT_TIMEOUT}",
    ]
    if node.get("auth") == "key" and node.get("key_path"):
        argv += ["-i", node.get("key_path"), "-o", "BatchMode=yes"]
    argv += [target]
    return argv