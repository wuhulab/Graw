# -*- coding: utf-8 -*-
"""
portforward.py - SSH 端口转发（本地直连远程服务）核心库

背景：
  本地工具（Navicat / redis-cli …）要连远程节点上的 MySQL / Redis，常规得
  开防火墙或临时跳板。本模块在「面板所在主机」上监听 127.0.0.1:LOCAL_PORT，
  收到连接后经到远程节点的 SSH 隧道（paramiko direct-tcpip）转发到
  远程 HOST:PORT——配置「远程 3306 → 本地 13306」后本地工具即可直连。

设计要点：
  - 每隧道一个守护线程 + 每连接两个泵线程；复用 node_manager 的 paramiko
    连接池（多隧道共享同一 SSH client，不重复建连）。
  - 本地监听强制 127.0.0.1（绝不暴露公网）；remote_host 白名单校验；
    端口范围 1024-65535 且不可被占用；目标节点必须是 SSH 节点。
  - 配置持久化 data/portforwards.json；enabled=true 的条目在面板启动时
    自动恢复（lifespan 调 restore_all，退出调 stop_all）。

安全：
  - 仅允许「面向远程节点的转发」，且远端目标为普通 host:port，不能转发
    面板自身所在主机的任意端口（降低横向攻击面扩展风险）。
"""

import json
import logging
import os
import re
import socket
import threading
import time
import uuid
from typing import Optional

logger = logging.getLogger("graw.portforward")

DATA_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "data"))
PF_FILE = os.path.join(DATA_DIR, "portforwards.json")

# 目标主机白名单：主机名 / IPv4 / IPv6
_HOST_RE = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.\-:]*[A-Za-z0-9])?$")
# 本地监听端口范围（排除特权与已占用检查在运行时做）
_LOCAL_PORT_MIN = 1024
_LOCAL_PORT_MAX = 65535

_lock = threading.Lock()
_pf_lock = threading.Lock()

# 运行中的隧道 registry：id -> tunnel dict
# {id, node_id, local_port, remote_host, remote_port, thread, stop, listener, stats}
_tunnels: dict = {}


# ---------------------------------------------------------------------------
# 配置存储（原子写）
# ---------------------------------------------------------------------------
def _load() -> dict:
    if not os.path.exists(PF_FILE):
        return {"version": 1, "items": []}
    try:
        with open(PF_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {"version": 1, "items": []}
    except (OSError, json.JSONDecodeError):
        return {"version": 1, "items": []}


def _save(data: dict) -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = PF_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, PF_FILE)


def list_items() -> list:
    """已配置的转发条目（不含运行态内部字段）。"""
    return list(_load().get("items", []))


# ---------------------------------------------------------------------------
# 隧道运行
# ---------------------------------------------------------------------------
def _pump(src, dst, t: dict, key: str) -> None:
    """单向搬运字节并累计流量统计。"""
    try:
        while not t["stop"].is_set():
            data = src.recv(32768)
            if not data:
                break
            dst.sendall(data)
            with _pf_lock:
                t["stats"][key] += len(data)
    except (OSError, EOFError):
        pass
    finally:
        try:
            dst.shutdown(socket.SHUT_WR)
        except OSError:
            pass


def _handle_conn(t: dict, conn: socket.socket, node: dict) -> None:
    """处理单条本地连接：建立 SSH 隧道并按需双向泵。"""
    chan = None
    try:
        from app.node_manager import _paramiko_node_key, _paramiko_pool_client

        client = _paramiko_pool_client(node, 15)
        transport = client.get_transport()
        if transport is None or not transport.is_active():
            raise ConnectionError("SSH 连接不可用")
        chan = transport.open_channel(
            "direct-tcpip",
            (t["remote_host"], t["remote_port"]),
            ("127.0.0.1", 0),
            timeout=15,
        )
        with _pf_lock:
            t["stats"]["conns"] += 1
        conn.settimeout(30)
        chan.settimeout(30)
        pump_local = threading.Thread(target=_pump, args=(conn, chan, t, "bytes_out"), daemon=True)
        pump_remote = threading.Thread(target=_pump, args=(chan, conn, t, "bytes_in"), daemon=True)
        pump_local.start()
        pump_remote.start()
        pump_local.join()
        pump_remote.join()
    except Exception as e:  # 单连接失败（目标拒绝/网络抖）只记日志，不影响监听线程
        logger.debug("隧道 %s 连接处理失败: %s", t.get("id"), e)
    finally:
        try:
            if chan is not None:
                chan.close()
        except Exception:
            pass
        try:
            conn.close()
        except Exception:
            pass


