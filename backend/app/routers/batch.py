# -*- coding: utf-8 -*-
"""
batch.py - 批量操作中心路由（仅管理员）

功能：
  1. POST /api/batch/command    - 勾选多台节点，批量执行同一 shell 命令
  2. POST /api/batch/containers - 勾选多台节点，按容器名关键字过滤后批量启/停/重启

设计要点：
  - 每节点在独立 contextvars 上下文内执行（node_manager.run_on_node），
    asyncio.to_thread 工作线程复用也不会污染并行的其它请求 / 节点。
  - 并发受限（同一批至多 3 路并行）；单节点失败返回该节点 ok:false，
    不中断整批，也不把失败抛成 HTTP 500。
  - 安全：命令长度上限、输出截断、action 枚举、节点存在性校验；
    完整审计（记录命令与每节点结果摘要，不落 stdout 敏感内容）。
  - 本路由必须在 main.py 的 _AGENT_PROXY_EXCLUDE_PREFIX 中：批量命令由
    主面板持全部节点凭据直连执行，不能把整请求转发给单一子节点。
"""

import asyncio
import json
import logging
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app import auditlog, node_manager
from app.auth import get_current_user

logger = logging.getLogger("graw.batch")

router = APIRouter()

# 限制参数（防滥用）：节点数 / 命令长度 / 输出长度 / 并发 / 超时
MAX_NODES = 16
MAX_CMD_LEN = 8000
DEFAULT_OUTPUT = 20000
MIN_OUTPUT = 1024
MAX_OUTPUT = 200000
CONCURRENCY = 3
DEFAULT_TIMEOUT = 30
DOCKER_ACTIONS = ("start", "stop", "restart")


class BatchCommandReq(BaseModel):
    """批量命令请求体：node_ids 至少一个，command 非空。"""

    node_ids: list = Field(..., min_length=1, max_length=MAX_NODES)
    command: str = Field(..., min_length=1, max_length=MAX_CMD_LEN)
    timeout: int = Field(DEFAULT_TIMEOUT, ge=1, le=300)
    max_output: int = Field(DEFAULT_OUTPUT, ge=MIN_OUTPUT, le=MAX_OUTPUT)


class ContainerFilter(BaseModel):
    """容器名关键字过滤（子串匹配，大小写不敏感）。"""

    keyword: str = Field(default="", max_length=200)


class BatchContainersReq(BaseModel):
    """批量容器动作请求体：action 仅允许 start/stop/restart。"""

    node_ids: list = Field(..., min_length=1, max_length=MAX_NODES)
    action: str = Field(..., pattern="^(start|stop|restart)$")
    filter: ContainerFilter = Field(default_factory=ContainerFilter)
    timeout: int = Field(DEFAULT_TIMEOUT, ge=1, le=300)


def _validate_nodes(node_ids: list) -> None:
    """校验节点 ID 都存在（本地/SSH 节点均可）。"""
    for nid in node_ids:
        if node_manager.get_node(nid) is None:
            raise HTTPException(status_code=400, detail=f"节点不存在: {nid}")


def _node_name(node_id: str) -> str:
    """取节点展示名称（节点缺失时回退节点 ID）。"""
    node = node_manager.get_node(node_id)
    if node is None:
        return node_id
    return node.get("name") or node_id


def _clip(text: str, limit: int) -> str:
    """截断输出到字节上限，超限附带提示（避免大输出打爆前端/网络）。"""
    if text is None:
        return ""
    s = str(text)
    if len(s) <= limit:
        return s
    return s[:limit] + f"\n…(输出已截断，共 {len(s)} 字符)"


async def _batch_command(req: BatchCommandReq) -> list:
    """并发在指定节点上执行同一条命令（并发度受信号量限制）。"""
    sem = asyncio.Semaphore(CONCURRENCY)

    def _run_one_sync(node_id: str) -> dict:
        # 同步包装：在独立 contextvars 上下文内设置请求节点再执行
        start = time.time()
        try:
            r = node_manager.run_on_node(
                node_id,
                lambda: node_manager.host_shell(
                    req.command, timeout=req.timeout, capture_output=True, text=True
                ),
            )
            return {
                "node_id": node_id,
                "node_name": _node_name(node_id),
                "ok": r.returncode == 0,
                "returncode": r.returncode,
                "stdout": _clip(r.stdout or "", req.max_output),
                "stderr": _clip(r.stderr or "", req.max_output),
                "duration": round(time.time() - start, 2),
            }
        except Exception as e:  # 单节点失败不中断整批
            logger.warning("批量命令 节点 %s 失败: %s", node_id, e)
            return {
                "node_id": node_id,
                "node_name": _node_name(node_id),
                "ok": False,
                "returncode": None,
                "stdout": "",
                "stderr": f"执行失败: {e}",
                "duration": round(time.time() - start, 2),
            }

    async def _run_wrapper(node_id: str) -> dict:
        async with sem:
            # to_thread 默认使用默认执行器；run_on_node 内部 copy_context 隔离节点
            return await asyncio.to_thread(_run_one_sync, node_id)

    return await asyncio.gather(*(_run_wrapper(n) for n in req.node_ids))


