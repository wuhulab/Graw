# -*- coding: utf-8 -*-
"""
webstats.py - 网站访问统计

解析 nginx 访问日志（combined / vhost combined 格式），输出：
  - PV / UV / IP 数 等总览指标
  - 按天 PV/UV 走势（供折线图）
  - 状态码分布
  - 热门页面 / 热门 IP / 来源 Referer / UA 分类

设计要点：
  1. 大日志文件只读取"尾部"（单文件最多 _MAX_BYTES），避免整文件加载
     导致内存暴涨；配合轮转日志（access.log.1）覆盖近两天数据。
  2. 单行正则解析 combined 日志；对畸形行安全跳过，不影响整体统计。
  3. 支持按域名（server_name / Host）过滤与按天数过滤。
  4. 全部读取操作只读、不落盘，超限时返回部分数据并提示已截断。
"""
import os
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query

from app.hostfs import host_path, unhost_path

router = APIRouter()

# 常见 nginx 日志目录（宿主机视角），按优先级探测（固定路径）
_LOG_CANDIDATES = [
    "/var/log/nginx/access.log",
    "/var/log/nginx/openresty/access.log",
    "/usr/local/openresty/nginx/logs/access.log",
    "/usr/local/nginx/logs/access.log",
    # 1Panel：openresty（Docker）容器挂载到宿主的全局访问日志
    "/opt/1panel/apps/openresty/openresty/log/access.log",
    "/var/log/httpd/access_log",
]

# 目录/通配日志候选：1Panel 站点级访问日志（每个站点一个文件）等需要 glob 展开
_EXT_LOG_GLOBS = [
    "/opt/1panel/www/sites/*/log/access.log",
    "/opt/1panel/wwwlogs/*.log",
]

# 单文件最多处理的字节数（约 200MB），防止超大日志拖垮后端
_MAX_BYTES = 200 * 1024 * 1024

# combined 日志格式：
#   $remote_addr - $remote_user [$time_local] "$request" $status $body_bytes_sent "$http_referer" "$http_user_agent"
_LOG_RE = re.compile(
    r'^(?P<ip>[\d.:a-fA-F]+)\s+-\s+(?P<user>\S+)\s+'
    r'\[(?P<time>[^\]]+)\]\s+'
    r'"(?P<request>[^"]*)"\s+'
    r'(?P<status>\d{3})\s+'
    r'(?P<bytes>\d+)\s+'
    r'"(?P<referer>[^"]*)"\s+'
    r'"(?P<ua>[^"]*)"'
)

# 常见静态资源后缀：默认不计入"页面"统计（仍是 PV，但不进热门页面榜）
_STATIC_EXT = re.compile(r'\.(css|js|png|jpe?g|gif|webp|svg|ico|woff2?|ttf|eot|map)(\?.*)?$', re.I)
# 常见的机器人 / 爬虫 UA 关键词
_BOT_HINT = re.compile(r'bot|spider|crawl|slurp|scan|curl|wget|python-requests|Go-http|postman', re.I)
# 域名提取：vhost combined 日志第 1 字段可能是域名（前面带数字则为纯 IP）
_HOST_FIRST = re.compile(r'^(?P<host>[\w.-]+\.[a-z]{2,63}):\d+\s+')

# 月份表：解析 nginx time_local（如 20/Aug/2026:10:15:30 +0800）
_MONTHS = {m: i + 1 for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
)}


def _parse_time_local(value: str) -> Optional[datetime]:
    """解析 nginx time_local 格式（如 20/Aug/2026:10:15:30 +0800，忽略时区偏移）。"""
    m = re.match(r"^(\d{1,2})/([A-Za-z]{3})/(\d{4}):(\d{2}):(\d{2}):(\d{2})", value.strip())
    if not m:
        return None
    day, mon, year, hh, mm, ss = m.groups()
    try:
        return datetime(int(year), _MONTHS[mon], int(day), int(hh), int(mm), int(ss))
    except Exception:
        return None