def _tunnel_loop(t: dict, node: dict) -> None:
    """隧道监听线程：accept → 每连接建 SSH 隧道。"""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        listener.bind(("127.0.0.1", t["local_port"]))
        listener.listen(8)
        listener.settimeout(1.0)
    except OSError as e:
        logger.error("隧道 %s 监听 127.0.0.1:%s 失败: %s", t.get("id"), t["local_port"], e)
        with _pf_lock:
            t["error"] = str(e)
            t["status"] = "failed"
        return
    t["listener"] = listener
    with _pf_lock:
        t["status"] = "running"
    while not t["stop"].is_set():
        try:
            conn, _addr = listener.accept()
        except socket.timeout:
            continue
        except OSError:
            break
        threading.Thread(target=_handle_conn, args=(t, conn, node), daemon=True).start()
    try:
        listener.close()
    except OSError:
        pass
    with _pf_lock:
        t["status"] = "stopped"


def start_tunnel(rec: dict) -> dict:
    """启动（或重载）一个隧道；rec 含 node_id/local_port/remote_host/remote_port。"""
    tid = rec.get("id") or uuid.uuid4().hex[:10]
    # 目标节点校验：必须存在且为 SSH 节点
    from app import node_manager

    node = node_manager.get_node(rec.get("node_id") or "")
    if not node or node.get("type") != "ssh":
        raise ValueError("目标节点不存在或不是 SSH 节点")
    host = str(rec.get("remote_host") or "").strip()
    if not host or not _HOST_RE.match(host):
        raise ValueError("remote_host 格式非法")
    local_port = int(rec.get("local_port") or 0)
    if not (_LOCAL_PORT_MIN <= local_port <= _LOCAL_PORT_MAX):
        raise ValueError("local_port 必须在 1024-65535")
    remote_port = int(rec.get("remote_port") or 0)
    if not (1 <= remote_port <= 65535):
        raise ValueError("remote_port 必须在 1-65535")
    # 本地端口占用检测（含其它隧道）
    for other in list(_tunnels.values()):
        if other.get("id") != tid and other.get("local_port") == local_port and other.get("status") == "running":
            raise ValueError(f"本地端口 {local_port} 已被其它隧道占用")
    # 可先释放同名旧隧道（幂等启动）
    stop_tunnel(tid)
    t = {
        "id": tid,
        "node_id": rec.get("node_id"),
        "node_name": node.get("name") or rec.get("node_id"),
        "local_port": local_port,
        "remote_host": host,
        "remote_port": remote_port,
        "status": "starting",
        "error": "",
        "listener": None,
        "stop": threading.Event(),
        "stats": {"conns": 0, "bytes_in": 0, "bytes_out": 0},
    }
    with _pf_lock:
        _tunnels[tid] = t
    thr = threading.Thread(target=_tunnel_loop, args=(t, node), daemon=True)
    t["thread"] = thr
    thr.start()
    # 短暂等待监听结果（bind 成功/失败即知）
    for _ in range(20):
        if t.get("status") in ("running", "failed"):
            break
        time.sleep(0.1)
    return tunnel_public(t)


def stop_tunnel(tid: str) -> bool:
    """停止隧道：通知线程退出并清理。"""
    with _pf_lock:
        t = _tunnels.get(tid)
    if not t:
        return False
    t["stop"].set()
    if t.get("thread"):
        t["thread"].join(timeout=3)
    with _pf_lock:
        _tunnels.pop(tid, None)
    return True


def tunnel_public(t: dict) -> dict:
    """对外可见的隧道状态。"""
    st = dict(t)
    st.pop("thread", None)
    st.pop("stop", None)
    st.pop("listener", None)
    return st


def list_tunnels() -> list:
    """运行中的隧道列表（含统计）。"""
    with _pf_lock:
        return [tunnel_public(t) for t in _tunnels.values()]


def get_public(tid: str) -> Optional[dict]:
    with _pf_lock:
        t = _tunnels.get(tid)
    return tunnel_public(t) if t else None


def restore_all() -> None:
    """面板启动时恢复所有 enabled=true 的转发条目。"""
    for item in _load().get("items", []):
        if not item.get("enabled"):
            continue
        try:
            start_tunnel(item)
        except Exception as e:
            logger.warning("恢复端口转发 %s 失败: %s", item.get("id"), e)


def stop_all() -> None:
    """面板退出时停止所有隧道。"""
    for tid in list(_tunnels.keys()):
        stop_tunnel(tid)