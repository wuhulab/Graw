# -*- coding: utf-8 -*-
"""
temps.py - 宿主机温度传感器采集（只读 sysfs，无外部依赖）

背景：
  面板缺少硬件温度数据——服务器过热（CPU 温度墙、硬盘高温）是常见故障前兆，
  但当前监控只有 CPU/内存/磁盘/负载。本模块从宿主机内核 sysfs 直接读取温度：

    - /sys/class/hwmon/hwmon*/temp*_input   （首选：coretemp / k10temp / nvme 等驱动）
    - /sys/class/thermal/thermal_zone*     （回退：精简系统 / ARM 无 hwmon 时）

  容器模式下经 hostfs.host_path() 映射到 /host 前缀（与其它宿主路径读取一致）；
  本机直接运行时原样读取。Windows / 无传感器 / 权限不足时返回空列表（前端自动隐藏）。

设计要点：
  1. 两源不合并：hwmon 数据更丰富（带 label），优先使用；仅当 hwmon 无任何
     传感器时才回退 thermal zone，避免同一温度出现两条重复记录。
  2. CPU 温度判定（kind=cpu）：hwmon 驱动名（coretemp/k10temp/zenpower 等）
     或 label 含 package/tctl/tdie/cpu。仅 CPU 类参与「阈值告警 / 历史曲线」，
     避免把主板（acpitz）温度误标为 CPU 温度。
  3. 合理性过滤：读数换算为摄氏度后超出 [-50, 200] 视为异常值丢弃。
  4. 性能：纯文件读取（微秒级），单次运行最多 8 个传感器、名称截断 48 字符。
"""
import os
import re
import logging
from typing import List, Optional

from app.hostfs import host_path

logger = logging.getLogger("graw.temps")

# 传感器数量上限（防止异常硬件枚举出几十条撑爆卡片与历史字段）
MAX_SENSORS = 8
# 展示名最大长度（异常 label 截断，避免撑破界面）
MAX_NAME = 48

# CPU 温度相关的 hwmon 驱动名（x86: coretemp/k10temp，ARM: cpu_thermal，虚拟机: soc_thermal）
_CPU_HWMON = {"coretemp", "k10temp", "zenpower", "cpu_thermal", "soc_thermal"}
# CPU 温度相关的 label / 热区类型特征：
#   Intel "Package id 0"、AMD "Tctl"/"Tdie"、thermal 热区 "x86_pkg_temp"、ARM "cpu-thermal" 等
_CPU_LABEL_RE = re.compile(r"package|pkg|tctl|tdie|cpu", re.IGNORECASE)


def _read_text(path: str) -> str:
    """读取文本文件并去空白；不存在 / 不可读时返回空串（尽力而为，不抛异常）。"""
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read().strip()
    except OSError:
        # 传感器热插拔 / 权限不足等场景直接忽略该文件
        return ""


def milli_to_celsius(raw) -> Optional[float]:
    """sysfs 毫摄氏度（如 45000）转摄氏度（45.0）并做合理性过滤。

    返回 None 表示该读数不可用（空值 / 非数字 / 超出 [-50, 200] 物理范围）。
    """
    if raw is None:
        return None
    try:
        val = float(str(raw).strip()) / 1000.0
    except (TypeError, ValueError):
        return None
    if val < -50.0 or val > 200.0:
        return None
    return round(val, 1)


def is_cpu_sensor(name: str, label: str) -> bool:
    """判断传感器是否属于 CPU（驱动名或 label 命中特征）。

    用于把 CPU 温度与主板 / 硬盘 / 机箱温度区分开——只有 CPU 类温度才参与
    阈值告警与历史曲线，避免「acpitz 主板温度」被误标为 CPU 温度。
    """
    if (name or "").strip().lower() in _CPU_HWMON:
        return True
    return bool(_CPU_LABEL_RE.search(label or ""))


