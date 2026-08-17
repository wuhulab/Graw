# -*- coding: utf-8 -*-
"""
disks.py - 磁盘管理路由

通过 lsblk（Linux）或 psutil（Windows）获取宿主机块设备与分区信息，
供前端"磁盘管理"窗口展示。

- 磁盘列表：名称、总大小、分区数、磁盘类型（HDD/SSD）、是否系统盘。
- 分区列表：名称、大小、已用、可用、使用率、挂载目录、文件系统。
- 系统盘判定：挂载点为宿主机根（ / 或容器模式下的 HOST_ROOT）的磁盘，
  前端据此禁用其操作（格式化/删除分区等），避免误操作系统盘。
"""
import logging
import os
import platform

import psutil
from fastapi import APIRouter

from app.hostfs import HOST_ROOT, host_cmd, host_path, host_which

logger = logging.getLogger(__name__)

router = APIRouter()


def _gb(val: int) -> str:
    """字节数转可读的大小字符串（保留最多一位小数）。"""
    if val is None:
        return "-"
    gb = val / (1024 ** 3)
    if gb >= 100:
        return f"{int(round(gb))}G"
    return f"{gb:.1f}G"


def _is_system_disk(disk_name: str, partitions: list) -> bool:
    """判断磁盘是否为系统盘。

    命中条件：该磁盘下存在挂载点等于宿主机根目录（非容器为"/"，容器为
    HOST_ROOT）或 /boot 的分区。
    """
    root_targets = {"/"}
    if HOST_ROOT:
        root_targets.add(HOST_ROOT)
    for p in partitions:
        mp = (p.get("mountpoint") or "").strip()
        if mp in root_targets or mp == "/boot":
            parent = (p.get("name") or "")
            if parent == disk_name or parent.startswith(disk_name):
                return True
    return False


def _parse_lsblk_tree(node) -> tuple:
    """递归解析一条 lsblk 节点（含子分区），返回（磁盘dict, 分区list）。"""
    partitions = []
    disks = []
    for child in node.get("children", []) or []:
        ctype = (child.get("type") or "").strip()
        if ctype == "disk":
            # 罕见：磁盘嵌套磁盘，递归处理
            d, _ = _parse_lsblk_tree(child)
            disks.extend(d)
        elif ctype in ("part", "lvm", "crypt"):
            partitions.append(
                {
                    "name": child.get("name") or "",
                    "device": "/dev/" + (child.get("name") or ""),
                    "size": child.get("size") or 0,
                    "fstype": child.get("fstype") or "",
                    "mountpoint": (child.get("mountpoint") or "").strip() or "",
                    "label": child.get("label") or "",
                }
            )
    # 当前节点若是磁盘则收集
    ctype = (node.get("type") or "").strip()
    if ctype == "disk":
        size = node.get("size") or 0
        rota = node.get("rota")
        disks.append(
            {
                "name": node.get("name") or "",
                "size": size,
                "size_display": _gb(size),
                "partitions": len(partitions),
                "type": "HDD" if rota == "1" else "SSD",
                "model": node.get("model") or "",
                "system": _is_system_disk(node.get("name") or "", partitions),
                "parts": partitions,
            }
        )
    return disks, partitions


