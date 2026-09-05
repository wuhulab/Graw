# -*- coding: utf-8 -*-
"""
reporting.py - 定期巡检报告生成与推送（非路由模块）

背景：
  面板已有每日后台数据：系统指标历史（metrics_store）、证书到期（certcheck）、
  服务监控（svcmonitor）、站点可用性（uptime）、资源告警记录（notify_logs）。
  本模块把这些数据汇聚成一份「守护报告」（文本），供运维不登录面板也能
  （经通知渠道）掌握主机健康状态。

设计要点：
  - 纯只读聚合：不修改任何业务数据；生成放线程池（路由/to_thread 调用）。
  - 报告含五段：资源 24h 概览 / 证书（30 天内到期）/ 服务监控异常 / 站点
    可用性异常 / 告警统计；全部从既有模块公开入口读取，失败单段降级。
  - 保留 30 份以文件名轮转；推送用 notify.push_all（只有文本渠道）。
  - 每日定时（08:00）由 main.py lifespan 启停的协程触发，也可手动触发。
"""

import asyncio
import logging
import os
import time
from datetime import datetime, timedelta

logger = logging.getLogger("graw.report")

DATA_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "data"))
REPORTS_DIR = os.path.join(DATA_DIR, "reports")
# 保留报告份数（超出删除最旧）
_KEEP = 30
# 报告覆盖时间窗（小时）
_WINDOW_HOURS = 24


def _ensure_dir() -> None:
    os.makedirs(REPORTS_DIR, exist_ok=True)


def list_reports(limit: int = 30) -> list:
    """列出已生成报告（文件名时间倒序）。"""
    _ensure_dir()
    try:
        files = [f for f in os.listdir(REPORTS_DIR) if f.endswith(".txt")]
    except OSError as e:
        logger.warning("列出报告失败: %s", e)
        return []
    files.sort(reverse=True)
    return [f for f in files[:limit]]


def read_report(name: str) -> str:
    """读取某份报告文本；name 白名单防穿越。"""
    safe = os.path.basename(name or "")
    if (not safe) or not safe.endswith(".txt"):
        return ""
    p = os.path.join(REPORTS_DIR, safe)
    if not os.path.isfile(p):
        return ""
    try:
        with open(p, "r", encoding="utf-8") as f:
            return f.read()
    except OSError as e:
        logger.warning("读取报告 %s 失败: %s", safe, e)
        return ""


def _rotate() -> None:
    """报告超量轮转（按文件名排序删除最旧）。"""
    _ensure_dir()
    try:
        files = sorted(f for f in os.listdir(REPORTS_DIR) if f.endswith(".txt"))
    except OSError:
        return
    for f in files[: max(0, len(files) - _KEEP)]:
        try:
            os.remove(os.path.join(REPORTS_DIR, f))
        except OSError:
            pass


# ---------------------------------------------------------------------------
# 数据汇聚（每段失败单独降级，不影响其余段落）
# ---------------------------------------------------------------------------
def _section_resources(lines: list) -> None:
    """资源 24h 概览：从指标历史取小时桶，算平均/峰值。"""
    try:
        from app import metrics_store

        end = time.time()
        start = end - _WINDOW_HOURS * 3600
        hist = metrics_store.history(start, end, bucket=3600)
        pts = hist.get("points", [])
        lines.append("【系统资源（过去 24 小时）】")
        if not pts:
            lines.append("  无可用指标历史（面板未运行满 24h 或指标已过保留期）")
            lines.append("")
            return
        keys = [("cpu", "CPU%"), ("mem", "内存%"), ("disk", "磁盘%"), ("load1", "负载")]

        def _avg_max(k):
            vals = [float(p.get(k) or 0) for p in pts]
            avg = sum(vals) / len(vals) if vals else 0
            mx = max(vals) if vals else 0
            return avg, mx

        peak_ts = ""
        for k, label in keys:
            avg, mx = _avg_max(k)
            if k == "load1":
                lines.append(f"  {label}  平均 {avg:.2f} / 峰值 {mx:.2f}")
            else:
                lines.append(f"  {label}  平均 {avg:.1f}% / 峰值 {mx:.1f}%")
            if k == "cpu" and mx >= 90:
                for p in pts:
                    if float(p.get("cpu") or 0) >= 90:
                        peak_ts = datetime.fromtimestamp(p["ts"]).strftime("%H:%M")
                        break
        if peak_ts:
            lines.append(f"  ⚠ CPU 曾 ≥90%（约 {peak_ts} 附近）")
        lines.append("")
    except Exception as e:  # 指标模块异常：降级为空段
        logger.warning("报告生成：资源段失败: %s", e)
        lines.append("【系统资源】数据不可用")
        lines.append("")


def _section_certs(lines: list) -> None:
    """证书：30 天内到期 / 已过期列表。"""
    try:
        from app.routers import certcheck

        certs = certcheck._check_certs_sync()
    except Exception as e:
        logger.warning("报告生成：证书段失败: %s", e)
        return
    bad = [c for c in certs if c.get("status") in ("warn", "expired")]
    lines.append("【证书（30 天内到期 / 已过期）】")
    if not bad:
        lines.append("  无即将到期证书 ✓")
    else:
        for c in bad:
            name = c.get("name") or "/".join(c.get("domains") or []) or c.get("id") or "?"
            lines.append(f"  - {name} 剩余 {c.get('days_left')} 天（{c.get('status')}）")
    lines.append("")


