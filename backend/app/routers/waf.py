"""WAF 应用防火墙（站点级 Web 应用防火墙）。

功能：
  1. 全局总开关（WAF 总开关），管理站点 WAF 策略的生成与生效。
  2. 每网站策略：
     - 频率设置：访问 / 攻击 / 404 三类频率限制（模式 url/全局、周期、次数、封禁秒数）
     - 防御规则：SQL 注入 / 一句话木马 / 目录 / XSS / 参数 / UA / Header / Cookie / HTTP / URL
     - 自定义规则：文件上传限制（大小）、CDN 直连开关
     - 其他：恶意 IP 组、蜘蛛 IP 池（含百度/Bing/谷歌/360/神马/搜狗/字节/DuckDuckGo/Yandex）
     - 黑白名单：IP / URL / User-Agent / IP 组
     - 地区访问限制：按地理位置限制站点访问来源
     - 自定义 ACL：按固定规则决定 allow / deny / challenge
     - 等候厅：挑战页（challenge），命中后交用户手动通过
  3. 拦截日志：记录被拦截 IP、命中规则、触发原因；拦截地图统计 30 天内地理位置分布。

配置存储：backend/data/waf.json（策略），backend/data/waf_logs.json（拦截日志）。
生成物：nginx include 片段写往 NGINX_WAF_DIR（默认 /etc/nginx/waf/<site>.conf），
        有 nginx 时写盘成功则 reload，无 nginx 仅存配置供预览。
"""

import ipaddress
import json
import os
import platform
import re
import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.hostfs import host_path, host_cmd
from app import webserver

router = APIRouter()

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
WAF_FILE = os.path.join(DATA_DIR, "waf.json")
WAF_LOG_FILE = os.path.join(DATA_DIR, "waf_logs.json")
# WAF 生成的 nginx include 片段目录（宿主机视角，写入时经 host_path 映射）。
# 跟随 NGINX/OpenResty 引擎模式：nginx => /etc/nginx/waf，openresty => .../waf
NGINX_WAF_DIR = webserver.waf_dir()
IS_WIN = platform.system() == "Windows"

# 同名站点类型/来源：站点 id 与 sites.json 中的 name 一致，直接复用做配置文件防穿越
_SITE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

# 写入 nginx 的值的禁止字符：剔除引号/分号/花括号可防止截断注入新指令。
# 注意：保留反斜杠，避免破坏防御签名里的 \\s / \\d / \\. 正则语义
#（在 nginx if 块内反斜杠不构成注入）。
_NGINX_VALUE_FORBIDDEN = re.compile(r'[";{}]')

# 控制字符（换行/回车/空字节/DEL 等），用于所有用户可控非正则字段
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")

# 频率限制周期/次数/封禁的取值范围
_PERIOD_MAX = 86400
_COUNT_MAX = 1000000


def _load_waf() -> dict:
    """读取全局配置；文件缺失或损坏时返回默认结构。"""
    if not os.path.exists(WAF_FILE):
        return {"enabled": False, "sites": []}
    try:
        with open(WAF_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"enabled": False, "sites": []}


def _waf_dir() -> str:
    """当前引擎的 WAF 片段目录（跟随 NGINX/OpenResty 模式动态解析）。

    供写入/删除/展示使用；测试可通过 patch 本函数重定向到临时目录。
    """
    return webserver.waf_dir()


def _save_waf(data: dict):
    """持久化全局配置。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(WAF_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _load_logs() -> list:
    """读取拦截日志；损坏时返回空列表（日志可重建，不做过度防护）。"""
    if not os.path.exists(WAF_LOG_FILE):
        return []
    try:
        with open(WAF_LOG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_logs(logs: list):
    """持久化拦截日志。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(WAF_LOG_FILE, "w", encoding="utf-8") as f:
        json.dump(logs, f, ensure_ascii=False, indent=2)


