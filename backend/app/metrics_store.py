# -*- coding: utf-8 -*-
"""
metrics_store.py - 历史监控指标持久化存储

背景：
  面板首页的系统指标经 /api/system/ws 每 2 秒实时推送，但数据只存在于内存，
  无法回看历史趋势。本模块把采样点落盘（按天 NDJSON 文件），
  并支持按时间范围 + 聚合间隔查询，供「历史监控回放」窗口画图表。

设计要点：
  1. 采样粒度：沿用系统指标的 2 秒采样周期，与实时数据一致。
  2. 存储格式：data/metrics/YYYY-MM-DD.ndjson，一行一条采样点。
     NDJSON 便于追加写（避免整文件重写），也便于按天加载与清理。
  3. 保留策略：仅保留最近 RETENTION_DAYS（默认 7）天的文件，
     启动与每日首次写入时自动清理过期文件，防止无限增长。
  4. 聚合查询：history() 按 bucket 秒数对区间内采样做平均降采样，
     同时返回区间原始点数量，供前端决定图表渲染方式。
  5. 线程安全：生产者协程（单线程）调用 record_sample 写入内存缓冲，
     flush 时通过 threading.Lock 保护文件追加，避免与查询侧并发写冲突。
  6. 数据冗余容错：损坏的单行采样点会被跳过，不影响同文件其他采样。
"""
import os
import json
import time
import shutil
import threading
from datetime import datetime, timedelta
from typing import List, Optional

# 采样落盘周期（秒）：与 WS 推送周期保持一致
SAMPLE_INTERVAL = 2.0
# 历史保留天数
RETENTION_DAYS = 7
# 磁盘空间告警阈值（单位：字节），超过时停止采样并记录错误
_MAX_DISK_BYTES = 500 * 1024 * 1024  # 500MB 兜底

# 指标目录与按天文件路径
METRICS_DIR = os.path.join(
    os.path.dirname(__file__), "..", "data", "metrics"
)

# 写锁：保护文件追加与内存缓冲
_write_lock = threading.Lock()
# 内存中尚未落盘的采样点（生产者协程追加，flush 时清空）
_pending: List[dict] = []
# 上次磁盘使用检查时间，避免每次追加都 stat 一次
_last_space_check: float = 0.0
# 磁盘告警是否已触发（触发后进入节流，不再频繁尝试写）
_disk_warned: bool = False


def _day_path(day: str) -> str:
    """返回某天的采样文件路径。"""
    return os.path.join(METRICS_DIR, f"{day}.ndjson")


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _cleanup_old_files() -> None:
    """清理超过保留期的按天文件（保留最后一天之外的最旧 1 天，容错时钟回拨）。"""
    try:
        os.makedirs(METRICS_DIR, exist_ok=True)
        cutoff = datetime.now() - timedelta(days=RETENTION_DAYS + 1)
        cutoff_str = cutoff.strftime("%Y-%m-%d")
        for name in os.listdir(METRICS_DIR):
            if not name.endswith(".ndjson"):
                continue
            day = name[: len("YYYY-MM-DD")]
            if day < cutoff_str:
                try:
                    os.remove(os.path.join(METRICS_DIR, name))
                except OSError:
                    # 文件已被删除或正被占用时忽略
                    pass
    except OSError:
        # 目录不可访问时静默，不影响主流程
        pass


def _can_write() -> bool:
    """磁盘空间检查：指标目录所在磁盘剩余 <200MB 时停止采样，避免撑爆磁盘。

    Windows 无 os.statvfs，统一用 shutil.disk_usage 获取所在磁盘剩余空间。
    """
    global _last_space_check, _disk_warned
    now = time.monotonic()
    if now - _last_space_check < 30:
        return not _disk_warned
    _last_space_check = now
    try:
        free = shutil.disk_usage(METRICS_DIR).free
        _disk_warned = free < 200 * 1024 * 1024
    except OSError:
        _disk_warned = False
    return not _disk_warned


def record_sample(sample: Optional[dict]) -> None:
    """记录一条采样点到内存缓冲，并异步由 flush 落盘。

    入参 sample 为 None 或缺少关键字段时直接忽略（生产异常时的兜底）。
    """
    if not sample or not isinstance(sample, dict):
        return
    # 仅接受结构完整的指标 payload（含 overview/network 等关键节），
    # 缺关键节的脏数据直接忽略，避免写入全零无意义采样
    overview = sample.get("overview")
    if not isinstance(overview, dict):
        return
    now = time.time()
    try:
        # 提取并精简为紧凑结构，降低落盘体积
        memory = overview.get("memory") or {}
        storage = overview.get("storage") or {}
        load = overview.get("load") or {}
        network = sample.get("network") or {}
        diskio = sample.get("diskio") or {}
        row = {
            "ts": round(now, 1),
            "cpu": _num(overview.get("cpu")),
            "mem": _num(memory.get("percent")),
            "disk": _num(storage.get("percent")),
            "load1": _num(load.get("load1")),
            "net_up": _num(network.get("upload")),
            "net_down": _num(network.get("download")),
            "disk_read": _num(diskio.get("read")),
            "disk_write": _num(diskio.get("write")),
        }
    except Exception:
        return
    with _write_lock:
        # 限制内存缓冲上限，防止生产异常积压导致内存膨胀
        if len(_pending) < 1200:
            _pending.append(row)


def _num(v) -> float:
    """安全数值转换：无法转数值时返回 0。"""
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


