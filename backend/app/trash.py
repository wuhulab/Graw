# -*- coding: utf-8 -*-
"""
trash.py - 回收站核心逻辑（非路由）

背景：
  文件管理删除的文件默认被「移入回收站」而不是直接删除，避免误删。
  回收站能力由三个要素构成：
    1. 面板级配置（backend/data/recycle.json）：启用开关、自动删除开关、保留天数；
    2. 每节点回收站目录：位于被管理主机本机（与源文件同一文件系统，才能用 rename
       实现近乎瞬时的「移动」，跨节点/跨文件系统则退化失败而不静默删源）；
    3. 回收站元信息（<回收站目录>/.graw_trash.json）：记录每个条目的原路径、
       删除时间与删除者，供恢复/清理使用。

  目录位置（宿主机视角）：
    - 远程 SSH 节点：/var/lib/graw-trash
    - 本地 Linux（含 Docker /host 挂载）：/var/lib/graw-trash
    - 本地 Windows：%LOCALAPPDATA%/graw-trash

  所有路径参数均为「宿主机视角」路径（与 files.py 一致），实际 I/O 经
  node_manager 原语完成本地/远程两态映射，因此回收站天然支持多机管理。

安全约定：
  - 元信息中的 trash 路径必须落在回收站目录内，否则拒绝（防元信息被篡改后
    读取/删除任意路径）；
  - 恢复目标 original 为面板数据目录（data/）时拒绝（文件管理本身禁止删除
    data 目录内容，理论不可达，作纵深防御）；
  - 配置写入采用「临时文件 + os.replace」原子写。
"""
import asyncio
import json
import logging
import os
import platform
import shlex
import shutil
import threading
import time
import uuid

from app import node_manager

logger = logging.getLogger("graw.trash")

# 面板数据目录（存放回收站开关配置）
DATA_DIR = os.path.normpath(
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))
)
CFG_FILE = os.path.join(DATA_DIR, "recycle.json")

# 回收站目录固定名称（宿主机视角）
REMOTE_TRASH_ROOT = "/var/lib/graw-trash"

# 后台自动清理 tick 间隔（秒）：每小时扫一遍所有节点已过期条目
PURGE_TICK_SECONDS = 3600

# 元信息文件名（位于回收站目录内，随节点走）
META_NAME = ".graw_trash.json"

_lock = threading.Lock()
_purge_task = None


# ---------------------------------------------------------------------------
# 配置读写（面板级，与节点无关）
# ---------------------------------------------------------------------------
def _default_cfg() -> dict:
    return {"enabled": True, "auto_delete": True, "auto_delete_days": 30}


def load_cfg() -> dict:
    """读取回收站配置；文件缺失/损坏时回退默认值。"""
    if not os.path.exists(CFG_FILE):
        return _default_cfg()
    try:
        with open(CFG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return _default_cfg()
        days = int(data.get("auto_delete_days") or 30)
        return {
            "enabled": bool(data.get("enabled", True)),
            "auto_delete": bool(data.get("auto_delete", True)),
            "auto_delete_days": max(1, min(365, days)),
        }
    except Exception as e:
        logger.warning("读取 recycle.json 失败，按默认配置处理: %s", e)
        return _default_cfg()


def save_cfg(enabled: bool, auto_delete: bool, auto_delete_days: int) -> dict:
    """原子写入回收站配置；天数限制在 1-365。"""
    cfg = {
        "enabled": bool(enabled),
        "auto_delete": bool(auto_delete),
        "auto_delete_days": max(1, min(365, int(auto_delete_days))),
    }
    with _lock:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = CFG_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CFG_FILE)
    logger.info("回收站配置已更新: enabled=%s auto_delete=%s days=%s",
                cfg["enabled"], cfg["auto_delete"], cfg["auto_delete_days"])
    return cfg


def is_enabled() -> bool:
    """回收站是否启用（删除走回收站的前提）。"""
    return load_cfg().get("enabled", True)


# ---------------------------------------------------------------------------
# 回收站目录定位与工具
# ---------------------------------------------------------------------------
def trash_root() -> str:
    """返回当前管理主机上回收站目录的「宿主机视角」路径。"""
    if node_manager.is_remote():
        return REMOTE_TRASH_ROOT
    if platform.system() == "Windows":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        return os.path.join(base, "graw-trash")
    return REMOTE_TRASH_ROOT


def trash_root_safe() -> str:
    """规范化回收站根目录（去除尾部分隔符），用于路径包含性判断。"""
    return os.path.normpath(trash_root())


def path_in_trash(path: str) -> bool:
    """判断宿主机视角路径是否位于回收站目录内（防递归回收/越权读写）。"""
    try:
        p = os.path.normcase(os.path.normpath(path))
        base = os.path.normcase(trash_root_safe())
        return os.path.commonpath([p, base]) == base and p != base
    except ValueError:
        # 跨盘符（Windows）：与回收站目录不可能同根
        return False


# ---------------------------------------------------------------------------
# 元信息读写（<回收站目录>/.graw_trash.json，存于被管理主机上）
# ---------------------------------------------------------------------------
def _meta_path() -> str:
    """回收站元信息文件（宿主机视角）。"""
    return os.path.join(trash_root(), META_NAME)


