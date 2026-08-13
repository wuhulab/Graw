import json
import os
import platform
import subprocess
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.hostfs import host_cmd

router = APIRouter()

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
FW_FILE = os.path.join(DATA_DIR, "firewall.json")
IS_WIN = platform.system() == "Windows"


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


def _add_port_rule(rule: dict):
    port = rule["port"]
    proto = rule.get("protocol", "tcp")
    action = "accept" if rule.get("action", "allow") == "allow" else "block"
    name = f"graw_port_{rule['id']}"
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
    else:
        act = "ACCEPT" if action == "accept" else "DROP"
        _iptables_cmd(["-A", "INPUT", "-p", proto, "--dport", str(port), "-j", act])


def _del_port_rule(rule: dict):
    name = f"graw_port_{rule['id']}"
    if IS_WIN:
        _netsh_cmd(["delete", "rule", "name=" + name])
    else:
        _iptables_cmd(
            [
                "-D",
                "INPUT",
                "-p",
                rule.get("protocol", "tcp"),
                "--dport",
                str(rule["port"]),
                "-j",
                "ACCEPT",
            ]
        )
        _iptables_cmd(
            [
                "-D",
                "INPUT",
                "-p",
                rule.get("protocol", "tcp"),
                "--dport",
                str(rule["port"]),
                "-j",
                "DROP",
            ]
        )


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
    _add_port_rule(rule)
    data["port_rules"].append(rule)
    _save_fw(data)
    return rule


@router.delete("/port/{rule_id}")
async def delete_port_rule(rule_id: str):
    data = _load_fw()
    rule = next((r for r in data.get("port_rules", []) if r["id"] == rule_id), None)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    _del_port_rule(rule)
    data["port_rules"] = [r for r in data["port_rules"] if r["id"] != rule_id]
    _save_fw(data)
    return {"ok": True}


@router.post("/ip")
async def add_ip_rule(req: IpRule):
    data = _load_fw()
    rule = {
        "id": str(uuid.uuid4())[:8],
        "ip": req.ip,
        "action": req.action,
        "comment": req.comment or "",
        "created_at": datetime.now().isoformat(),
    }
    _add_ip_rule(rule)
    data["ip_rules"].append(rule)
    _save_fw(data)
    return rule


@router.delete("/ip/{rule_id}")
async def delete_ip_rule(rule_id: str):
    data = _load_fw()
    rule = next((r for r in data.get("ip_rules", []) if r["id"] == rule_id), None)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    _del_ip_rule(rule)
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
        host_cmd(
            ["netsh", "advfirewall", "set", "allprofiles", "state", action],
            capture_output=True,
            timeout=10,
        )
    else:
        # Best effort: flush INPUT chain to disable (not safe); rely on JSON state for display
        pass
    return {"enabled": enabled}
