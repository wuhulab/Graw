# -*- coding: utf-8 -*-
"""
rollback.py - 配置快照 / 一键回滚 REST 路由（仅管理员）

功能：
  1. GET    /api/rollback           - 快照列表（可按 kind 过滤，不含内容）
  2. GET    /api/rollback/{id}      - 快照详情（含内容 base64，供前端预览）
  3. POST   /api/rollback/{id}/restore - 一键回滚：写回旧内容 + 按类型触发 reload
  4. DELETE /api/rollback/{id}      - 删除某条快照

回滚编排（restore）：
  - kind=site      ：写回原 conf 文件 → webserver.reload()（nginx/openresty）
  - kind=firewall  ：写回 firewall.json → firewall._apply_all_rules() 重算规则
  全程写审计日志（auditlog.record），失败记 logger.error 并返回 5xx。

安全：
  - 快照 id / 目标后缀白名单校验在 config_snapshot.restore_content 内完成，
    路由层只做编排与错误兜底；
  - 本路由挂 ADMIN 依赖（main.py 注册），回滚语义与配置修改一致。
"""

import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from app import auditlog, config_snapshot
from app.auth import get_current_user

logger = logging.getLogger("graw.rollback")

router = APIRouter()


def _client_ip(request: Request) -> str:
    """取请求来源 IP（端口转发/反向代理场景下取直连地址即可）。"""
    return request.client.host if request.client else ""


@router.get("")
def list_snapshots(kind: str = "", limit: int = 100):
    """快照列表：可按 kind 过滤，返回元信息（不含内容）。"""
    try:
        limit = max(1, min(int(limit), 500))
    except (TypeError, ValueError):
        limit = 100
    items = config_snapshot.list_snapshots(kind=kind, limit=limit)
    return {"items": items, "total": len(items)}


@router.get("/{s_id}")
def get_snapshot(s_id: str):
    """快照详情：含 content_b64，供前端内容预览 / diff。"""
    snap = config_snapshot.get_snapshot(s_id)
    if not snap:
        raise HTTPException(status_code=404, detail="快照不存在")
    return {
        "id": snap["id"],
        "kind": snap["kind"],
        "target_id": snap["target_id"],
        "file_path": snap["file_path"],
        "when": snap["when"],
        "user": snap["user"],
        "route": snap["route"],
        "bytes": snap["bytes"],
        "content_b64": snap["content_b64"],
    }


@router.post("/{s_id}/restore")
def restore(s_id: str, request: Request, user: dict = Depends(get_current_user)):
    """一键回滚：写回快照内容并触发对应 reload。"""
    data = config_snapshot.restore_content(s_id)
    if not data:
        raise HTTPException(status_code=404, detail="快照不存在或内容非法")
    kind = data["kind"]
    file_path = data["file_path"]
    content = data["content"]
    username = user.get("username") if user else ""
    ip = _client_ip(request)

    # 写回文件：快照内容来自同机旧配置，直接覆盖写回
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(content)
    except OSError as e:
        logger.error("回滚写回失败 %s: %s", file_path, e)
        raise HTTPException(status_code=500, detail=f"写回配置失败: {e}")

    # 按 kind 触发 reload / 规则重算（函数内 import 避免路由导入期耦合）
    try:
        if kind == "site":
            from app import webserver

            webserver.reload()
        elif kind == "firewall":
            import json

            try:
                restored = json.loads(content or "{}")
            except json.JSONDecodeError:
                restored = {}
            from app.routers import firewall

            firewall._apply_all_rules(restored)
    except Exception as e:  # reload 失败不吞：配置已写回但未生效，返回可读错误
        logger.error("回滚后 reload 失败 kind=%s: %s", kind, e)
        raise HTTPException(status_code=500, detail=f"配置已写回，但 reload 失败: {e}")

    auditlog.record("回滚配置", username, ip, f"kind={kind} target={data['target_id']} file={file_path}")
    return {"ok": True, "kind": kind, "target_id": data["target_id"], "file_path": file_path}


@router.delete("/{s_id}")
def delete(s_id: str, request: Request, user: dict = Depends(get_current_user)):
    """删除单条快照（释放空间）。"""
    if not config_snapshot.delete_snapshot(s_id):
        raise HTTPException(status_code=404, detail="快照不存在")
    username = user.get("username") if user else ""
    auditlog.record("删除配置快照", username, _client_ip(request), f"id={s_id}")
    return {"ok": True}