def _load_items() -> list:
    """读取当前节点回收站条目；文件缺失/损坏时返回空列表。"""
    meta = _meta_path()
    try:
        if not node_manager.isfile(meta):
            return []
        text = node_manager.read_text(meta, errors="replace")
        data = json.loads(text or "{}")
    except Exception as e:
        # 元信息损坏不应让整个回收站不可用：只保留无法解析的提示
        logger.warning("读取回收站元信息失败 %s: %s", meta, e)
        return []
    items = data.get("items", [])
    return [i for i in items if isinstance(i, dict)]


def _save_items(items: list) -> None:
    """原子写入（宿主机视角）回收站元信息。"""
    meta = _meta_path()
    payload = json.dumps({"version": 1, "items": items}, ensure_ascii=False)
    node_manager.write_text(meta, payload)


def _find_by_trash(items: list, trash: str) -> dict:
    """按 trash 路径精确查找条目（带路径归一，忽略末尾分隔符差异）。"""
    target = os.path.normpath(trash)
    for it in items:
        if os.path.normpath(str(it.get("trash", ""))) == target:
            return it
    return None


# ---------------------------------------------------------------------------
# 底层移动/删除原语（本地 rename / 远程 mv，与 files.py rename 同一模式）
# ---------------------------------------------------------------------------
def _ensure_trash_dir() -> str:
    """确保当前节点回收站目录存在，返回其「宿主机视角」路径。

    目录创建失败（权限不足等）时抛 OSError，由上层决定是否降级为物理删除。
    """
    root = trash_root()
    real = node_manager.host_path(root)
    if node_manager.is_remote():
        node_manager.host_shell(f"mkdir -p {shlex.quote(real)}", timeout=30)
    else:
        os.makedirs(real, exist_ok=True)
    return root


def _move(src_host_view: str, dst_host_view: str, timeout: int = 3600) -> None:
    """把源（宿主机视角）移动到目标（宿主机视角）。

    本地用 shutil.move（同文件系统下是原子 rename，跨设备自动退化拷贝+删除）；
    远程经 SSH `mv` 执行（回收站目录与源文件同在被管理主机，同文件系统）。
    """
    src = node_manager.host_path(src_host_view)
    dst = node_manager.host_path(dst_host_view)
    if node_manager.is_remote():
        node_manager.host_shell(
            f"mv {shlex.quote(src)} {shlex.quote(dst)}", timeout=timeout
        )
    else:
        shutil.move(src, dst)


def _rm_path(path_host_view: str) -> None:
    """永久删除当前节点上的文件/目录（宿主机视角），不经过回收站。"""
    path = node_manager.host_path(path_host_view)
    if node_manager.is_remote():
        node_manager.host_shell(f"rm -rf {shlex.quote(path)}", timeout=3600)
        return
    if os.path.isdir(path) and not os.path.islink(path):
        shutil.rmtree(path, ignore_errors=True)
    else:
        try:
            os.remove(path)
        except OSError:
            pass


def _unique_trash_dst(root: str, src_path: str) -> str:
    """在回收站目录内为源文件生成不冲突的目标路径（同名加 (2)、(3)…）。"""
    name = os.path.basename(os.path.normpath(src_path)).strip() or "untitled"
    stem, ext = os.path.splitext(name)
    cand = os.path.join(root, name)
    if not node_manager.exists(cand):
        return cand
    for i in range(2, 1000):
        cand = os.path.join(root, f"{stem} ({i}){ext}")
        if not node_manager.exists(cand):
            return cand
    # 理论上不会到达：同名条目超过 999 个时直接加时间戳后缀保证唯一
    cand = os.path.join(root, f"{stem}-{int(time.time())}{ext}")
    return cand if not node_manager.exists(cand) else os.path.join(root, f"graw-{uuid.uuid4().hex[:8]}-{name}")


# ---------------------------------------------------------------------------
# 对外操作
# ---------------------------------------------------------------------------
def move_to_trash(src_host_view: str, username: str) -> dict:
    """把文件/目录移入当前节点回收站，返回条目信息。

    源必须存在（上层 files.py 已验证）；失败（回收站目录不可写等）时抛异常，
    不会静默物理删除——宁可让删除操作报错，也不丢失用户数据。
    """
    root = _ensure_trash_dir()
    dst = _unique_trash_dst(root, src_host_view)
    with _lock:
        items = _load_items()
        _move(src_host_view, dst)
        item = {
            "id": uuid.uuid4().hex,
            "original": os.path.normpath(src_host_view),
            "trash": os.path.normpath(dst),
            "deleted_at": int(time.time()),
            "deleted_by": username or "unknown",
        }
        items.append(item)
        _save_items(items)
    return item


def list_items() -> list:
    """返回当前节点回收站条目（按删除时间倒序）。"""
    items = _load_items()
    items.sort(key=lambda i: int(i.get("deleted_at") or 0), reverse=True)
    return items


