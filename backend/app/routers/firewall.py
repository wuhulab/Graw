import ipaddress
import json
import os
import asyncio
import platform
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.node_manager import host_cmd


def _validate_ip(value: str) -> str:
    """校验 IP / CIDR 格式（支持 IPv4 与 IPv6）。

    ip 值最终会传给 iptables -s / netsh remoteip=，白名单校验
    可阻止畸形值破坏防火墙命令语义（如携带额外参数片段）。
    """
    try:
        # ipaddress 同时接受单 IP（1.2.3.4）与 CIDR（10.0.0.0/8）
        ipaddress.ip_network(value, strict=False)
    except ValueError:
        raise HTTPException(
            status_code=400, detail=f"IP 或 CIDR 格式非法: {value!r}"
        )
    return value

router = APIRouter()

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
FW_FILE = os.path.join(DATA_DIR, "firewall.json")
IS_WIN = platform.system() == "Windows"

# 默认屏蔽「未放行端口」时保留的重要端口（ssh/http/https/子节点 Agent 等），
# 避免误伤正常访问或把管理通道（如 Agent 的 8000）一并屏蔽导致面板失联。
# 8000 为默认 Agent 端口，若自建 Agent 换端口需在此追加。
BLOCK_PROTECTED_PORTS = {22, 80, 443, 8000}


def _listening_tcp_ports() -> set:
    """探测宿主机正在监听的 TCP 端口集合。

    优先使用 ss，失败时回退 netstat。返回值仅包含能正确解析为数字的端口，
    无法探测时返回空集合（前端随即不执行屏蔽操作，避免误伤）。
    """
    ports = set()
    for cmdline in (["ss", "-tln"], ["netstat", "-tln"]):
        try:
            r = host_cmd(cmdline, capture_output=True, text=True, timeout=10)
        except Exception:
            continue
        if r.returncode != 0 or not (r.stdout or "").strip():
            continue
        for line in (r.stdout or "").splitlines():
            parts = line.split()
            # ss: State(LISTEN) Recv-Q Send-Q Local-Address... / netstat: Proto Recv-Q Send-Q Local External
            if not parts or parts[0] != "LISTEN":
                continue
            if len(parts) < 4:
                continue
            local = parts[3]
            # Local 可能是 0.0.0.0:22 / [::]:22 / *:22，端口取最后一个冒号后的数字
            port_str = local.rsplit(":", 1)[-1]
            try:
                ports.add(int(port_str))
            except (ValueError, TypeError):
                continue
        if ports:
            return ports
    return ports


def _load_fw() -> dict:
    if not os.path.exists(FW_FILE):
        return {"enabled": True, "port_rules": [], "ip_rules": []}
    try:
        with open(FW_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"enabled": True, "port_rules": [], "ip_rules": []}


