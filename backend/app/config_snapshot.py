# -*- coding: utf-8 -*-
"""
config_snapshot.py - 关键配置文件「写前快照」与一键回滚核心库

背景：
  面板高频改写配置文件（站点 nginx conf、防火墙规则 JSON），改坏想恢复是
  运维最痛的操作之一。本模块在每次写前把旧内容存为快照，对外提供
  列表 / 详情 / 回滚 / 删除原语——「回滚」即把旧内容写回原文件并触发
  对应 reload（由 rollback 路由按 kind 编排）。

设计要点：
  - 快照与业务配置「同机存储」：写在执行业务请求的主机上的
    data/config_snapshots/（站点配置在子节点时，业务在子节点执行，
    快照同样落在子节点），保证回滚写回的目标与快照同机可见。
  - 安全：snapshot_id / target_id / kind 全部白名单正则，杜绝路径穿越；
    回滚写回的 file_path 按 kind 做后缀白名单兜底（.conf / firewall.json），
    file_path 来自内部生成而非用户直接输入。
  - 容量：同一目标保留 _KEEP 份，超出删除最旧；单份快照内容上限 _MAX_BYTES，
    超限跳过并告警日志（不影响业务写配置）。
  - 线程安全：模块级锁保护读取-写入-轮转（站点与防火墙埋点并发安全）。
"""

import base64
import json
import logging
import os
import re
import threading
import uuid
from datetime import datetime
from typing import Optional

logger = logging.getLogger("graw.config_snapshot")

DATA_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "data"))
SNAP_ROOT = os.path.join(DATA_DIR, "config_snapshots")

# 合法 kind / target_id / snapshot_id（防路径穿越）
KINDS = ("site", "firewall")
_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
# 回滚写回目标的后缀白名单（路径本身来自内部生成，此处纵深兜底）
_FILE_SUFFIX = {"site": (".conf",), "firewall": ("firewall.json",)}

_KEEP = 20                 # 同一目标保留快照份数
_MAX_BYTES = 256 * 1024    # 单份快照内容上限（site conf / 防火墙 JSON 远小于此）

# 数据写锁（防止并发快照互相覆盖/轮转竞态）
_lock = threading.Lock()

# 时间戳路由非查询串白名单都走这里
_SID_FMT = "%Y%m%dT%H%M%S"


def _fresh_id() -> str:
    """生成快照 ID：本地时间戳 + 8 位随机十六进制，保证可排序且防碰撞。"""
    return datetime.now().strftime(_SID_FMT) + "_" + uuid.uuid4().hex[:8]


def _safe_id(value: str) -> str:
    """校验并返回白名单 ID（非法抛 ValueError，阻止路径穿越）。"""
    if not value or not _ID_RE.match(value):
        raise ValueError("ID 含非法字符")
    return value


def _target_dir(kind: str, target_id: str) -> str:
    """返回某一目标的快照目录（不存在则创建）。"""
    if kind not in KINDS:
        raise ValueError(f"不支持的快照类型: {kind}")
    d = os.path.join(SNAP_ROOT, kind, _safe_id(target_id))
    os.makedirs(d, exist_ok=True)
    return d


def capture_before(kind, target_id, file_path, route="", user="", ip="") -> Optional[dict]:
    """保存 file_path 当前内容为写前快照，返回快照元信息；文件不存在视为新建。

    调用时机：写配置之前调用。内部自动轮转（超出 _KEEP 删除最旧）。
    快照失败（读失败 / 超限）只记日志不抛异常——配置写入不能被快照拖垮。

    Args:
        kind: 'site' | 'firewall'
        target_id: 目标标识（站点 id 或 'firewall'）
        file_path: 该主机上「实际可访问」的文件路径（sites 已 host_path 转换）
        route:     触发来源（如 "POST /api/sites/xxx/update"），供审计回溯
        user / ip: 操作者（回滚列表展示用）
    """
    if kind not in KINDS:
        logger.warning("capture_before 忽略未知 kind=%s", kind)
        return None
    try:
        content = ""
        bytes_n = 0
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
            bytes_n = len(content.encode("utf-8", "replace"))
        if bytes_n > _MAX_BYTES:
            logger.warning("快照超限跳过 %s（%d bytes > %d）", file_path, bytes_n, _MAX_BYTES)
            return None
    except FileNotFoundError:
        # 文件还不存在（新建场景）：旧内容为空，回滚 = 删除该文件
        content = ""
        bytes_n = 0
    except OSError as e:
        logger.warning("读 %s 失败，跳过快照: %s", file_path, e)
        return None

    snap = {
        "id": _fresh_id(),
        "kind": kind,
        "target_id": target_id,
        "file_path": file_path,
        "when": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "user": user or "",
        "ip": ip or "",
        "route": route or "",
        "bytes": bytes_n,
        "content_b64": base64.b64encode(content.encode("utf-8", "replace")).decode("ascii"),
    }
    with _lock:
        d = _target_dir(kind, target_id)
        path = os.path.join(d, snap["id"] + ".json")
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(snap, f, ensure_ascii=False)
        except OSError as e:
            logger.error("写快照失败 %s: %s", path, e)
            return None
        _rotate(kind, target_id, d)
    return dict(snap)  # 返回副本，避免外部改动内部缓存


