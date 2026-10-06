# -*- coding: utf-8 -*-
"""nodes.py - 多节点（多机）管理路由

提供节点的增删改查、SSH 连通性测试，以及「当前管理主机」的查询与切换。
普通用户不可访问（挂载时使用 ADMIN 依赖）；密码等敏感字段不会随列表返回。

节点资源聚合视图（GET /overview）：并发采集全部节点的 CPU/内存/磁盘/负载
指标与在线状态，本机走 psutil，SSH 子节点经单次连接脚本采集（与
routers/system.py 的远端采集同思路）；并发受限（至多 3 路并行），单节点
失败只标记 offline 不中断整批。该接口属于多节点管理边界，保持完整管理员。
"""
import asyncio
import logging
import platform
import time
from typing import Optional

import psutil
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app import node_manager
from app.hostfs import host_path

logger = logging.getLogger("graw.nodes.api")

router = APIRouter()

# 聚合采集的并发上限与单节点超时（与 batch 模块对齐：至多 3 路并行）
_OVERVIEW_CONCURRENCY = 3
_OVERVIEW_TIMEOUT = 20

# 远端一次性采集脚本：单次 SSH 连接取回 CPU/内存/磁盘/负载/主机名/运行时长，
# 避免每个指标一条命令（连接开销远大于命令本身）。各段以 ===BLOCK<key>=== 分隔。
# 注意：分段约定与 routers/system.py 的 _REMOTE_SCRIPT 一致（===BLOCK<key>=== 前缀，
# 由 _remote_overview_blocks 解析），改标记名必须两边同步。
_OVERVIEW_REMOTE_SCRIPT = r'''
echo "===BLOCKSTAT0==="; head -1 /proc/stat 2>/dev/null
sleep 0.12
echo "===BLOCKSTAT1==="; head -1 /proc/stat 2>/dev/null
echo "===BLOCKMEM==="; grep -E '^(MemTotal|MemAvailable):' /proc/meminfo 2>/dev/null
echo "===BLOCKDF==="; df -kP / 2>/dev/null | tail -1
echo "===BLOCKLOAD==="; cat /proc/loadavg 2>/dev/null
echo "===BLOCKNPROC==="; nproc 2>/dev/null
echo "===BLOCKBTIME==="; grep '^btime' /proc/stat 2>/dev/null
echo "===BLOCKHOST==="; hostname 2>/dev/null
'''


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
    # 分组 / 标签（聚合视图展示用元数据，与凭据解耦）。
    # None = 未提供（编辑时保留原值），显式空串/空数组 = 清空
    group: Optional[str] = Field(None, max_length=64)
    tags: Optional[list] = None


class NodeMetaIn(BaseModel):
    """节点元数据（分组 / 标签）更新表单，本机与 SSH 节点均可。"""

    group: str = Field("", max_length=64)
    tags: list = Field(default_factory=list)


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
            # 遗忘主机密钥失败时忽略
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


@router.post("/{node_id}/meta")
def update_node_meta(node_id: str, req: NodeMetaIn):
    """更新节点（含本机）的分组 / 标签元数据。"""
    try:
        return node_manager.update_node_meta(node_id, req.group, req.tags)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ------------------------------------------------------------
# 节点资源聚合视图
# ------------------------------------------------------------

def _parse_stat_line(line: str) -> tuple:
    """解析 /proc/stat 的 cpu 汇总行，返回 (总节拍, 空闲节拍)。"""
    parts = line.split()
    if len(parts) < 5:
        return 0, 0
    try:
        values = [int(x) for x in parts[1:]]
    except ValueError:
        return 0, 0
    return sum(values), values[3] if len(values) > 3 else 0


def _remote_overview_blocks(raw: str) -> dict:
    """把远端脚本输出按 ===BLOCK<key>=== 分段解析为 dict。"""
    blocks: dict = {}
    cur = None
    buf = []
    for line in raw.splitlines():
        if line.startswith("===BLOCK") and line.endswith("==="):
            if cur is not None:
                blocks[cur] = "\n".join(buf).strip()
            cur = line[len("===BLOCK"):-len("===")].strip()
            buf = []
        else:
            buf.append(line)
    if cur is not None:
        blocks[cur] = "\n".join(buf).strip()
    return blocks


def _local_overview() -> dict:
    """本机指标采集（psutil，跨平台；与 system.py 的本地路径一致）。"""
    cpu = psutil.cpu_percent(interval=None)
    mem = psutil.virtual_memory()
    disk_path = host_path("/") if platform.system() != "Windows" else "C:\\"
    try:
        disk = psutil.disk_usage(disk_path)
        storage = {
            "percent": round(disk.percent, 1),
            "total": disk.total,
            "used": disk.used,
            "free": disk.free,
        }
    except Exception:
        # 磁盘不可读（罕见）时不阻断整卡展示
        storage = {"percent": 0.0, "total": 0, "used": 0, "free": 0}
    try:
        load1, load5, load15 = psutil.getloadavg()
    except Exception:
        load1 = cpu / 100 * psutil.cpu_count()
        load5 = load1
        load15 = load1
    try:
        uptime = int(time.time() - psutil.boot_time())
    except Exception:
        uptime = 0
    try:
        hostname = platform.node()
    except Exception:
        hostname = ""
    return {
        "cpu": round(cpu, 1),
        "memory": {
            "percent": round(mem.percent, 1),
            "total": mem.total,
            "used": mem.used,
            "available": mem.available,
        },
        "storage": storage,
        "load": {
            "percent": round(min(100, load1 / max(1, psutil.cpu_count()) * 100), 1),
            "load1": round(load1, 2),
            "load5": round(load5, 2),
            "load15": round(load15, 2),
        },
        "uptime_seconds": uptime,
        "hostname": hostname,
    }