def _scan_hwmon(base: str) -> List[dict]:
    """扫描 hwmon 目录，返回原始传感器列表（名称未去重）。"""
    sensors: List[dict] = []
    try:
        entries = sorted(os.listdir(base))
    except OSError:
        # 目录不存在（Windows / 无 hwmon 内核模块）时静默返回空
        return sensors
    for entry in entries:
        if not entry.startswith("hwmon"):
            continue
        dev_dir = os.path.join(base, entry)
        hname = _read_text(os.path.join(dev_dir, "name")) or entry
        try:
            files = sorted(os.listdir(dev_dir))
        except OSError:
            continue
        for fn in files:
            # 只取 tempN_input；label 为可选的 tempN_label
            if not (fn.startswith("temp") and fn.endswith("_input")):
                continue
            num = fn[len("temp"):-len("_input")]
            if not num.isdigit():
                continue
            value = milli_to_celsius(_read_text(os.path.join(dev_dir, fn)))
            if value is None:
                continue
            label = _read_text(os.path.join(dev_dir, f"temp{num}_label"))
            display = (label or hname)[:MAX_NAME]
            sensors.append({
                "name": display,
                "value": value,
                "kind": "cpu" if is_cpu_sensor(hname, label or display) else "other",
                "source": hname,  # 驱动名：用于同名传感器（多块 NVMe）消歧
            })
    return sensors


def _scan_thermal(base: str) -> List[dict]:
    """扫描 thermal_zone 目录（hwmon 缺失时的回退源）。"""
    sensors: List[dict] = []
    try:
        entries = sorted(os.listdir(base))
    except OSError:
        return sensors
    for entry in entries:
        if not entry.startswith("thermal_zone"):
            continue
        zone_dir = os.path.join(base, entry)
        ztype = _read_text(os.path.join(zone_dir, "type")) or entry
        value = milli_to_celsius(_read_text(os.path.join(zone_dir, "temp")))
        if value is None:
            continue
        sensors.append({
            "name": ztype[:MAX_NAME],
            "value": value,
            "kind": "cpu" if is_cpu_sensor(ztype, ztype) else "other",
            "source": ztype,  # 热区类型：与展示名一致，保留字段以便统一消歧逻辑
        })
    return sensors


def normalize_sensors(sensors: List[dict]) -> List[dict]:
    """统一整理采集结果：排序（CPU 优先、名称稳定）→ 同名消歧 → 截断上限。

    同名消歧分两步：
      1. 不同驱动（如两块 NVMe）label 都叫 "Composite" 时，前缀驱动名区分
         （"nvme Composite"）；
      2. 前缀后仍同名（同型号多设备）时追加序号（"nvme Composite #2"），
         保证前端每条记录可分辨。
    """
    valid = [s for s in sensors if isinstance(s, dict) and isinstance(s.get("value"), (int, float))]
    # 统计展示名出现次数，用于第一步消歧
    counts: dict = {}
    for s in valid:
        counts[s.get("name")] = counts.get(s.get("name"), 0) + 1
    result: List[dict] = []
    seen: dict = {}
    for s in valid:
        name = s.get("name") or "sensor"
        if counts.get(name, 0) > 1 and s.get("source"):
            name = f"{s['source']} {name}"[:MAX_NAME]
        # 第二步：前缀后仍有重复时追加序号
        n = seen.get(name, 0) + 1
        seen[name] = n
        if n > 1:
            name = f"{name} #{n}"[:MAX_NAME]
        result.append({"name": name, "value": round(float(s["value"]), 1), "kind": s.get("kind", "other")})
    # CPU 优先，其次按名称稳定排序（前端展示顺序不会每次刷新乱跳）
    result.sort(key=lambda s: (0 if s.get("kind") == "cpu" else 1, s.get("name") or ""))
    return result[:MAX_SENSORS]


def collect_temps() -> List[dict]:
    """采集宿主机全部温度传感器，返回统一结构列表。

    返回：[{"name": 展示名, "value": 摄氏度, "kind": "cpu"|"other"}, ...]
    - 容器模式经 host_path() 读取宿主机 /host/sys；本机直跑时读本地 /sys。
    - hwmon 优先、thermal 回退（两源不合并，避免同一温度重复）。
    - 无传感器（Windows / 虚拟机 / 精简内核）时返回空列表，调用方正常降级。
    """
    sensors = _scan_hwmon(host_path("/sys/class/hwmon"))
    if not sensors:
        sensors = _scan_thermal(host_path("/sys/class/thermal"))
    return normalize_sensors(sensors)


def cpu_temp(sensors) -> Optional[float]:
    """从采集结果中取 CPU 温度（用于历史曲线与阈值告警）。

    仅采信 kind=="cpu" 的传感器；无 CPU 温度（如仅有主板 acpitz）时返回 None，
    宁可缺失也不误标——避免用户按错误数据判断 CPU 过热。
    """
    if not isinstance(sensors, list):
        return None
    for s in sensors:
        if isinstance(s, dict) and s.get("kind") == "cpu":
            value = s.get("value")
            if isinstance(value, (int, float)):
                return float(value)
    return None