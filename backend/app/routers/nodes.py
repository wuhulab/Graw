# -*- coding: utf-8 -*-
"""nodes.py - 多节点（多机）管理路由

提供节点的增删改查、SSH 连通性测试，以及「当前管理主机」的查询与切换。
普通用户不可访问（挂载时使用 ADMIN 依赖）；密码等敏感字段不会随列表返回。
"""
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app import node_manager

logger = logging.getLogger("graw.nodes.api")

router = APIRouter()


class SSHNodeIn(BaseModel):
    """新增 / 编辑 SSH 节点表单。"""
    id: Optional[str] = None
    name: str = Field("", max_length=64)
    host: str = Field(..., min_length=1, max_length=255)
    port: int = Field(22, ge=1, le=65535)
    user: str = Field(..., min_length=1, max_length=64)
    auth: str = Field("password", pattern="^(password|key)$")
    password: Optional[str] = Field("", max_length=200)
    key_path: Optional[str] = Field("", max_length=1024)
    # Agent（子节点 API）：可选配置，用于让主面板经由 SSH 隧道回调子节点 Graw
    agent_port: Optional[int] = Field(8000, ge=1, le=65535)
    agent_key: Optional[str] = Field("", max_length=256)
    agent_secret: Optional[str] = Field("", max_length=256)
    # agent_enabled 显式标记是否启用（False 时清除 key/secret）
    agent_enabled: Optional[bool] = None


class CurrentIn(BaseModel):
    node_id: str = Field(..., min_length=1)


@router.get("")
def list_nodes():
    """返回所有节点（脱敏）与当前选中节点。"""
    return {"nodes": node_manager.list_nodes(), "current": node_manager.current_node_id()}


@router.get("/current")
def get_current():
    """返回当前管理的主机。"""
    node = node_manager.get_current_node()
    nid = node_manager.current_node_id()
    if node.get("type") == "ssh":
        info = {
            "id": node.get("id"),
            "name": node.get("name"),
            "type": "ssh",
            "host": node.get("host"),
            "host_display": f"{node.get('user')}@{node.get('host')}:{node.get('port')}",
        }
    else:
        info = {"id": nid, "name": node.get("name"), "type": "local"}
    return {"current": nid, "node": info}


@router.post("")
def create_node(req: SSHNodeIn):
    """新增一个 SSH 节点。"""
    try:
        created = node_manager.upsert_ssh_node(req.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return created


@router.put("/{node_id}")
def update_node(node_id: str, req: SSHNodeIn):
    """更新一个 SSH 节点（password 留空表示保持原密码）。"""
    payload = req.model_dump()
    payload["id"] = node_id
    try:
        updated = node_manager.upsert_ssh_node(payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return updated


@router.delete("/{node_id}")
def delete_node(node_id: str):
    """删除一个 SSH 节点（本地节点不可删除）。"""
    node = node_manager.get_node(node_id)
    try:
        ok = node_manager.delete_node(node_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not ok:
        raise HTTPException(status_code=404, detail="节点不存在")
    # 清理该节点的主机密钥 TOFU 记录（第十四轮审计配套）：节点重装/换钥后，
    # 删除并重新添加节点即可重建信任，不被旧的 known_hosts 指纹拦截
    if node and node.get("type") == "ssh" and node.get("host"):
        try:
            from app.ssh_host_keys import forget
            forget(f"{node['host']}:{node.get('port') or 22}")
        except Exception:
            pass
    return {"ok": True}


@router.post("/{node_id}/test")
def test_node(node_id: str):
    """测试与某个节点的 SSH 连通性。"""
    node = node_manager.get_node(node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="节点不存在")
    result = node_manager.connect_test(node)
    return {"node_id": node_id, **result}


@router.post("/current")
def set_current(body: CurrentIn):
    """切换当前管理的主机。"""
    node = node_manager.get_node(body.node_id)
    if node is None:
        raise HTTPException(status_code=404, detail="节点不存在")
    try:
        info = node_manager.set_current(body.node_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"current": body.node_id, "node": info}