def _engine_list_containers(node_id: str, timeout: int) -> list:
    """在某节点列出全部容器 JSON 项（支持 docker 与 podman）。

    返回：[{engine:'docker', item:{...}} , ...]；节点无容器引擎时返回空列表。
    """
    out_items = []
    for engine in ("docker", "podman"):
        try:
            r = node_manager.run_on_node(
                node_id,
                lambda: node_manager.host_cmd(
                    [engine, "ps", "-a", "--format", "json"],
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                ),
            )
        except Exception as e:
            logger.debug("节点 %s 探测 %s 失败: %s", node_id, engine, e)
            continue
        if r.returncode != 0:
            continue
        raw = (r.stdout or "").strip()
        if not raw:
            continue
        # 解析：整体 JSON 数组 / 单 dict / 逐行 JSON（docker 与 podman 输出差异）
        try:
            data = json.loads(raw)
            if isinstance(data, list):
                rows = data
            elif isinstance(data, dict):
                rows = [data]
            else:
                rows = []
        except json.JSONDecodeError:
            rows = []
            for line in raw.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
        for row in rows:
            out_items.append({"engine": engine, "item": row})
        break  # 已用可用引擎，跳过另一候选
    return out_items


async def _batch_containers(req: BatchContainersReq) -> list:
    """批量容器动作：逐节点过滤后执行 start/stop/restart。"""
    sem = asyncio.Semaphore(CONCURRENCY)

    def _run_one_sync(node_id: str) -> dict:
        from app.routers.docker_api import _item_id, _item_name  # 复用引擎字段差异处理

        start = time.time()
        try:
            listed = _engine_list_containers(node_id, req.timeout)
        except Exception as e:
            logger.warning("批量容器 列表失败 节点 %s: %s", node_id, e)
            return {
                "node_id": node_id,
                "node_name": _node_name(node_id),
                "ok": False,
                "containers": [],
                "error": f"列出容器失败: {e}",
                "duration": round(time.time() - start, 2),
            }
        # 按关键字过滤（子串、大小写不敏感）
        kw = (req.filter.keyword or "").strip().lower()
        targets = []
        for entry in listed:
            cid = _item_id(entry["item"])
            name = _item_name(entry["item"])
            if not cid:
                continue
            if kw and kw not in name.lower():
                continue
            targets.append({"engine": entry["engine"], "id": cid, "name": name or cid})
        if not targets:
            return {
                "node_id": node_id,
                "node_name": _node_name(node_id),
                "ok": True,
                "containers": [],
                "note": "无匹配容器",
                "duration": round(time.time() - start, 2),
            }
        results = []
        for t in targets:
            try:
                r = node_manager.run_on_node(
                    node_id,
                    lambda: node_manager.host_cmd(
                        [t["engine"], req.action, t["id"]],
                        capture_output=True,
                        text=True,
                        timeout=req.timeout,
                    ),
                )
                results.append(
                    {
                        "id": t["id"],
                        "name": t["name"],
                        "ok": r.returncode == 0,
                        "detail": _clip((r.stderr or r.stdout or "").strip(), 2000),
                    }
                )
            except Exception as e:
                results.append({"id": t["id"], "name": t["name"], "ok": False, "detail": f"执行失败: {e}"})
        all_ok = all(x["ok"] for x in results)
        return {
            "node_id": node_id,
            "node_name": _node_name(node_id),
            "ok": all_ok,
            "containers": results,
            "duration": round(time.time() - start, 2),
        }

    async def _run_wrapper(node_id: str) -> dict:
        async with sem:
            return await asyncio.to_thread(_run_one_sync, node_id)

    return await asyncio.gather(*(_run_wrapper(n) for n in req.node_ids))


@router.post("/command")
async def batch_command(
    req: BatchCommandReq, request: Request, user: dict = Depends(get_current_user)
):
    """在多台节点批量执行同一条命令。"""
    _validate_nodes(req.node_ids)
    results = await _batch_command(req)
    # 审计：只记命令与节点集合，不落 stdout 内容（可能含敏感输出）
    username = (user or {}).get("username", "")
    ip = request.client.host if request.client else ""
    auditlog.record(
        "批量命令",
        username,
        ip,
        f"nodes={','.join(req.node_ids)} cmd={req.command[:200]}",
    )
    return {"results": results, "ok": all(r["ok"] for r in results)}


@router.post("/containers")
async def batch_containers(
    req: BatchContainersReq, request: Request, user: dict = Depends(get_current_user)
):
    """在多个节点的容器上进行批量启停/重启（按名称关键字过滤）。"""
    _validate_nodes(req.node_ids)
    results = await _batch_containers(req)
    username = (user or {}).get("username", "")
    ip = request.client.host if request.client else ""
    auditlog.record(
        "批量容器操作",
        username,
        ip,
        f"nodes={','.join(req.node_ids)} action={req.action} kw={req.filter.keyword}",
    )
    return {"results": results, "ok": all(r["ok"] for r in results)}