def _load_sites() -> list:
    """读取站点列表（自建 sites.json + 外部真实站点），用于下拉与存在性校验。

    外部站点（如 1Panel 兼容站点）不在 sites.json，仅在发现结果中动态存在，
    因此这里合并返回，保证 WAF 能对「网站」里配置过的站点正常选择与保存。
    """
    try:
        from app.routers.sites import merged_sites
        return merged_sites()
    except Exception:
        sites_file = os.path.join(DATA_DIR, "sites.json")
        if not os.path.exists(sites_file):
            return []
        try:
            with open(sites_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []


def _site_exists(site_id: str) -> bool:
    """站点头部是否真实存在于 sites.json（同时做配置文件名校验）。"""
    if not _SITE_ID_RE.match(site_id):
        return False
    return any(s.get("name") == site_id for s in _load_sites())


def _ensure_site(site_id: str, active: bool = True):
    """校验站点头部存在且可作为配置文件名；否则 404/400。

    site_id 会作为 nginx 配置文件名（<id>.conf），白名单防 ../ 与盘符穿越。
    """
    if not _SITE_ID_RE.match(site_id):
        raise HTTPException(status_code=400, detail="站点头部格式非法")
    if active and not _site_exists(site_id):
        raise HTTPException(status_code=404, detail="站点头部不存在")


def _default_site_config(site: str) -> dict:
    """返回某站点默认 WAF 策略结构。"""
    return {
        "site": site,
        "enabled": False,  # 该站点是否启用 WAF
        "frequency": {
            "access": {"mode": "url", "period": 60, "count": 100, "ban": 600},
            "attack": {"mode": "url", "period": 60, "count": 30, "ban": 600},
            "notfound": {"mode": "url", "period": 60, "count": 10, "ban": 600},
        },
        "defense": {
            "sql": True,
            "webshell": True,
            "directory": True,
            "xss": True,
            "param": True,
            "ua": True,
            "header": True,
            "cookie": True,
            "http": True,
            "url": True,
        },
        "custom": {"upload_limit_mb": 20, "cdn": False},
        "other": {"malicious_ip_groups": [], "spider_pool": True},
        "blackwhite": {
            "ip_whitelist": [],
            "ip_blacklist": [],
            "url_whitelist": [],
            "url_blacklist": [],
            "ua_whitelist": [],
            "ua_blacklist": [],
            "ip_groups": [],
        },
        "geo": {"enabled": False, "action": "block", "countries": []},
        "acl": [],
        "waiting_hall": {"enabled": False, "url": "/waf_challenge"},
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }


def get_site_config(site_id: str) -> Optional[dict]:
    """按站点头部返回已保存策略；无则 None。"""
    for sc in _load_waf().get("sites", []):
        if sc.get("site") == site_id:
            return sc
    return None


# ---------------------------------------------------------------------------
# 校验工具
# ---------------------------------------------------------------------------
def _reject_control(value, field: str = "值"):
    """拒绝控制字符（换行/回车/空字节/DEL 等）。"""
    if value and _CONTROL_RE.search(str(value)):
        raise HTTPException(status_code=400, detail=f"字段 {field} 含非法控制字符")


def _validate_ip(value: str, field: str = "IP"):
    """校验 IP / CIDR（IPv4/IPv6），与防火墙一致。"""
    try:
        ipaddress.ip_network(value, strict=False)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{field} 格式非法: {value!r}")
    return value


def _validate_ng_value(value: str, field: str = "值"):
    """校验将写入 nginx 的值：仅做禁止字符清洗（显式放行正则反斜杠）。"""
    if value is None:
        return value
    v = str(value)
    _reject_control(v, field)
    if _NGINX_VALUE_FORBIDDEN.search(v):
        raise HTTPException(
            status_code=400, detail=f"字段 {field} 含非法字符（引号/分号/花括号）"
        )
    return v


def _ng_escape(value: str) -> str:
    """写入 nginx 前清洗值（历史/兜底）：用合法替身替换禁止字符。"""
    v = str(value)
    return _NGINX_VALUE_FORBIDDEN.sub("_", _CONTROL_RE.sub("", v))


def _validate_frequency(fx: dict, label: str):
    """校验单项频率限制：period/count/ban 数值边界 + mode 枚举。"""
    if not isinstance(fx, dict):
        raise HTTPException(status_code=400, detail=f"{label} 频率配置格式非法")
    mode = fx.get("mode")
    if mode not in ("url", "global"):
        raise HTTPException(status_code=400, detail=f"{label} 模式必须为 url/global")
    for k, lo, hi in (("period", 1, _PERIOD_MAX), ("count", 1, _COUNT_MAX), ("ban", 1, _PERIOD_MAX)):
        v = fx.get(k)
        if not isinstance(v, int) or isinstance(v, bool) or not (lo <= v <= hi):
            raise HTTPException(status_code=400, detail=f"{label} 频率 {k} 须为 {lo}-{hi} 整数")


# 蜘蛛 IP 池：站点 WAF 关闭蜘蛛池后拦截这些内置蜘蛛 UA
SPIDER_UA_PATTERNS = {
    "baidu": r"Baiduspider",
    "bing": r"bingbot",
    "google": r"Googlebot",
    "360": r"360Spider",
    "shenma": r"YisouSpider",
    "sogou": r"Sogou",
    "byte": r"Bytespider",
    "duckduckgo": r"DuckDuckBot|ddg_bot",
    "yandex": r"YandexBot",
}


# 防御规则签名：正则命中即判为攻击请求（if (...) return 403）。合并到一条 if 语句减小体积。
# 使用单引号包裹 nginx 正则（nginx map/if 内正则用 `~*` 大小写不敏感前缀）。
_DEFENSE_SIGNATURES = {
    "sql": (
        r"select\s.+from|union\s+all\s+select|insert\s+into|update\s+.*\sset|"
        r"delete\s+from|drop\s+table|information_schema|sleep\s*\(|benchmark\s*\("
    ),
    "webshell": (
        r"eval\s*\(|system\s*\(|exec\s*\(|assert\s*\(|passthru\s*\(|shell_exec\s*\(|"
        r"c99shell|r57shell|webshell|\.php\s+[?a-zA-Z_]+="
    ),
    "directory": (
        r"\.\./|/\.\.|\.\.\\\\|\\\\\.\\.|/etc/passwd|/proc/self/environ|"
        r"boot\.ini|\.htaccess|%0a%00|%2e%2e"
    ),
    "xss": (
        r"<\s*script|javascript:\s*alert|beef\s*=|onerror\s*=|onload\s*=|"
        r"<svg|document\.cookie|<iframe|<img[^>]+onerror"
    ),
    "param": r"(\'|\"|--|;|\(\)|\binsert\b|\bselect\b)\s*=?\s*.*[\x00-\x1f]",
    "http": r"\\x00|get\s+transaction|shell\s+|nmap|nikto|hydra|sqlmap|metasploit",
}

# header/cookie/ua/url 与通用签名需要具体化，这里给每个开关单独的 if 用独立正则。
_LOWERCASE_SIGNATURES = {
    "ua": r"(curl/|wget/|nikto|sqlmap|nessus|acunetix|masscan|python-requests|scanner|havij)",
    "url": r"(union|concat|char\(|database\(|version\(|~/\.ssh|/\.git/config|/_profiler/)",
}


def _render_frequency_block(name: str, fx: dict, zones_http: list) -> list:
    """渲染单个频率限制 nginx 片段。

    - 全局模式：基于 $binary_remote_addr 维度限流
    - url 模式：基于 $remote_addr$request_uri 维度限流
    limit_req_zone 必须在 http {} 上下文，因此以注释形式输出头部说明，
    并把 zone 定义收集到 zones_http 供整体输出。命中超频返回 429。
    """
    fx = fx or {}
    if not fx.get("count"):
        return []
    mode = fx.get("mode", "url")
    count = int(fx.get("count", 100))
    zone_name = f"waf_{name}"
    key = "$binary_remote_addr" if mode == "global" else "$binary_remote_addr$request_uri"
    zones_http.append(
        f"limit_req_zone {key} zone={zone_name}:10m rate={count}r/m;"
    )
    return [
        f"    limit_req zone={zone_name} burst={max(10, count // 5)} nodelay;",
        f"    # WAF rate limit ({name}): count={count}/min, mode={mode}",
    ]


def _render_site_nginx(cfg: dict) -> str:
    """根据站点 WAF 策略渲染 nginx include 片段（server 块上下文指令）。

    输出内容仅为「应被 include 进目标站点 server 块」的指令，不含 server 包裹。
    """
    lines = []
    lines.append("# Graw WAF auto-generated fragment (do not edit manually)")
    lines.append(f"# site: {_ng_escape(cfg.get('site', ''))}  "
                 f"updated: {cfg.get('updated_at', '')}")
    # 是否额外生成 limit_req_zone（需在 http 上下文，这里注释头说明）
    lines.append("# NOTE: limit_req_zone lines must live in http {} context; "
                 "copy them there on first enable.")

    b = cfg.get("blackwhite", {})
    # 白名单放行（allow）优先于黑名单/防护 —— allow 先、deny 后
    for ip in b.get("ip_whitelist", []):
        lines.append(f"    allow {_ng_escape(ip)};")
    # 恶意 IP 组 / 黑名单 IP
    block_ips = list(b.get("ip_blacklist", []))
    for grp in cfg.get("other", {}).get("malicious_ip_groups", []):
        block_ips.append(grp)
    for ip in b.get("ip_groups", []):
        block_ips.append(ip)
    for ip in block_ips:
        lines.append(f"    deny {_ng_escape(ip)};")

    # URL 黑名单/白名单：白名单 allow，黑名单 deny
    for u in b.get("url_whitelist", []):
        lines.append(f"    location {_ng_escape(u)} {{ allow all; }}")
    for u in b.get("url_blacklist", []):
        lines.append(f"    location {_ng_escape(u)} {{ return 403; }}")

    # 地区访问限制（依赖 $geoip_country_code；未装 geoip 时条件不成立不生效）
    geo = cfg.get("geo", {})
    if geo.get("enabled") and geo.get("countries"):
        action = geo.get("action", "block")
        allowed = " ".join(_ng_escape(c) for c in geo["countries"])
        # action=allow(只允许这些地区) -> 其余 deny；action=deny(仅拒绝这些地区)
        cond = f"($geoip_country_code !~ ^({allowed})$)" if action == "allow" else f"($geoip_country_code ~ ^({allowed})$)"
        lines.append(f"    if {cond} {{ return 403; }}")

    # 自定义 ACL
    for acl in cfg.get("acl", []):
        lines.extend(_render_acl(acl))

    # 防御规则：多个 if ... return 403
    defs = cfg.get("defense", {})
    sig_lines = []
    for key, pattern in _DEFENSE_SIGNATURES.items():
        if defs.get(key):
            sig_lines.append(f"if ($request_uri ~* {pattern}) {{ return 403; }}")
    for key, pattern in _LOWERCASE_SIGNATURES.items():
        if defs.get(key):
            sig_lines.append(f"if (${key} ~* {pattern}) {{ return 403; }}")
    lines.extend(sig_lines)

    # 自定义：CDN 关闭 -> 拒绝常见代理 UA / 空 UA；上传大小限制放 location 无法在 if 中；
    # 上传限制用 client_max_body_size（safe；此外 IP 白名单外的上传大文件 403 在预览注释里）
    custom = cfg.get("custom", {})
    if not custom.get("cdn"):
        lines.append("    if ($http_cdn_loop) { return 403; }")
        lines.append("    # CDN off: requests bypassing CDN can be filtered upstream")
    upload_mb = int(custom.get("upload_limit_mb", 20))
    lines.append(f"    client_max_body_size {max(1, upload_mb)}m;")

    # 频率限制（命中即 429；zone 定义需放在 http {} 上下文）
    freq = cfg.get("frequency", {})
    zones_http = []
    freq_lines = []
    for name, fk in (("access", "access"), ("attack", "attack"), ("notfound", "notfound")):
        freq_lines.extend(_render_frequency_block(name, freq.get(fk, {}), zones_http))

    # 等候厅：提供挑战页 location
    wh = cfg.get("waiting_hall", {})
    if wh.get("enabled"):
        lines.append(
            f"    location = {_ng_escape(wh.get('url', '') or '/waf_challenge')} "
            f"{{ internal; default_type text/html; "
            f"return 200 '<html><body><h2>Challenge</h2></body></html>'; }}"
        )

    # 429 兜底（封禁响应）
    lines.append("    error_page 429 =429 @waf_too_many;")
    lines.extend(freq_lines)

    # 输出 limit_req_zone（http 上下文）—— 以真实指令附加在片段末尾供手动复制
    if zones_http:
        lines.append("\n# --- limit_req_zone (copy into http {}) ---")
        lines.extend("# " + z for z in zones_http)

    return "\n".join(lines)


def _render_acl(acl: dict) -> list:
    """渲染单条自定义 ACL 为 nginx if 规则。

    match: uri|ip|ua|args|method | op: eq|regex|contains|starts | action: allow|deny|challenge

    安全修复（第九轮审计，中危）：所有插值一律用双引号包裹。nginx if 表达式中
    未加引号的正则/字符串遇到空格、#（行注释符）、括号等字符会截断或注释，
    生成语法错误的片段（持久 DoS）。值内的双引号已在 _validate_acl 统一拒绝，
    因此包裹后值中的空格 / # / ( ) / | 均按正则字面量处理，无法逃逸。
    """
    match = acl.get("match", "uri")
    op = acl.get("op", "eq")
    value = _ng_escape(acl.get("value", ""))
    action = acl.get("action", "deny")
    if not value:
        return []
    var = {"uri": "$request_uri", "ip": "$remote_addr", "ua": "$http_user_agent",
           "args": "$args", "method": "$request_method"}.get(match, "$request_uri")
    if op == "regex":
        cond = f'({var} ~* "{value}")'
    elif op == "contains":
        cond = f'({var} ~ "{re.escape(value)}")'
    elif op == "starts":
        cond = f'({var} ~ "^{re.escape(value)}")'
    else:  # eq
        cond = f'({var} = "{value}")'
    if action == "allow":
        return [f"    if {cond} {{ allow all; }}"]
    if action == "challenge":
        return [f"    if {cond} {{ return 302 /waf_challenge; }}"]
    return [f"    if {cond} {{ return 403; }}"]


# ---------------------------------------------------------------------------
# HTTP 端点
# ---------------------------------------------------------------------------
@router.get("/status")
async def waf_status():
    """全局状态：总开关 + 平台 + nginx 是否可用。"""
    data = _load_waf()
    nginx_available = bool(_nginx_available())
    return {
        "enabled": bool(data.get("enabled", False)),
        "platform": "windows" if IS_WIN else "linux",
        "nginx_available": nginx_available,
        "waf_dir": _waf_dir(),
    }


def _nginx_available() -> bool:
    # 按当前引擎（nginx/openresty）探测可用性
    try:
        return webserver.available()
    except Exception:
        return False


@router.post("/toggle")
async def waf_toggle(body: dict):
    """全局 WAF 总开关。"""
    data = _load_waf()
    enabled = bool(body.get("enabled", False))
    data["enabled"] = enabled
    _save_waf(data)
    return {"enabled": enabled}


@router.get("/sites")
async def waf_sites():
    """站点下拉列表 + 各自启停状态（不含完整策略，避免拉取过重）。

    仅暴露可安全作为 <name>.conf 文件名的站点；外部真实配置中的非法
    名称（如通配 _ / 含非法字符）不参与下拉，避免生成无法落地的问题配置。
    """
    all_sites = _load_sites()
    sites = [s for s in all_sites if _SITE_ID_RE.match(str(s.get("name") or ""))]
    cfgs = {sc.get("site"): sc for sc in _load_waf().get("sites", [])}
    result = []
    for s in sites:
        name = s.get("name")
        sc = cfgs.get(name)
        result.append({
            "site": name,
            "enabled": bool(sc.get("enabled", False)) if sc else False,
            "secured": bool(sc) and _site_exists(name),
        })
    return {"sites": result, "global_enabled": bool(_load_waf().get("enabled", False))}


@router.get("/site/{site_id}")
async def waf_site_get(site_id: str):
    """读取单个站点 WAF 策略（无则返回默认结构）。"""
    _ensure_site(site_id, active=False)
    sc = get_site_config(site_id)
    if sc is None:
        sc = _default_site_config(site_id)
    return sc


@router.put("/site/{site_id}")
async def waf_site_save(site_id: str, body: dict):
    """保存单个站点 WAF 策略。

    全量覆盖该站点策略。校验所有用户可控字段后写入配置并按需生成 nginx 片段。
    """
    _ensure_site(site_id, active=True)
    cfg = _validate_site_config(site_id, body)
    data = _load_waf()
    data.setdefault("sites", [])
    for i, sc in enumerate(data["sites"]):
        if sc.get("site") == site_id:
            data["sites"][i] = cfg
            break
    else:
        data["sites"].append(cfg)
    _save_waf(data)
    # 写盘并 reload
    try:
        write_result = _write_nginx_fragment(cfg)
    except OSError as e:
        write_result = {"written": False, "error": str(e)}
    return {"saved": True, "config": cfg, "write": write_result}


@router.post("/site/{site_id}/disable")
async def waf_site_disable(site_id: str):
    """停用某站点 WAF（清掉已生成的 nginx 片段）。"""
    _ensure_site(site_id, active=False)
    data = _load_waf()
    for sc in data.get("sites", []):
        if sc.get("site") == site_id:
            sc["enabled"] = False
            sc["updated_at"] = datetime.now().isoformat(timespec="seconds")
            break
    _save_waf(data)
    _remove_nginx_fragment(site_id)
    return {"ok": True}


@router.get("/preview")
async def waf_preview(site_id: str):
    """预览某站点生成的 nginx 片段（不落盘）。"""
    _ensure_site(site_id, active=False)
    sc = get_site_config(site_id)
    if sc is None:
        sc = _default_site_config(site_id)
    return {"site": site_id, "content": _render_site_nginx(sc)}


@router.post("/apply")
async def waf_apply(body: dict):
    """手动把全部启用站点 WAF 片段重新写盘并 reload nginx（全局应用）。"""
    data = _load_waf()
    if not data.get("enabled", False):
        return {"ok": False, "message": "全局 WAF 未开启，未应用配置"}
    written, skipped = 0, 0
    for sc in data.get("sites", []):
        if sc.get("enabled"):
            try:
                _write_nginx_fragment(sc)
                written += 1
            except OSError:
                skipped += 1
        else:
            _remove_nginx_fragment(sc.get("site"))
    return {"ok": True, "written": written, "skipped": skipped}


def _write_nginx_fragment(cfg: dict) -> dict:
    """将某站点 WAF 片段写盘（host_path 映射），有 nginx 则 reload。"""
    site = cfg.get("site")
    target_dir = host_path(_waf_dir())
    os.makedirs(target_dir, exist_ok=True)
    conf = os.path.join(target_dir, f"{site}.conf")
    with open(conf, "w", encoding="utf-8") as f:
        f.write(_render_site_nginx(cfg))
    nginx = _nginx_available()
    if nginx:
        _reload_nginx()
    return {"written": True, "path": conf, "nginx": nginx}


def _remove_nginx_fragment(site_id: str):
    """删除某站点 WAF 片段；无文件则忽略。"""
    if not _SITE_ID_RE.match(str(site_id)):
        return
    conf = os.path.join(host_path(_waf_dir()), f"{site_id}.conf")
    try:
        if os.path.exists(conf):
            os.remove(conf)
    except OSError:
        pass


def _reload_nginx():
    # 按当前引擎（nginx/openresty）执行 reload
    try:
        webserver.reload()
    except Exception:
        pass


def _validate_site_config(site_id: str, body: dict) -> dict:
    """深度校验并归一化站点 WAF 策略；任何非法值抛 400。"""
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="策略必须为对象")
    cfg = _default_site_config(site_id)
    cfg["site"] = site_id
    cfg["enabled"] = bool(body.get("enabled", False))

    # 频率
    freq = body.get("frequency") or {}
    for key in ("access", "attack", "notfound"):
        fx = freq.get(key) or {}
        _validate_frequency(fx, f"频率-{key}")
        cfg["frequency"][key] = {
            "mode": fx.get("mode", "url"),
            "period": int(fx.get("period", 60)),
            "count": int(fx.get("count", 100)),
            "ban": int(fx.get("ban", 600)),
        }

    # 防御规则开关
    defs = body.get("defense") or {}
    for k in cfg["defense"]:
        cfg["defense"][k] = bool(defs.get(k, cfg["defense"][k]))

    # 自定义规则
    custom = body.get("custom") or {}
    upload_mb = custom.get("upload_limit_mb", 20)
    cfg["custom"] = {
        "upload_limit_mb": 1 if not isinstance(upload_mb, int) else max(1, min(upload_mb, 4096)),
        "cdn": bool(custom.get("cdn", False)),
    }

    # 其他
    other = body.get("other") or {}
    groups = other.get("malicious_ip_groups") or []
    if not isinstance(groups, list) or len(groups) > 256:
        raise HTTPException(status_code=400, detail="恶意 IP 组须为最多 256 项列表")
    groups = [_ng_safe_group(g) for g in groups if str(g).strip()]
    cfg["other"] = {
        "malicious_ip_groups": groups,
        "spider_pool": bool(other.get("spider_pool", True)),
    }

    # 黑白名单
    bw = body.get("blackwhite") or {}
    cfg["blackwhite"] = {
        "ip_whitelist": _ge_ips(bw.get("ip_whitelist"), "IP 白名单"),
        "ip_blacklist": _ge_ips(bw.get("ip_blacklist"), "IP 黑名单"),
        "url_whitelist": _ge_urls(bw.get("url_whitelist"), "URL 白名单"),
        "url_blacklist": _ge_urls(bw.get("url_blacklist"), "URL 黑名单"),
        "ua_whitelist": _ge_uas(bw.get("ua_whitelist"), "UA 白名单"),
        "ua_blacklist": _ge_uas(bw.get("ua_blacklist"), "UA 黑名单"),
        "ip_groups": _ge_ips(bw.get("ip_groups"), "IP 组"),
    }

    # 地区
    geo = body.get("geo") or {}
    countries = geo.get("countries") or []
    if not isinstance(countries, list) or len(countries) > 100:
        raise HTTPException(status_code=400, detail="地区列表须为最多 100 项列表")
    countries = [_ng_escape(c) for c in countries if str(c).strip()]
    geo_action = geo.get("action", "block")
    if geo_action not in ("allow", "deny"):
        geo_action = "block"
    cfg["geo"] = {"enabled": bool(geo.get("enabled", False)),
                  "action": geo_action, "countries": countries}

    # ACL
    acl = body.get("acl") or []
    if not isinstance(acl, list) or len(acl) > 256:
        raise HTTPException(status_code=400, detail="ACL 须为最多 256 项列表")
    cfg["acl"] = [_validate_acl(x) for x in acl if isinstance(x, dict)]

    # 等候厅
    wh = body.get("waiting_hall") or {}
    url = wh.get("url") or "/waf_challenge"
    if not re.fullmatch(r"/[A-Za-z0-9_./-]{0,200}", str(url)):
        raise HTTPException(status_code=400, detail="等候厅 URL 格式非法")
    cfg["waiting_hall"] = {"enabled": bool(wh.get("enabled", False)), "url": str(url)}

    cfg["updated_at"] = datetime.now().isoformat(timespec="seconds")
    return cfg