def _rotate(kind: str, target_id: str, d: str) -> None:
    """同目标超过 _KEEP 份时删除最旧（按文件名时间戳排序）。"""
    try:
        files = [f for f in os.listdir(d) if f.endswith(".json")]
        if len(files) <= _KEEP:
            return
        files.sort()
        for f in files[: len(files) - _KEEP]:
            try:
                os.remove(os.path.join(d, f))
            except OSError as e:
                logger.warning("删除旧快照失败 %s: %s", f, e)
    except OSError as e:
        logger.warning("轮转快照目录失败 %s: %s", d, e)


def _snap_path(snap_id: str) -> str:
    """按快照 ID 定位文件，并校验其落于快照根内（防穿越）。"""
    _safe_id(snap_id)
    # 文件平铺在 {kind}/{target}/{id}.json，跨 kind/target 定位需扫描——直接
    # 全量扫描（列表规模小）比维护索引简单可靠。
    real_root = os.path.realpath(SNAP_ROOT)
    for root, _dirs, files in os.walk(SNAP_ROOT):
        for fn in files:
            if fn == snap_id + ".json":
                p = os.path.realpath(os.path.join(root, fn))
                if os.path.commonpath([real_root, p]) == real_root:
                    return p
    return ""


def _load_snap(snap_id: str) -> Optional[dict]:
    """按快照 ID 读取快照内容；不存在返回 None。"""
    p = _snap_path(snap_id)
    if not p:
        return None
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except OSError as e:
        logger.error("读快照 %s 失败: %s", snap_id, e)
        return None


def list_snapshots(kind: str = "", limit: int = 100) -> list:
    """列出快照元信息（不含 content_b64），按时间倒序。

    Args:
        kind: 可选按类型过滤（'' 表示全部）
        limit: 返回条数上限
    """
    items = []
    if kind and kind not in KINDS:
        return []
    with _lock:
        for root, _dirs, files in os.walk(SNAP_ROOT):
            for fn in files:
                if not fn.endswith(".json"):
                    continue
                p = os.path.join(root, fn)
                try:
                    with open(p, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if kind and data.get("kind") != kind:
                        continue
                    items.append(
                        {
                            "id": data.get("id", ""),
                            "kind": data.get("kind", ""),
                            "target_id": data.get("target_id", ""),
                            "file_path": data.get("file_path", ""),
                            "when": data.get("when", ""),
                            "user": data.get("user", ""),
                            "route": data.get("route", ""),
                            "bytes": data.get("bytes", 0),
                        }
                    )
                except (OSError, json.JSONDecodeError) as e:
                    logger.warning("跳过损坏快照 %s: %s", p, e)
    items.sort(key=lambda x: x["when"], reverse=True)
    return items[:limit]


def get_snapshot(snap_id: str) -> Optional[dict]:
    """按 ID 取快照全量（含 content_b64，供前端预览/回滚）。"""
    return _load_snap(snap_id)


def delete_snapshot(snap_id: str) -> bool:
    """删除单条快照，成功返回 True。"""
    p = _snap_path(snap_id)
    if not p:
        return False
    try:
        os.remove(p)
        return True
    except OSError as e:
        logger.error("删除快照 %s 失败: %s", snap_id, e)
        return False


def restore_content(snap_id: str) -> Optional[dict]:
    """取出快照用于回滚，返回 {kind,target_id,file_path,content}；非法返回 None。

    返回前校验 file_path 后缀白名单（洗掉任何异常数据），并校验目标 id。
    """
    snap = _load_snap(snap_id)
    if not snap:
        return None
    kind = snap.get("kind", "")
    if kind not in KINDS:
        return None
    ids_ok = _safe_id(snap.get("target_id", ""))
    if not ids_ok:
        return None
    fp = snap.get("file_path", "")
    if not fp or not fp.endswith(_FILE_SUFFIX[kind]):
        logger.warning("回滚目标后缀非法，拒绝: %s", fp)
        return None
    try:
        content = base64.b64decode(snap.get("content_b64", "")).decode("utf-8", "replace")
    except Exception as e:  # 内容解码失败视为数据损坏
        logger.error("快照 %s 内容解码失败: %s", snap_id, e)
        return None
    return {"kind": kind, "target_id": snap.get("target_id"), "file_path": fp, "content": content}