def _parse_line(line: str) -> Optional[dict]:
    """解析单行访问日志；畸形行返回 None。"""
    m = _LOG_RE.match(line.strip())
    if not m:
        return None
    d = m.groupdict()
    # 提取 vhost 域名（若日志为 vhost combined 格式）
    host = None
    hm = _HOST_FIRST.match(line)
    if hm and not hm.group("host")[0].isdigit():
        host = hm.group("host")
    request = d.get("request") or ""
    method, path, *_ = (request.split(" ") + ["", ""])
    return {
        "ip": d.get("ip", ""),
        "time": _parse_time_local(d.get("time", "")),
        "method": method,
        "path": path,
        "status": int(d.get("status") or 0),
        "referer": d.get("referer", ""),
        "ua": d.get("ua", ""),
        "host": host,
    }


def _read_tail(path: str, max_bytes: int = _MAX_BYTES) -> List[str]:
    """读取文件尾部（最多 max_bytes），返回逐行列表（流式释放内存）。

    - 文件不存在 / 无权限：返回空列表
    - 文件超过 max_bytes：只保留最后 max_bytes，配合 _TRUNCATED 提示
    """
    if not os.path.exists(path):
        return []
    try:
        size = os.path.getsize(path)
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            if size > max_bytes:
                # 从文件末尾往前回退 max_bytes，丢弃不完整的首行
                f.seek(size - max_bytes)
                f.readline()
            else:
                f.seek(0)
            return [ln.rstrip("\n") for ln in f]
    except Exception:
        return []


def _discover_logs() -> List[str]:
    """探测可用的访问日志路径（宿主机视角）。

    固定候选逐项 `isfile` 探测；目录/通配候选（如 1Panel 站点级日志）
    用 glob 展开后拼接，统一去重保序。
    """
    found = []
    seen = set()

    def _add(hp):
        if os.path.isfile(hp) and hp not in seen:
            seen.add(hp)
            found.append(hp)

    for p in _LOG_CANDIDATES:
        _add(host_path(p))

    import glob as _glob
    for pat in _EXT_LOG_GLOBS:
        for hp in _glob.glob(host_path(pat)) or []:
            _add(hp)
    return found


def _sanitize_domain(domain: str) -> str:
    """将域名规整为可用于拼路径的安全片段（仅字母/数字/./-）。"""
    d = (domain or "").strip().lower()
    return re.sub(r"[^a-z0-9.-]", "", d)


def _domain_matches(host: Optional[str], domain: str) -> bool:
    """日志行 host 是否匹配目标域名（支持 *.example.com 通配）。"""
    if not domain or not host:
        return not domain
    domain = domain.strip().lower().lstrip("*.")
    host = host.lower()
    return host == domain or host.endswith("." + domain)