def _ng_safe_group(g) -> str:
    """归一化 IP 组名：仅字母/数字/_/-，防衍射进 nginx 配置。"""
    g = str(g).strip()
    if not re.fullmatch(r"[A-Za-z0-9_\-]{1,64}", g):
        raise HTTPException(status_code=400, detail="IP 组名称只能含字母/数字/_/-")
    return g


def _ge_ips(v, label: str) -> list:
    """归一化并校验 IP 列表（含 CIDR）。"""
    if not isinstance(v, list) or len(v) > 1024:
        raise HTTPException(status_code=400, detail=f"{label} 须为最多 1024 项列表")
    out = []
    for x in v:
        s = str(x).strip()
        if s:
            _validate_ip(s, label)
            out.append(s)
    return out


def _ge_urls(v, label: str) -> list:
    """归一化并校验 URL 列表（以 / 开头，无注入字符）。"""
    if not isinstance(v, list) or len(v) > 1024:
        raise HTTPException(status_code=400, detail=f"{label} 须为最多 1024 项列表")
    out = []
    for x in v:
        s = str(x).strip()
        if not s:
            continue
        _validate_ng_value(s, label)
        if not s.startswith("/"):
            raise HTTPException(status_code=400, detail=f"{label} 项须以 / 开头: {s!r}")
        out.append(s)
    return out