def _section_services(lines: list) -> None:
    """服务监控异常项。"""
    try:
        from app.routers import svcmonitor

        items = svcmonitor._load().get("items", [])
    except Exception as e:
        logger.warning("报告生成：服务监控段失败: %s", e)
        return
    bad = [i for i in items if (i.get("last_status") or i.get("status") or "ok") not in ("ok", "healthy")]
    lines.append("【服务监控异常】")
    if not bad:
        lines.append(f"  共监控 {len(items)} 项，均正常 ✓")
    else:
        for i in bad:
            lines.append(f"  - {i.get('name')}（{i.get('last_status') or i.get('status')}）")
    lines.append("")


def _section_uptime(lines: list) -> None:
    """站点可用性监控：当前 DOWN 的站点。"""
    try:
        from app.routers import uptime

        items = uptime._load().get("items", [])
    except Exception as e:
        logger.warning("报告生成：可用性段失败: %s", e)
        return
    down = [i for i in items if i.get("last_status") == "down"]
    lines.append("【站点可用性】")
    if not down:
        lines.append(f"  共监控 {len(items)} 个站点，均可达 ✓")
    else:
        for i in down:
            lines.append(f"  - {i.get('name')}（{i.get('url')}）已 {i.get('down_since') or '持续'} 不可达")
    lines.append("")


def _section_alerts(lines: list) -> None:
    """近 24h 资源告警统计（来自通知中心的阈值告警记录）。"""
    try:
        from app.routers import notify

        logs = notify._load_logs()
    except Exception as e:
        logger.warning("报告生成：告警段失败: %s", e)
        return
    cutoff = datetime.now() - timedelta(hours=_WINDOW_HOURS)
    recent = []
    for e in logs:
        t = e.get("time", "")
        try:
            dt = datetime.fromisoformat(t)
        except (ValueError, TypeError):
            continue
        if dt >= cutoff:
            recent.append(e)
    lines.append("【资源告警（过去 24 小时）】")
    if not recent:
        lines.append("  无阈值告警触发 ✓")
    else:
        by_metric = {}
        for e in recent:
            m = e.get("metric", "?")
            by_metric[m] = by_metric.get(m, 0) + 1
        lines.append(f"  共触发 {len(recent)} 次：" + "，".join(f"{k}×{v}" for k, v in by_metric.items()))
        lines.append(f"  （末次：{recent[-1].get('message', '')[:120]}）")
    lines.append("")


# ---------------------------------------------------------------------------
# 生成与推送
# ---------------------------------------------------------------------------
def generate_report() -> dict:
    """生成一份巡检报告并落盘，返回 {file, text, pushed}。"""
    lines = [
        "====== Graw 巡检报告 ======",
        "生成时间：" + datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "",
    ]
    _section_resources(lines)
    _section_certs(lines)
    _section_services(lines)
    _section_uptime(lines)
    _section_alerts(lines)
    lines.append("（完整报告以文本存储于 data/reports/，可在面板查看）")
    text = "\n".join(lines)
    _ensure_dir()
    fname = datetime.now().strftime("%Y%m%d_%H%M%S") + ".txt"
    try:
        with open(os.path.join(REPORTS_DIR, fname), "w", encoding="utf-8") as f:
            f.write(text)
        _rotate()
    except OSError as e:
        logger.error("写报告失败: %s", e)
        return {"file": "", "text": text, "pushed": 0}
    # 推送：先推摘要（避免整篇太长刷屏），写入 notify 渠道
    pushed = 0
    try:
        from app.routers import notify

        summary = "\n".join(lines[:3] + lines[3:][:3] + ["（全文见面板「巡检报告」）"])
        pushed = notify.push_all("【Graw 每日巡检】\n" + summary)[0]
    except Exception as e:
        logger.warning("报告推送失败: %s", e)
    return {"file": fname, "text": text, "pushed": pushed}


async def _daily_loop() -> None:
    """每日定时巡检主循环（08:00 生成并推送）。"""
    while True:
        try:
            now = datetime.now()
            # 距下一个 08:00 的秒数
            target = now.replace(hour=8, minute=0, second=0, microsecond=0)
            if now >= target:
                target += timedelta(days=1)
            wait = (target - now).total_seconds()
            await asyncio.sleep(wait)
            try:
                # 收敛到线程池执行（汇聚各模块数据 + 写盘 + 推送，避免阻塞事件循环）
                await asyncio.to_thread(generate_report)
            except Exception as e:
                logger.error("每日巡检生成失败: %s", e)
            # 生成后立即进入下一周期（避免每天多跑）
            await asyncio.sleep(60)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("巡检循环异常，1 小时后重试")
            await asyncio.sleep(3600)


_task: "asyncio.Task | None" = None


def start_daily() -> None:
    """启动每日巡检后台任务（main.py lifespan 调用）。"""
    global _task
    if _task is not None and not _task.done():
        return
    _task = asyncio.create_task(_daily_loop())


def stop_daily() -> None:
    """停止每日巡检后台任务。"""
    global _task
    if _task is not None:
        _task.cancel()
        _task = None