# -*- coding: utf-8 -*-
"""
ssh_host_keys.py - SSH 主机密钥 TOFU 校验（第十四轮审计修复）

背景
----
routers/terminal.py 的远程终端此前使用 paramiko.AutoAddPolicy()：
主机密钥未知时【无条件接受且不持久化】——连 OpenSSH 的 known_hosts
（Trust-On-First-Use）都没有。网络位置攻击者（ARP/DNS 劫持）在
「面板 -> 远程 SSH 节点」路径上冒充节点，出示任意主机密钥即可通过
校验，收割面板中存储的节点 SSH 密码并劫持终端会话。

本模块提供与 OpenSSH known_hosts 一致的 TOFU 语义：
  - 首次连接某 host:port：记录其主机密钥指纹并放行（trust on first use）
  - 再次连接：指纹必须一致，否则拒绝连接（密钥变更 = 可能 MITM）
指纹持久化于 data/ssh_known_hosts.json（0600，与 data/ 其它敏感文件同级）。

兼容性：不改变既有部署体验——首次连接仍可直连（自动记录），
与旧行为唯一差别是「此后节点密钥若被更换将拒绝连接并提示」。
"""
import base64
import hashlib
import json
import os
import threading
from typing import Optional

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
KNOWN_HOSTS_FILE = os.path.join(DATA_DIR, "ssh_known_hosts.json")

_lock = threading.RLock()  # 可重入锁：check_or_remember/forget 持锁期间调用 _save（内部再次加锁）不会死锁


def _load() -> dict:
    """读取已记录的主机密钥指纹表（host:port -> sha256 base64）。"""
    if not os.path.exists(KNOWN_HOSTS_FILE):
        return {}
    try:
        with open(KNOWN_HOSTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(data: dict) -> None:
    """原子写入 + 收紧权限（Linux 0600）。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    with _lock:
        tmp = KNOWN_HOSTS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, KNOWN_HOSTS_FILE)
    try:
        os.chmod(KNOWN_HOSTS_FILE, 0o600)
    except OSError:
        pass  # Windows 上 chmod 无实际效果，忽略


def _key_fingerprint(key) -> str:
    """计算主机密钥指纹：SHA256(raw) 的 base64（与 ssh-keygen -lf 同风格）。"""
    try:
        raw = key.asbytes()  # paramiko PKey
    except AttributeError:
        # 兜底：未实现 asbytes 的兼容对象用字符串表示
        raw = str(key).encode("utf-8", errors="replace")
    digest = hashlib.sha256(raw).digest()
    return base64.b64encode(digest).decode("ascii").rstrip("=")


def check_or_remember(host_port: str, key) -> Optional[str]:
    """TOFU 校验一个主机密钥。

    返回 None 表示放行（首次记录，或指纹一致）；
    返回非 None 字符串为拒绝原因（指纹不一致——疑似中间人攻击）。
    """
    hp = (host_port or "").strip()
    if not hp:
        return "缺少主机标识（host:port）"
    fp = _key_fingerprint(key)
    known = _load()
    with _lock:
        prev = known.get(hp)
        if prev is None:
            # 首次连接：记录指纹（TOFU）并放行
            known[hp] = fp
            _save(known)
            return None
    if prev != fp:
        return (
            f"主机 {hp} 的 SSH 主机密钥已变更（拒绝连接，疑似中间人攻击）。"
            f"如确认节点重装/换钥，请在面板「SSH 密钥」中清除该节点记录后重试。"
        )
    return None


def forget(host_port: str) -> None:
    """删除某主机的主机密钥记录（节点重装/换钥后由管理员调用）。"""
    hp = (host_port or "").strip()
    if not hp:
        return
    with _lock:
        known = _load()
        if hp in known:
            del known[hp]
            _save(known)


class HostKeyPolicy:
    """paramiko MissingHostKeyPolicy 实现：TOFU（首次记录，之后必须一致）。

    用法（替代 AutoAddPolicy / RejectPolicy）：
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(HostKeyPolicy())
    """

    def missing_host_key(self, client, hostname, key):
        reason = check_or_remember(hostname, key)
        if reason:
            import paramiko

            raise paramiko.SSHException(reason)