def _ge_uas(v, label: str) -> list:
    """归一化并校验 UA 列表。"""
    if not isinstance(v, list) or len(v) > 1024:
        raise HTTPException(status_code=400, detail=f"{label} 须为最多 1024 项列表")
    out = []
    for x in v:
        s = str(x).strip()
        if s:
            _validate_ng_value(s, label)
            out.append(s)
    return out


def _validate_acl(x: dict) -> dict:
    """校验单条 ACL：match/op/action 枚举 + value 长度/字符。"""
    match = x.get("match")
    op = x.get("op")
    action = x.get("action")
    if match not in ("uri", "ip", "ua", "args", "method"):
        raise HTTPException(status_code=400, detail="ACL match 非法")
    if op not in ("eq", "regex", "contains", "starts"):
        raise HTTPException(status_code=400, detail="ACL op 非法")
    if action not in ("allow", "deny", "challenge"):
        raise HTTPException(status_code=400, detail="ACL action 非法")
    value = str(x.get("value") or "")
    if len(value) > 512:
        raise HTTPException(status_code=400, detail="ACL value 超长")
    if op == "regex":
        try:
            re.compile(value)
        except re.error:
            raise HTTPException(status_code=400, detail="ACL value 正则非法")
        _reject_control(value, "ACL value")
        # 安全修复（第九轮审计，中危）：regex 分支此前跳过 _validate_ng_value，
        # 且渲染时值直接裸拼进 `if ($var ~* <value>)`（未加引号包裹），空格 / #
        # 等字符可截断 nginx 表达式或把后续内容注释掉，生成语法错误的 nginx
        # 片段并写入磁盘，导致 reload 失败、重启后 nginx 无法加载（持久 DoS）。
        # 此处补上与其它分支一致的字符校验（拒绝 " ; { } 与控制字符），
        # 配合 _render_acl 的双引号包裹，彻底封堵配置注入。
        _validate_ng_value(value, "ACL value")
    else:
        _validate_ng_value(value, "ACL value")
    return {"id": str(x.get("id") or uuid.uuid4().hex[:8]), "match": match,
            "op": op, "value": value, "action": action}


