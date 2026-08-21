# -*- coding: utf-8 -*-
"""
agent_client.py - 主面板访问子节点 Agent API 的客户端（走 SSH 反代隧道）

背景：
  架构「子节点跑完整 Graw」。主面板想让全部应用优先从子节点 Agent 取数，
  但子节点 Agent 不应暴露公网端口。因此主面板复用 SSH 连接的 paramiko
  transport，用 `direct-tcpip` 隧道把请求送进子节点的 Agent 端口（如 8000），
  期间在隧道上跑 HTTP，并带上「成对密钥』换取的 JWT。

流程：
  1. 取回/复用连接到子节点的 paramiko client（见 node_manager 连接池）。
  2. direct-tcpip 建立到 `127.0.0.1:<agent_port>` 的隧道通道。
  3. 在通道上发送 HTTP/1.0 请求（Connection: close，读到 EOF 即响应结束）。
  4. 首次访问前用成对密钥向子节点 /api/agent/issue 换取 JWT，并缓存到临近过期。
  5. 透传业务请求时附加 Bearer JWT。

安全：
  - 全程走既有 SSH 加密隧道，子节点 Agent 不需暴露公网端口。
  - JWT 角色由子节点环境变量决定（GRAW_AGENT_ROLE），防越权。
"""
import contextlib
import hashlib
import hmac
import io
import json
import secrets
import time
from typing import Optional

from app import node_manager

# 默认子节点 Graw 监听端口（agent 模式与本地面板同源）
DEFAULT_AGENT_PORT = 8000
# JWT 提前续期余量（秒）：剩余不足则重新换取
TOKEN_RENEW_MARGIN = 300
# 每节点缓存的 agent token：{node_key: {"exp":..,"token":..}}
_token_cache: dict = {}
_token_lock = None
import threading

_token_lock = threading.Lock()


def _sig(secret: str, key: str, ts: int, nonce: str) -> str:
    """与子节点 agent_auth._compute_sig 完全一致的签名。"""
    msg = f"{key}|{ts}|{nonce}".encode("utf-8")
    return hmac.new(secret.encode("utf-8"), msg, hashlib.sha256).hexdigest()


def _node_agent_cfg(node: dict) -> dict:
    """提取节点上配置的 agent 参数；缺省时返回空字典（未启用）。"""
    return {
        "port": int(node.get("agent_port") or DEFAULT_AGENT_PORT),
        "key": (node.get("agent_key") or "").strip(),
        "secret": (node.get("agent_secret") or "").strip(),
    }


def agent_ready(node: dict) -> bool:
    """当前节点是否已配置 Agent 访问（key+secret 齐全且为 SSH 节点）。"""
    if not node or node.get("type") != "ssh":
        return False
    cfg = _node_agent_cfg(node)
    return bool(cfg["key"] and cfg["secret"])


def _open_tunnel_channel(client, dst_port: int):
    """在既有 SSH client 的 transport 上打通到子节点 agent 端口的通道。"""
    transport = client.get_transport()
    if transport is None or not transport.is_active():
        raise ConnectionError("SSH 连接不可用")
    # 隧道目的：子节点本地回环 agent 端口（复用同一 SSH 会话，不新建连接）
    chan = transport.open_channel(
        "direct-tcpip",
        ("127.0.0.1", dst_port),
        ("127.0.0.1", 0),
        timeout=15,
    )
    return chan


def _http_over_channel(chan, method: str, path: str, headers: dict, body: Optional[bytes] = None) -> dict:
    """在隧道通道上发起一次 HTTP/1.0 请求，返回 {status, headers, body}。"""
    host_header = "localhost"
    req_line = f"{method} {path} HTTP/1.0\r\n"
    req_headers = ["Host: " + host_header, "Connection: close"]
    if body:
        req_headers.append("Content-Length: " + str(len(body)))
    for k, v in headers.items():
        req_headers.append(f"{k}: {v}")
    req = req_line + "\r\n".join(req_headers) + "\r\n\r\n"
    chan.sendall(req.encode("utf-8") + (body or b""))

    raw = b""
    try:
        while True:
            chunk = chan.recv(65536)
            if not chunk:
                break
            raw += chunk
            # 收到完整头后可结束（HTTP/1.0 + Connection: close 读到 EOF）
    finally:
        try:
            chan.close()
        except Exception:
            pass

    # 拆分响应头与 body
    head, sep, resp_body = raw.partition(b"\r\n\r\n")
    if not sep:
        return {"status": 500, "headers": {}, "body": raw}
    head_lines = head.decode("utf-8", "replace").split("\r\n")
    status_line = head_lines[0] if head_lines else ""
    parts = status_line.split(" ", 2)
    status = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    headers_out = {}
    for line in head_lines[1:]:
        if ":" in line:
            k, v = line.split(":", 1)
            headers_out[k.strip().lower()] = v.strip()
    return {"status": status, "headers": headers_out, "body": resp_body}


def _ensure_token(node: dict, client) -> str:
    """换取（或复用缓存的）子节点 JWT。"""
    species = _node_agent_cfg(node)
    if not species["key"] or not species["secret"]:
        raise ValueError("该节点未配置 Agent 访问密钥")

    key = node_manager._paramiko_node_key(node)
    now = time.time()
    with _token_lock:
        cached = _token_cache.get(key)
        if cached and cached.get("exp", 0) - TOKEN_RENEW_MARGIN > now:
            return cached["token"]

        ts = int(now)
        nonce = secrets.token_urlsafe(8)
        sig = _sig(species["secret"], species["key"], ts, nonce)
        chan = None
        try:
            chan = _open_tunnel_channel(client, species["port"])
            path = f"/api/agent/issue?ts={ts}&nonce={nonce}&sig={sig}"
            resp = _http_over_channel(chan, "GET", path, {"X-Graw-Agent-Key": species["key"]})
            data = json.loads((resp["body"] or b"{}").decode("utf-8", "replace")) if resp["body"] else {}
            if resp["status"] != 200 or not data.get("token"):
                raise ConnectionError(f"Agent 换取 token 失败: {resp['status']} {resp['body'][:200]!r}")
            token = data["token"]
            token_exp = data.get("expires_in", 86400 * 7)
            _token_cache[key] = {"exp": now + token_exp, "token": token}
            return token
        finally:
            try:
                if chan is not None:
                    chan.close()
            except Exception:
                pass


def agent_proxy(node: dict, method: str, path: str, headers: dict, body: Optional[bytes] = None) -> dict:
    """主面板向当前节点的 Agent 转发一次业务请求（经由隧道）。"""
    # 取回/复用连接池中的 SSH client
    timeout = 30
    client = node_manager._paramiko_pool_client(node, timeout) if node_manager._paramiko_ok() else None
    if client is None:
        raise ConnectionError("paramiko 不可用，无法建立 Agent 隧道")
    token = _ensure_token(node, client)
    species = _node_agent_cfg(node)
    auth_headers = dict(headers or {})
    auth_headers["Authorization"] = f"Bearer {token}"
    chan = _open_tunnel_channel(client, species["port"])
    return _http_over_channel(chan, method, path, auth_headers, body=body)