def _aggregate(
    lines: List[str],
    *,
    days: int,
    domain: str,
    cut_date: datetime,
) -> dict:
    """统计日志行，输出总览 / 走势 / 榜单等聚合结果。"""
    pv = 0            # 总请求数（页面请求 + 静态资源）
    page_pv = 0       # 仅页面（非静态资源）
    ip_set = set()
    ua_set = set()
    daily: Dict[str, dict] = {}
    status: Dict[int, int] = {}
    top_pages: Dict[str, int] = {}
    top_ips: Dict[str, int] = {}
    referers: Dict[str, int] = {}
    bots = 0

    for line in lines:
        rec = _parse_line(line)
        if not rec or not rec["time"]:
            continue
        # 按天数过滤：超过范围的直接跳过
        if rec["time"] < cut_date:
            continue
        # 按域名过滤
        if domain and not _domain_matches(rec.get("host"), domain):
            continue

        pv += 1
        ip_set.add(rec["ip"])
        ua_set.add(rec["ua"])
        if rec["ua"] and _BOT_HINT.search(rec["ua"]):
            bots += 1

        # 状态码
        status[rec["status"]] = status.get(rec["status"], 0) + 1

        # 走势：按天分组
        day_key = rec["time"].strftime("%Y-%m-%d")
        dd = daily.setdefault(day_key, {"pv": 0, "ip": set()})
        dd["pv"] += 1
        dd["ip"].add(rec["ip"])

        # 热门页面（排除静态资源与带查询串的同页面归一）
        path = rec["path"] or "/"
        if not _STATIC_EXT.search(path):
            page_pv += 1
            clean = path.split("?")[0][:200]
            top_pages[clean] = top_pages.get(clean, 0) + 1

        # 热门 IP
        top_ips[rec["ip"]] = top_ips.get(rec["ip"], 0) + 1

        # 来源（仅外部站点，跳过本站/直接访问/无来源标记 -）
        ref = (rec["referer"] or "").strip()
        if ref and ref != "-" and not ref.startswith("/"):
            ref_host = re.sub(r"^https?://([^/]+).*$", r"\1", ref)
            if ref_host:
                referers[ref_host] = referers.get(ref_host, 0) + 1

    # 走势排序 & IP 集合转数量
    sorted_daily = []
    for k in sorted(daily.keys()):
        sorted_daily.append({"date": k, "pv": daily[k]["pv"], "uv": len(daily[k]["ip"])})

    return {
        "pv": pv,
        "page_pv": page_pv,
        "uv": len(ua_set),
        "ip_count": len(ip_set),
        "bots": bots,
        "daily": sorted_daily,
        "status": {str(k): v for k, v in sorted(status.items(), key=lambda x: -x[1])},
        "top_pages": [{"path": k, "count": v} for k, v in
                      sorted(top_pages.items(), key=lambda x: -x[1])[:20]],
        "top_ips": [{"ip": k, "count": v} for k, v in
                    sorted(top_ips.items(), key=lambda x: -x[1])[:20]],
        "referers": [{"host": k, "count": v} for k, v in
                     sorted(referers.items(), key=lambda x: -x[1])[:20]],
    }


@router.get("/logs")
async def list_logs():
    """返回可用的访问日志路径（供前端选择）。"""
    found = _discover_logs()
    return {"logs": [unhost_path(p) for p in found]}


@router.get("/analyze")
async def analyze(
    log_path: str = Query(default="", description="访问日志路径（空则自动探测）"),
    days: int = Query(default=7, ge=1, le=365, description="统计天数"),
    domain: str = Query(default="", description="按域名过滤（可选）"),
):
    """解析访问日志并返回统计结果。

    - 未指定 log_path 时自动探测常见路径；找不到则 400。
    - 日志超大会自动只取尾部并截断（部分统计）。
    """
    # 校验输入：绝对路径 + 禁止 .. 穿越（Linux/Windows 统一处理）
    if log_path:
        norm = os.path.normpath(log_path)
        if not os.path.isabs(norm):
            raise HTTPException(status_code=400, detail="日志路径必须是绝对路径")
        if ".." in norm.split(os.sep):
            raise HTTPException(status_code=400, detail="日志路径不允许包含 ..")
        path = host_path(norm)
    else:
        found = _discover_logs()
        d = _sanitize_domain(domain)
        if d:
            site_log = host_path(f"/opt/1panel/www/sites/{d}/log/access.log")
            if os.path.isfile(site_log):
                # 站点级日志已限定该域名（首字段无 vhost host），置空 domain 避免再按 host 过滤把数据排空
                found = [site_log]
                domain = ""
        if not found:
            raise HTTPException(
                status_code=400,
                detail="未在常见路径找到 nginx 访问日志，请手动指定日志路径",
            )
        path = found[0]

    if not os.path.isfile(path):
        raise HTTPException(status_code=400, detail=f"日志文件不存在: {log_path or path}")

    # 读取尾部数据（大文件截断，单文件上限 _MAX_BYTES）
    lines = _read_tail(path)
    truncated = len(lines) == 0 and os.path.getsize(path) > _MAX_BYTES
    # 若单文件被截断或天数>1，尝试合并轮转日志 access.log.1 补充近两天
    if days > 1:
        rotated = os.path.join(os.path.dirname(path), "access.log.1")
        if os.path.isfile(rotated):
            lines = _read_tail(rotated) + lines

    cut_date = datetime.now() - timedelta(days=days)
    result = _aggregate(lines, days=days, domain=domain, cut_date=cut_date)
    result["file"] = unhost_path(path)
    result["days"] = days
    result["domain"] = domain
    result["lines"] = len(lines)
    result["truncated"] = truncated
    return result