def _save_fw(data: dict):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(FW_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _iptables_cmd(args: list, timeout=10) -> tuple:
    cmd = ["iptables"] + args
    try:
        # 在宿主机环境执行 iptables（容器模式经 chroot 映射，作用于宿主 netfilter）
        r = host_cmd(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except Exception as e:
        return -1, "", str(e)


def _netsh_cmd(args: list, timeout=10) -> tuple:
    cmd = ["netsh", "advfirewall", "firewall"] + args
    try:
        r = host_cmd(cmd, capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr
    except Exception as e:
        return -1, "", str(e)


def _flush_port(port: int, proto: str = "tcp") -> None:
    """清除某个端口在本模块写入的全部 iptables 规则（幂等）。

    对同一端口多次 apply（加规则/改拒绝/重复添加）时先清空旧规则，避免旧版本
    只写了 INPUT 而遗漏 mangle 等形态残留覆盖新逻辑（如 DROP 只落 INPUT 导致
    Docker 发布端口不受影响）。
    """
    if IS_WIN:
        return
    dport = str(port)
    # iptables -D 对不存在的规则返回非零，忽略即可（幂等清除）
    for rule in [
        ["-t", "mangle", "-D", "PREROUTING", "-p", proto, "--dport", dport, "-j", "ACCEPT"],
        ["-t", "mangle", "-D", "PREROUTING", "-p", proto, "--dport", dport, "-j", "DROP"],
        ["-D", "INPUT", "-p", proto, "--dport", dport, "-j", "ACCEPT"],
        ["-D", "INPUT", "-p", proto, "--dport", dport, "-j", "DROP"],
    ]:
        _iptables_cmd(rule)


def _add_port_rule(rule: dict):
    port = rule["port"]
    proto = rule.get("protocol", "tcp")
    action = "accept" if rule.get("action", "allow") == "allow" else "block"
    name = f"graw_port_{rule['id']}"
    _flush_port(port, proto)
    if IS_WIN:
        _netsh_cmd(
            [
                "add",
                "rule",
                "name=" + name,
                "dir=in",
                f"action={action}",
                f"protocol={proto}",
                f"localport={port}",
            ]
        )
        return
    if action == "accept":
        # 放行：INPUT 覆盖宿主直连进程；mangle PREROUTING（在 nat DNAT 之前）放行
        # Docker 发布端口，且 -I 插顶可覆盖此前可能存在的高优先 DROP。
        _iptables_cmd(["-I", "INPUT", "-p", proto, "--dport", str(port), "-j", "ACCEPT"])
        _iptables_cmd(["-t", "mangle", "-I", "PREROUTING", "-p", proto, "--dport", str(port), "-j", "ACCEPT"])
    else:
        # 拒绝：Docker 发布端口在 nat PREROUTING 被 DNAT 成容器端口后走 FORWARD，
        # 不经过 INPUT；必须在 mangle PREROUTING（DNAT 之前）按宿主端口 DROP，
        # 才能真正屏蔽 Docker 发布端口的公网访问。
        _iptables_cmd(["-I", "INPUT", "-p", proto, "--dport", str(port), "-j", "DROP"])
        _iptables_cmd(["-t", "mangle", "-I", "PREROUTING", "-p", proto, "--dport", str(port), "-j", "DROP"])


def _del_port_rule(rule: dict):
    name = f"graw_port_{rule['id']}"
    if IS_WIN:
        _netsh_cmd(["delete", "rule", "name=" + name])
        return
    _flush_port(rule["port"], rule.get("protocol", "tcp"))


def _apply_all_rules(data: dict) -> int:
    """按保存记录重新应用全部端口规则（幂等）。

    用于纠正旧版本仅写 INPUT、遗漏 mangle PREROUTING 的历史规则形态，
    使 Docker 发布端口的拒绝/放行立即生效。
    """
    count = 0
    for rule in data.get("port_rules", []):
        _add_port_rule(rule)
        count += 1
    return count


def _add_ip_rule(rule: dict):
    ip = rule["ip"]
    action = "accept" if rule.get("action", "allow") == "allow" else "block"
    name = f"graw_ip_{rule['id']}"
    if IS_WIN:
        _netsh_cmd(
            [
                "add",
                "rule",
                "name=" + name,
                "dir=in",
                f"action={action}",
                "remoteip=" + ip,
            ]
        )
    else:
        act = "ACCEPT" if action == "accept" else "DROP"
        _iptables_cmd(["-A", "INPUT", "-s", ip, "-j", act])


def _del_ip_rule(rule: dict):
    name = f"graw_ip_{rule['id']}"
    if IS_WIN:
        _netsh_cmd(["delete", "rule", "name=" + name])
    else:
        _iptables_cmd(["-D", "INPUT", "-s", rule["ip"], "-j", "ACCEPT"])
        _iptables_cmd(["-D", "INPUT", "-s", rule["ip"], "-j", "DROP"])


class PortRule(BaseModel):
    port: int = Field(..., ge=1, le=65535)
    protocol: str = Field(default="tcp", pattern="^(tcp|udp)$")
    action: str = Field(default="allow", pattern="^(allow|deny)$")
    comment: Optional[str] = ""


class IpRule(BaseModel):
    ip: str = Field(..., min_length=1)
    action: str = Field(default="allow", pattern="^(allow|deny)$")
    comment: Optional[str] = ""


@router.get("/status")
async def fw_status():
    data = _load_fw()
    platform_name = "windows" if IS_WIN else "linux"
    return {"enabled": data.get("enabled", True), "platform": platform_name}


@router.get("/rules")
async def list_rules():
    return _load_fw()


@router.post("/port")
async def add_port_rule(req: PortRule):
    data = _load_fw()
    rule = {
        "id": str(uuid.uuid4())[:8],
        "port": req.port,
        "protocol": req.protocol,
        "action": req.action,
        "comment": req.comment or "",
        "created_at": datetime.now().isoformat(),
    }
    # iptables/netsh 为阻塞 subprocess，放线程池避免卡事件循环
    await asyncio.to_thread(_add_port_rule, rule)
    data["port_rules"].append(rule)
    _save_fw(data)
    return rule


@router.delete("/port/{rule_id}")
async def delete_port_rule(rule_id: str):
    data = _load_fw()
    rule = next((r for r in data.get("port_rules", []) if r["id"] == rule_id), None)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    # iptables/netsh 为阻塞 subprocess，放线程池避免卡事件循环
    await asyncio.to_thread(_del_port_rule, rule)
    data["port_rules"] = [r for r in data["port_rules"] if r["id"] != rule_id]
    _save_fw(data)
    return {"ok": True}


@router.post("/ip")
async def add_ip_rule(req: IpRule):
    # 安全校验：IP / CIDR 白名单，拒绝畸形值进入 iptables/netsh 参数
    _validate_ip(req.ip)
    data = _load_fw()
    rule = {
        "id": str(uuid.uuid4())[:8],
        "ip": req.ip,
        "action": req.action,
        "comment": req.comment or "",
        "created_at": datetime.now().isoformat(),
    }
    # iptables/netsh 为阻塞 subprocess，放线程池避免卡事件循环
    await asyncio.to_thread(_add_ip_rule, rule)
    data["ip_rules"].append(rule)
    _save_fw(data)
    return rule


@router.delete("/ip/{rule_id}")
async def delete_ip_rule(rule_id: str):
    data = _load_fw()
    rule = next((r for r in data.get("ip_rules", []) if r["id"] == rule_id), None)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    # iptables/netsh 为阻塞 subprocess，放线程池避免卡事件循环
    await asyncio.to_thread(_del_ip_rule, rule)
    data["ip_rules"] = [r for r in data["ip_rules"] if r["id"] != rule_id]
    _save_fw(data)
    return {"ok": True}


@router.post("/toggle")
async def toggle_firewall(body: dict):
    data = _load_fw()
    enabled = bool(body.get("enabled", True))
    data["enabled"] = enabled
    _save_fw(data)
    if IS_WIN:
        action = "on" if enabled else "off"
        # netsh 为阻塞 subprocess，放线程池避免卡事件循环
        await asyncio.to_thread(
            host_cmd,
            ["netsh", "advfirewall", "set", "allprofiles", "state", action],
            capture_output=True,
            timeout=10,
        )
    else:
        # Best effort: flush INPUT chain to disable (not safe); rely on JSON state for display
        pass
    return {"enabled": enabled}


@router.post("/clear")
async def clear_firewall():
    """清空全部防火墙规则（高风险操作，前端需二次确认）。

    逐条移除已登记规则对应的系统级规则后清空配置，避免残留孤儿规则。
    """
    data = _load_fw()
    removed = 0
    # iptables/netsh 为阻塞 subprocess，放线程池避免卡事件循环
    for rule in data.get("port_rules", []):
        await asyncio.to_thread(_del_port_rule, rule)
        removed += 1
    for rule in data.get("ip_rules", []):
        await asyncio.to_thread(_del_ip_rule, rule)
        removed += 1
    data["port_rules"] = []
    data["ip_rules"] = []
    _save_fw(data)
    return {"ok": True, "removed": removed}


@router.post("/reconcile")
async def reconcile_firewall():
    """按保存记录重放全部端口规则（幂等）。

    用于纠正旧版本仅写 INPUT、遗漏 mangle PREROUTING 的历史规则，使 Docker
    发布端口的屏蔽/放行立即生效。调用后无需重复保存，规则已写入 iptables。
    """
    data = _load_fw()
    # 重放规则为阻塞 subprocess（逐条 iptables/netsh），放线程池避免卡事件循环
    applied = await asyncio.to_thread(_apply_all_rules, data)
    return {"ok": True, "applied": applied}


@router.get("/listening")
async def list_listening_ports():
    """返回当前监听中的 TCP 端口及其放行/重要标记，供前端展示「未放行端口」。"""
    data = _load_fw()
    allowed = {r["port"] for r in data.get("port_rules", []) if r.get("action") == "allow"}
    # ss/netstat 为阻塞 subprocess，放线程池避免卡事件循环
    ports = await asyncio.to_thread(_listening_tcp_ports)
    items = [
        {"port": p, "allowed": p in allowed, "protected": p in BLOCK_PROTECTED_PORTS}
        for p in sorted(ports)
    ]
    return {"ports": items}


@router.post("/block-unopened")
async def block_unopened():
    """默认屏蔽所有未放行端口：对正在监听、但未加入「允许」规则、且非重要端口的
    TCP 端口统一添加 deny 规则（80/443/22 等重要端口除外）。

    已有 deny 规则或已允许的端口不会重复添加。返回本次新增的屏蔽端口。
    """
    data = _load_fw()
    existing_allow = {r["port"] for r in data.get("port_rules", []) if r.get("action") == "allow"}
    existing_deny = {r["port"] for r in data.get("port_rules", []) if r.get("action") == "deny"}
    # ss/netstat 为阻塞 subprocess，放线程池避免卡事件循环
    listening = await asyncio.to_thread(_listening_tcp_ports)

    targets = sorted(
        p for p in listening
        if p not in existing_allow and p not in existing_deny and p not in BLOCK_PROTECTED_PORTS
    )
    created = []
    for p in targets:
        rule = {
            "id": str(uuid.uuid4())[:8],
            "port": p,
            "protocol": "tcp",
            "action": "deny",
            "comment": "默认屏蔽未放行端口",
            "created_at": datetime.now().isoformat(),
        }
        await asyncio.to_thread(_add_port_rule, rule)
        data["port_rules"].append(rule)
        created.append(rule)
    _save_fw(data)

    return {
        "ok": True,
        "created": len(created),
        "ports": [c["port"] for c in created],
        "skipped_protected": sorted(p for p in listening if p in BLOCK_PROTECTED_PORTS and p not in existing_allow),
    }