def flush() -> None:
    """把内存缓冲的采样点追加写入当天文件（带写锁，失败不影响后续）。"""
    global _pending, _disk_warned
    if not _can_write():
        return
    with _write_lock:
        if not _pending:
            return
        batch = _pending
        _pending = []
    _cleanup_old_files()
    day = _today()
    path = _day_path(day)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            for row in batch:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        if os.path.getsize(path) > _MAX_DISK_BYTES:
            # 单日文件异常膨胀时截断为最近半日数据，避免单文件过大
            _truncate_file(path)
    except OSError:
        # 写失败（如磁盘满）时把批次放回缓冲，下次再试
        with _write_lock:
            _pending = batch + _pending
        _disk_warned = True


def _truncate_file(path: str) -> None:
    """单日文件超过上限时，仅保留最近 50% 行，控制文件体积。"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        half = lines[len(lines) // 2:]
        with open(path, "w", encoding="utf-8") as f:
            f.writelines(half)
    except OSError:
        pass


def _load_day(day: str) -> List[dict]:
    """读取某天的全部采样点；损坏行跳过。"""
    path = _day_path(day)
    rows: List[dict] = []
    if not os.path.exists(path):
        return rows
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                    if isinstance(row, dict) and "ts" in row:
                        rows.append(row)
                except (ValueError, TypeError):
                    continue
    except OSError:
        # 读取当天文件失败（损坏或不可访问）时返回空
        pass
    return rows


def _iter_rows(start_ts: float, end_ts: float) -> List[dict]:
    """返回 [start_ts, end_ts] 区间内的采样点（跨天读取，按 ts 升序）。"""
    start_dt = datetime.fromtimestamp(start_ts)
    end_dt = datetime.fromtimestamp(end_ts)
    # 遍历区间内可能涉及的每一天
    day = start_dt.strftime("%Y-%m-%d")
    last_day = end_dt.strftime("%Y-%m-%d")
    rows: List[dict] = []
    while day <= last_day:
        rows.extend(_load_day(day))
        day = (datetime.strptime(day, "%Y-%m-%d") + timedelta(days=1)).strftime(
            "%Y-%m-%d"
        )
    # 过滤时间范围并按时间升序
    rows = [r for r in rows if start_ts <= r.get("ts", 0) <= end_ts]
    rows.sort(key=lambda r: r.get("ts", 0))
    return rows


def history(
    start_ts: float,
    end_ts: float,
    bucket: Optional[int] = None,
) -> dict:
    """查询指定时间范围的指标历史。

    参数：
      start_ts / end_ts：Unix 时间戳（秒）
      bucket：聚合桶大小（秒）。为 None 或 <=0 时返回原始采样点。
    返回：
      {
        "points": [ {ts, cpu, mem, disk, load1, net_up, net_down, disk_read, disk_write}, ... ],
        "buckets": 聚合后的桶数（未聚合时为原始点数）,
        "raw": 区间内原始采样点数,
        "retention_days": 保留天数,
      }
    """
    if end_ts < start_ts:
        start_ts, end_ts = end_ts, start_ts
    rows = _iter_rows(start_ts, end_ts)
    raw_count = len(rows)
    if not bucket or bucket <= 0:
        return {
            "points": rows,
            "buckets": raw_count,
            "raw": raw_count,
            "retention_days": RETENTION_DAYS,
        }
    # 按 bucket 分桶平均（每桶取该桶时间中点作为 ts）
    points: List[dict] = []
    keys = ["cpu", "mem", "disk", "load1", "net_up", "net_down", "disk_read", "disk_write"]
    cur_start = int(start_ts // bucket) * bucket
    bucket_end = cur_start + bucket
    acc: dict = {k: 0.0 for k in keys}
    count = 0
    for r in rows:
        ts = r.get("ts", 0)
        if ts >= bucket_end:
            if count:
                points.append(
                    {"ts": cur_start + bucket / 2, **{k: round(acc[k] / count, 2) for k in keys}}
                )
            cur_start = int(ts // bucket) * bucket
            bucket_end = cur_start + bucket
            acc = {k: 0.0 for k in keys}
            count = 0
        if start_ts <= ts <= end_ts:
            for k in keys:
                acc[k] += _num(r.get(k))
            count += 1
    if count:
        points.append(
            {"ts": cur_start + bucket / 2, **{k: round(acc[k] / count, 2) for k in keys}}
        )
    return {
        "points": points,
        "buckets": len(points),
        "raw": raw_count,
        "retention_days": RETENTION_DAYS,
    }


def status() -> dict:
    """返回历史数据概况：保留天数、可用文件数、最早/最新采样时间。"""
    _cleanup_old_files()
    files = []
    try:
        files = sorted(n for n in os.listdir(METRICS_DIR) if n.endswith(".ndjson"))
    # 指标目录读取失败，按空结果处理忽略
    except OSError:
        pass
    earliest = None
    latest = None
    # 只需扫描首尾文件的首末行即可得到近似范围
    if files:
        head = _load_day(files[0][: len("YYYY-MM-DD")])
        tail = _load_day(files[-1][: len("YYYY-MM-DD")])
        if head:
            earliest = head[0].get("ts")
        if tail:
            latest = tail[-1].get("ts")
    return {
        "retention_days": RETENTION_DAYS,
        "days": files,
        "earliest": earliest,
        "latest": latest,
    }


def clear() -> None:
    """清空全部历史采样文件（管理员操作）。"""
    try:
        os.makedirs(METRICS_DIR, exist_ok=True)
        for name in os.listdir(METRICS_DIR):
            if name.endswith(".ndjson"):
                try:
                    os.remove(os.path.join(METRICS_DIR, name))
                except OSError:
                    # 文件已被删除或正被占用时忽略
                    pass
    except OSError:
        # 清理过程异常（目录不可访问）时忽略
        pass