def _load_lsblk() -> list:
    """解析宿主机 lsblk 输出，返回磁盘列表。"""
    if not host_which("lsblk"):
        logger.warning("宿主机缺少 lsblk 命令，无法获取块设备信息")
        return []
    proc = host_cmd(
        [
            "lsblk",
            "-b",
            "-J",
            "-o",
            "NAME,SIZE,ROTA,TYPE,FSTYPE,MOUNTPOINT,LABEL,MODEL",
        ],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if proc.returncode != 0:
        logger.error("lsblk 执行失败: %s", proc.stderr.strip())
        return []
    import json

    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError as e:
        logger.error("lsblk 输出解析失败: %s", e)
        return []
    disks = []
    for node in data.get("blockdevices", []) or []:
        d, _ = _parse_lsblk_tree(node)
        disks.extend(d)
    return disks


def _load_windows_disk() -> list:
    """Windows 兜底：用 psutil 分区信息合成磁盘视图（按盘符归组）。"""
    seen = {}
    for part in psutil.disk_partitions(all=False):
        device = part.device  # 如 C:
        disk_name = (device or "").rstrip(":\\") or "disk"
        d = seen.setdefault(
            disk_name,
            {
                "name": disk_name,
                "size": part.fstype or "",
                "size_display": "-",
                "partitions": 0,
                "type": "SSD",
                "model": "",
                "system": True,
                "parts": [],
            },
        )
        d["parts"].append(
            {
                "name": device,
                "device": device,
                "size": 0,
                "fstype": part.fstype or "",
                "mountpoint": part.mountpoint or "",
                "label": "",
            }
        )
        d["partitions"] = len(d["parts"])
    return list(seen.values())


def _attach_usage(disks) -> None:
    """把每个分区的已用/可用/使用率挂到 parts 上。"""
    usage_map = {}
    for part in psutil.disk_partitions(all=False):
        mp = part.mountpoint
        usage_map[mp] = {"device": part.device, "fstype": part.fstype, "mountpoint": mp}
    for disk in disks:
        for p in disk["parts"]:
            mp = p["mountpoint"]
            # 宿主机根的挂载点在容器模式下被映射到 HOST_ROOT
            real_mp = host_path(mp) if mp == "/" else mp
            if real_mp in usage_map:
                info = usage_map[real_mp]
                p["name"] = p["name"] or os.path.basename(info["device"])
                p["fstype"] = p["fstype"] or info["fstype"]
                try:
                    usage = psutil.disk_usage(real_mp)
                    total = usage.total
                    used = usage.used
                    avail = usage.free
                    p["size"] = total
                    p["used"] = used
                    p["available"] = avail
                    p["percent"] = round(used / total * 100, 1) if total else 0
                    p["size_display"] = _gb(total)
                    p["used_display"] = _gb(used)
                    p["avail_display"] = _gb(avail)
                except (PermissionError, OSError):
                    logger.warning("无法获取挂载点 %s 的使用信息", real_mp)
                    p["used"], p["available"], p["percent"] = 0, 0, 0
            else:
                # 未挂载分区 / 挂载点不在 psutil 中，标记为不可用
                p["used"], p["available"], p["percent"] = 0, 0, None


@router.get("/list")
async def disk_list():
    """返回磁盘与分区信息。"""
    try:
        if platform.system() == "Windows":
            disks = _load_windows_disk()
        else:
            disks = _load_lsblk()
        _attach_usage(disks)
        # 基于最终分区挂载点重新判定系统盘，并同步到每个分区
        for disk in disks:
            disk["system"] = _is_system_disk(
                disk.get("name", ""), disk.get("parts", [])
            )
            for p in disk.get("parts", []):
                p["system"] = disk["system"]
        return {"disks": disks}
    except Exception as e:  # noqa: BLE001 - 兜底避免磁盘接口影响整体面板
        logger.exception("获取磁盘信息失败")
        return {"disks": [], "error": str(e)}


@router.post("/mount")
async def mount_partition(device: str, mountpoint: str):
    """挂载一个非系统盘分区。

    入参：
      device    块设备名（如 vda1），后端拼成 /dev/vda1。
      mountpoint 挂载目录（宿主机视角绝对路径），不存在时自动创建。
    """
    # 运行环境校验
    if platform.system() == "Windows":
        return {"ok": False, "message": "Windows 环境不支持 mount 命令"}

    # 设备名校验：仅允许字母/数字/点和短横线，杜绝路径穿越与命令注入
    if not device or not all(c.isalnum() or c in ".-_" for c in device):
        return {"ok": False, "message": "设备名不合法"}

    # 挂载点校验：必须为绝对路径，且不得是根目录
    if not mountpoint.startswith("/") or mountpoint == "/":
        return {"ok": False, "message": "挂载点必须是绝对路径且不能为根目录"}

    # 检查 mount 与 mkdir 命令在宿主机可用
    if not host_which("mount"):
        return {"ok": False, "message": "宿主机缺少 mount 命令"}

    dev_path = "/dev/" + device
    # container 模式下 mount 需在宿主机执行（privileged + mount 权限）
    host_mountpoint = host_path(mountpoint)

    # 1. 挂载点不存在则创建
    mkdir = host_cmd(["mkdir", "-p", host_mountpoint], capture_output=True, text=True)
    if mkdir.returncode != 0:
        return {"ok": False, "message": f"创建挂载目录失败：{mkdir.stderr.strip()}"}

    # 2. 执行挂载
    mount = host_cmd(["mount", dev_path, host_mountpoint], capture_output=True, text=True)
    if mount.returncode != 0:
        return {"ok": False, "message": f"挂载失败：{mount.stderr.strip()}"}

    logger.info("已挂载 %s -> %s", dev_path, mountpoint)
    return {"ok": True, "message": "挂载成功"}