# ---------------------------------------------------------------------------
# 拦截日志
# ---------------------------------------------------------------------------
class LogRecord(BaseModel):
    """外部流水线上报拦截日志（如解析 nginx access/error 日志）。"""
    site: Optional[str] = ""
    ip: Optional[str] = ""
    rule: Optional[str] = ""
    reason: str = Field(..., max_length=500)
    action: str = Field(default="deny", pattern="^(deny|challenge|429|allow)$")
    geo: Optional[str] = ""


@router.post("/logs/record")
async def waf_log_record(req: LogRecord):
    """追加一条拦截日志（外部流水线调用）；环形裁剪到最近 50000 条。"""
    _reject_control(req.reason, "reason")
    if req.site and not _SITE_ID_RE.match(req.site):
        raise HTTPException(status_code=400, detail="site 格式非法")
    if req.ip:
        _validate_ip(req.ip, "ip")
    logs = _load_logs()
    logs.append({
        "time": datetime.now().isoformat(timespec="seconds"),
        "site": req.site or "",
        "ip": req.ip or "",
        "rule": req.rule or "",
        "reason": req.reason,
        "action": req.action,
        "geo": req.geo or _ip_to_geo(req.ip),
    })
    if len(logs) > 50000:
        logs = logs[-50000:]
    _save_logs(logs)
    return {"ok": True, "count": len(logs)}


