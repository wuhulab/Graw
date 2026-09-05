# -*- coding: utf-8 -*-
"""
portforward.py（路由） - SSH 端口转发 REST 接口（仅管理员）

功能：
  GET    /api/portforward        - 已配置条目（持久化的端口转发清单）
  GET    /api/portforward/running - 运行中隧道状态（含流量统计）
  POST   /api/portforward        - 创建并立即启用一个隧道
  POST   /api/portforward/{id}/toggle - 启/停运行（同步持久化 enabled 状态）
  DELETE /api/portforward/{id}   - 删除条目并停止隧道

安全：
  - 全量 ADMIN；id 白名单防穿越；入库前按 portforward 库校验
    （node 为 SSH 节点、remote_host 白名单、端口范围、占用检测）。
"""

import logging
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app import portforward
from app.auth import get_current_user

logger = logging.getLogger("graw.portforward")

router = APIRouter()

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


class PortForwardReq(BaseModel):
    """创建转发请求：node_id 为 SSH 节点；local_port 本地监听；remote 为目标。"""

    name: str = Field(default="", max_length=64)
    node_id: str = Field(..., min_length=1, max_length=64)
    local_port: int = Field(..., ge=1024, le=65535)
    remote_host: str = Field(..., min_length=1, max_length=255)
    remote_port: int = Field(..., ge=1, le=65535)


def _persist_items() -> None:
    """把运行中的隧道回写到持久化文件（保持 enabled 语义）。"""
    running_ids = {t["id"] for t in portforward.list_tunnels()}
    data = portforward._load()
    for item in data.get("items", []):
        item["enabled"] = item.get("id") in running_ids
    portforward._save(data)


@router.get("")
def list_items():
    """已配置条目列表（含 enabled 状态）。"""
    return {"items": portforward.list_items()}


@router.get("/running")
def list_running():
    """运行中隧道状态（含连接数与流量）。"""
    return {"tunnels": portforward.list_tunnels()}


@router.post("")
def create(req: PortForwardReq):
    """创建转发：先启动（校验），成功后再持久化。"""
    rec = {
        "id": uuid.uuid4().hex[:10],
        "name": req.name.strip()[:64],
        "node_id": req.node_id.strip(),
        "local_port": req.local_port,
        "remote_host": req.remote_host.strip(),
        "remote_port": req.remote_port,
        "enabled": True,
    }
    try:
        pub = portforward.start_tunnel(rec)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    data = portforward._load()
    data.setdefault("items", []).append(rec)
    portforward._save(data)
    return {"ok": True, "tunnel": pub}


@router.post("/{tid}/toggle")
def toggle(tid: str):
    """启/停隧道（同步持久化 enabled 状态）。"""
    if not _ID_RE.match(tid or ""):
        raise HTTPException(status_code=400, detail="非法的隧道 ID")
    data = portforward._load()
    item = next((i for i in data.get("items", []) if i.get("id") == tid), None)
    if not item:
        raise HTTPException(status_code=404, detail="转发条目不存在")
    running = portforward.get_public(tid)
    if running:
        portforward.stop_tunnel(tid)
        item["enabled"] = False
    else:
        try:
            portforward.start_tunnel(item)
            item["enabled"] = True
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    portforward._save(data)
    return {"ok": True, "enabled": item["enabled"]}


@router.delete("/{tid}")
def remove(tid: str):
    """删除条目并停止对应隧道。"""
    if not _ID_RE.match(tid or ""):
        raise HTTPException(status_code=400, detail="非法的隧道 ID")
    data = portforward._load()
    items = [i for i in data.get("items", []) if i.get("id") != tid]
    if len(items) == len(data.get("items", [])):
        raise HTTPException(status_code=404, detail="转发条目不存在")
    data["items"] = items
    portforward._save(data)
    portforward.stop_tunnel(tid)
    return {"ok": True}