def _remote_overview(node_id: str) -> dict:
    """SSH 子节点指标采集：单次连接执行脚本并解析（Linux 目标）。

    由调用方保证在 node_manager.run_on_node 的节点上下文内执行。
    """
    r = node_manager.host_shell(
        _OVERVIEW_REMOTE_SCRIPT, capture_output=True, text=True, timeout=_OVERVIEW_TIMEOUT
    )
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout or "").strip()[:200] or f"exit {r.returncode}")
    blocks = _remote_overview_blocks(r.stdout or "")

    # CPU：脚本内两次采样差分
    t_pre, i_pre = _parse_stat_line(blocks.get("STAT0", ""))
    t_post, i_post = _parse_stat_line(blocks.get("STAT1", ""))
    cpu_pct = 0.0
    if t_post > t_pre:
        cpu_pct = 100.0 * (1 - (i_post - i_pre) / max(1, (t_post - t_pre)))

    # 内存
    meminfo = {}
    for line in blocks.get("MEM", "").splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        meminfo[k.strip()] = int("".join(c for c in v if c.isdigit()) or 0)
    mem_total = meminfo.get("MemTotal", 0) * 1024
    mem_avail = meminfo.get("MemAvailable", 0) * 1024
    mem_used = max(0, mem_total - mem_avail)
    mem_percent = round(mem_used / mem_total * 100, 1) if mem_total else 0.0

    # 磁盘：/（远程即根）
    storage = {"percent": 0.0, "total": 0, "used": 0, "free": 0}
    parts = blocks.get("DF", "").split()
    if len(parts) >= 5 and parts[1].isdigit():
        total_kb, used_kb, avail_kb = int(parts[1]), int(parts[2]), int(parts[3])
        storage = {
            "percent": round(used_kb / total_kb * 100, 1) if total_kb else 0.0,
            "total": total_kb * 1024,
            "used": used_kb * 1024,
            "free": avail_kb * 1024,
        }

    # 负载
    load1 = load5 = load15 = 0.0
    ld = blocks.get("LOAD", "").split()
    if len(ld) >= 3:
        try:
            load1, load5, load15 = map(float, ld[:3])
        except ValueError:
            pass
    try:
        ncores = int(blocks.get("NPROC", "").strip() or "1")
    except ValueError:
        ncores = 1

    # 运行时长：btime（开机时刻）
    btime = 0
    for line in blocks.get("BTIME", "").splitlines():
        p = line.split()
        if len(p) >= 2 and p[0] == "btime":
            try:
                btime = int(p[1])
            except ValueError:
                btime = 0
            break
    uptime = int(time.time() - btime) if btime else 0
    return {
        "cpu": round(cpu_pct, 1),
        "memory": {"percent": mem_percent, "total": mem_total, "used": mem_used, "available": mem_avail},
        "storage": storage,
        "load": {
            "percent": round(min(100, load1 / max(1, ncores) * 100), 1),
            "load1": round(load1, 2),
            "load5": round(load5, 2),
            "load15": round(load15, 2),
        },
        "uptime_seconds": uptime,
        "hostname": blocks.get("HOST", "").strip(),
    }


def _overview_one(node_id: str) -> dict:
    """采集单个节点的指标与在线状态（同步，供 to_thread 调用）。"""
    node = node_manager.get_node(node_id) or {}
    pub = next((n for n in node_manager.list_nodes() if n["id"] == node_id), {})
    base = {
        "id": node_id,
        "name": pub.get("name") or node_id,
        "type": node.get("type"),
        "host": pub.get("host", ""),
        "group": pub.get("group", ""),
        "tags": pub.get("tags", []),
        "agent_enabled": bool(pub.get("agent_enabled")),
        "online": True,
        "latency_ms": 0,
        "error": "",
    }
    if node.get("type") != "ssh":
        base.update(_local_overview())
        return base
    start = time.time()
    try:
        metrics = node_manager.run_on_node(node_id, lambda: _remote_overview(node_id))
    except Exception as e:  # 单节点失败不中断整批，标记 offline
        base["online"] = False
        base["error"] = str(e).strip()[:200] or "采集失败"
        logger.info("聚合视图 节点 %s 采集失败: %s", node_id, e)
        return base
    base["latency_ms"] = round((time.time() - start) * 1000)
    base.update(metrics)
    return base


@router.get("/overview")
async def nodes_overview():
    """节点资源聚合视图：并发采集全部节点的指标与在线状态。"""
    node_ids = [n["id"] for n in node_manager.list_nodes()]
    sem = asyncio.Semaphore(_OVERVIEW_CONCURRENCY)

    async def _one(nid: str) -> dict:
        async with sem:
            return await asyncio.to_thread(_overview_one, nid)

    results = await asyncio.gather(*(_one(nid) for nid in node_ids))
    return {"nodes": list(results), "generated_at": int(time.time() * 1000)}