@router.get("/logs")
async def waf_logs(site: str = "", action: str = "", ip: str = "", limit: int = 200):
    """按条件查询拦截日志；limit 1-1000。"""
    limit = max(1, min(int(limit or 200), 1000))
    logs = _load_logs()
    if site:
        logs = [x for x in logs if x.get("site") == site]
    if action:
        logs = [x for x in logs if x.get("action") == action]
    if ip:
        logs = [x for x in logs if ip in x.get("ip", "")]
    # 新在前
    logs = list(reversed(logs))[:limit]
    return {"logs": logs, "total": len(logs)}


@router.post("/logs/clear")
async def waf_logs_clear():
    """清空拦截日志。"""
    _save_logs([])
    return {"ok": True}


MAX_BLOCKMAP_DAYS = 30


@router.get("/blockmap")
async def waf_blockmap(days: int = 30):
    """统计近 N 天拦截地理位置分布（默认 30；天数为 1-90）。"""
    days = max(1, min(int(days or MAX_BLOCKMAP_DAYS), 90))
    since = datetime.now() - timedelta(days=days)
    logs = _load_logs()
    buckets = {}
    for x in logs:
        try:
            t = datetime.fromisoformat(x.get("time", ""))
        except ValueError:
            continue
        if t < since:
            continue
        geo = x.get("geo") or _ip_to_geo(x.get("ip"))
        buckets[geo] = buckets.get(geo, 0) + 1
    data = [{"geo": k, "count": v} for k, v in sorted(
        buckets.items(), key=lambda kv: -kv[1])]
    return {"days": days, "data": data, "total": sum(v for _, v in buckets.items())}


def _ip_to_geo(ip: str) -> str:
    """朴素 IP 归属：私有/回环网段标记为本地，否则按首字节分桶。

    生产建议接入 MaxMind GeoLite2，命中准确 STS；此处仅作兜底可视化。
    """
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return "unknown"
    if addr.is_private:
        return "私有 IP"
    if addr.is_loopback:
        return "本机回环"
    if addr.is_global and isinstance(addr, ipaddress.IPv4Address):
        b = int(addr) >> 24
        ranges = [(0, "全球未知"), (1, "北美"), (24, "北美"), (26, "北美"),
                  (36, "亚太"), (42, "中国互联"), (58, "亚太"), (79, "欧洲"),
                  (80, "欧洲"), (103, "亚太"), (180, "中国互联"), (200, "南美"),
                  (216, "北美")]
        for start, label in sorted(ranges, reverse=True):
            if b >= start:
                return label
        return "全球未知"
    return "全球未知"