def restore_item(trash_path: str, username: str) -> dict:
    """把回收站条目恢复到原位置。

    原路径父目录已不存在 / 原路径位于面板数据目录时拒绝，返回带 reason 的错误；
    正常恢复返回 {"ok": True}。
    """
    trash = os.path.normpath(trash_path)
    if not path_in_trash(trash):
        raise ValueError("非法回收站路径")
    with _lock:
        items = _load_items()
        item = _find_by_trash(items, trash)
        if not item:
            raise FileNotFoundError("回收站条目不存在")
        original = os.path.normpath(str(item.get("original", "")))
        # 纵深防御：原路径不允许落在面板数据目录内
        from app.routers.files import _is_forbidden  # noqa 局部导入避免循环 import

        if not original or _is_forbidden(original):
            raise ValueError("该条目无法恢复（原位置受保护）")
        parent = os.path.dirname(original)
        if not node_manager.exists(parent):
            raise FileNotFoundError("原位置目录不存在")
        if node_manager.exists(original):
            raise FileExistsError("原位置已存在同名文件")
        _move(trash, original)
        items = [i for i in items if i.get("trash") != trash]
        _save_items(items)
    return {"ok": True, "original": original}


def delete_item(trash_path: str) -> dict:
    """彻底删除回收站中的单一条目（不可恢复）。"""
    trash = os.path.normpath(trash_path)
    if not path_in_trash(trash):
        raise ValueError("非法回收站路径")
    with _lock:
        items = _load_items()
        item = _find_by_trash(items, trash)
        if not item:
            raise FileNotFoundError("回收站条目不存在")
        _rm_path(trash)
        items = [i for i in items if i.get("trash") != trash]
        _save_items(items)
    return {"ok": True}


def empty_trash() -> int:
    """清空当前节点回收站，返回清理条目数。"""
    with _lock:
        items = _load_items()
        n = len(items)
        for it in items:
            try:
                trash = os.path.normpath(str(it.get("trash", "")))
                if path_in_trash(trash):
                    _rm_path(trash)
            except Exception as e:
                logger.warning("清空回收站时删除 %s 失败: %s", it.get("trash"), e)
        _save_items([])
    if n:
        logger.info("已清空回收站，共 %d 条", n)
    return n


def purge_expired(days=None) -> int:
    """清理当前节点回收站中已过期的条目，返回清理条数。

    仅当配置开启「到期自动删除」时才生效；days 缺省取配置值。
    """
    cfg = load_cfg()
    if not cfg.get("auto_delete"):
        return 0
    days = days or int(cfg.get("auto_delete_days") or 30)
    deadline = time.time() - int(days) * 86400
    with _lock:
        items = _load_items()
        expired = [i for i in items if (int(i.get("deleted_at") or 0)) < deadline]
        removed = 0
        if expired:
            for it in expired:
                try:
                    trash = os.path.normpath(str(it.get("trash", "")))
                    if path_in_trash(trash):
                        _rm_path(trash)
                except Exception as e:
                    logger.warning("过期清理删除 %s 失败: %s", it.get("trash"), e)
                    continue
                removed += 1
            if removed:
                _save_items([i for i in items if i not in expired])
        if removed:
            logger.info("回收站过期清理: 删除 %d 条", removed)
    return removed


# ---------------------------------------------------------------------------
# 后台自动清理（lifespan 挂载）：按小时遍历所有节点清理过期条目
# ---------------------------------------------------------------------------
def _purge_node(node_id: str) -> None:
    """在指定节点的请求上下文中执行一次过期清理（网络异常等全部吞掉）。"""
    try:
        node_manager.set_request_node(node_id)
        purge_expired()
    except Exception as e:
        logger.warning("清理节点 %s 回收站过期条目失败: %s", node_id, e)
    finally:
        node_manager.set_request_node(None)


def _purge_all_nodes() -> int:
    """清理本地 + 所有 SSH 节点的过期回收站条目，返回总清理数。"""
    total = 0
    try:
        nodes = node_manager.list_nodes()
    except Exception as e:
        logger.warning("读取节点列表失败: %s", e)
        return 0
    for node in nodes:
        node_id = node.get("id")
        if not node_id:
            continue
        try:
            _purge_node(node_id)
            total += 1
        except Exception:
            continue
    return total


async def _purge_loop():
    """后台循环：每 PURGE_TICK_SECONDS 秒清理一轮所有节点。"""
    while True:
        try:
            await asyncio.to_thread(_purge_all_nodes)
        except Exception as e:
            logger.error("回收站自动清理失败: %s", e)
        await asyncio.sleep(PURGE_TICK_SECONDS)


async def start_auto_purge():
    """启动回收站后台自动清理（幂等）。"""
    global _purge_task
    if _purge_task is not None and not _purge_task.done():
        return
    _purge_task = asyncio.create_task(_purge_loop())
    logger.info("回收站自动清理已启动")


async def stop_auto_purge():
    """停止回收站后台自动清理。"""
    global _purge_task
    if _purge_task is not None:
        _purge_task.cancel()
        try:
            await _purge_task
        except asyncio.CancelledError:  # lgtm[py/empty-except] 取消后台任务触发 CancelledError，正常退出
            pass
        _purge_task = None