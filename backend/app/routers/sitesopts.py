# -*- coding: utf-8 -*-
"""
sitesopts.py - 站点增强配置（防盗链 / gzip / 静态资源缓存）

为网站提供三项常见优化，一键应用到站点（写入 nginx 配置并 reload）：
  1. 防盗链（hotlink）：仅允许指定来源域名引用静态资源，杜绝盗链消耗带宽。
  2. gzip 压缩：对文本类静态资源开启 gzip 传输，降低带宽占用。
  3. 浏览器缓存（cache_expire）：为图片/CSS/JS 等静态资源设置 Cache-Control 过期时间。

安全设计：
  - 所有可配置字段（允许来源域名）走白名单校验，绝不允许任意文本进配置，
    从源头阻断「配置 → nginx 指令注入」攻击链（与 rewrite.py 同思路）。
  - 生成片段由本模块唯一负责，sites._nginx_site_config 仅做拼装与注入，
    保证「站点数据 → 配置片段」的转换有且只有一条受控路径。
"""
import json
import os
import re
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter()

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
SITES_FILE = os.path.join(DATA_DIR, "sites.json")
# 外部站点（1Panel 兼容等，来自真实 nginx 配置，不在 sites.json）的增强配置，
# 单独持久化于此，避免每次重新发现站点时配置丢失。
SITESOPTS_EXT_FILE = os.path.join(DATA_DIR, "sitesopts_external.json")

# 域名白名单（支持通配子域 *.example.com）：与 sites 路由保持一致
_DOMAIN_RE = re.compile(
    r"^(\*\.)?([A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?\.)+[A-Za-z]{2,63}\Z"
)

# 静态资源扩展名白名单（防盗链 / 缓存匹配目标）
_STATIC_EXTS = (
    "jpg|jpeg|png|gif|webp|bmp|ico|svg|css|js|mjs|mp3|mp4|webm|zip|rar|7z"
    "|woff|woff2|ttf|eot|pdf|doc|docx|xls|xlsx"
)

# 缓存时长上限：7 天（秒）。超过按上限处理，防止异常值产生无效配置
MAX_CACHE_SECONDS = 86400 * 7


def _sanitize_domain(d: str) -> Optional[str]:
    """清洗允许来源域名：仅放行域名白名单（含通配子域），非法丢弃。"""
    d = (d or "").strip().lower()
    if _DOMAIN_RE.match(d):
        return d
    return None


def _cache_expires(seconds: int) -> str:
    """将秒数转为 nginx expires 时长；0/非法返回空串（不生成缓存行）。"""
    seconds = int(seconds or 0)
    if seconds <= 0:
        return ""
    seconds = min(seconds, MAX_CACHE_SECONDS)
    if seconds % 86400 == 0:
        return f"{seconds // 86400}d"
    if seconds % 3600 == 0:
        return f"{seconds // 3600}h"
    if seconds % 60 == 0:
        return f"{seconds // 60}m"
    return f"{seconds}s"


def get_nginx_extra(site: dict) -> str:
    """根据站点增强配置生成注入 nginx server 块的片段；无可配置项返回空串。

    仅对静态网址 / 子网站生效（反向代理无静态文件语义）。
    所有插值均经白名单/数值校验，绝不出现任意文本。
    """
    site_type = site.get("type", "static")
    if site_type not in ("static", "subsite"):
        return ""

    hotlink = site.get("hotlink") or {}
    gzip_enabled = bool(site.get("gzip", {}).get("enabled"))
    cache_seconds = int(site.get("cache_expire") or 0)

    block = []
    need_static_location = bool(hotlink.get("enabled")) or cache_seconds > 0
    if need_static_location:
        # 防盗链：valid_referers 白名单（none=无来源 / blocked=跨域空来源）
        lines = [
            "# 防盗链 + 静态资源缓存（面板站点增强配置）",
            f"    location ~* \\.({_STATIC_EXTS})$ {{",
        ]
        if hotlink.get("enabled"):
            allowed = [s for s in (hotlink.get("allowed") or []) if s]
            # 清洗后的白名单域名（非法值丢弃）
            domains = []
            for d in allowed:
                clean = _sanitize_domain(d)
                if clean:
                    domains.append(clean)
            refs = "server_names"
            if hotlink.get("allow_empty_referer", False):
                refs = "none blocked " + refs
            if domains:
                refs = refs + " " + " ".join(domains)
            lines.append(f"        valid_referers {refs};")
            lines.append(f"        if ($invalid_referer) {{")
            lines.append(f"            return 403;")
            lines.append(f"        }}")
        exp = _cache_expires(cache_seconds)
        if exp:
            lines.append(f"        expires {exp};")
        lines.append(f"    }}")
        block.append("\n".join(lines))

    if gzip_enabled:
        # gzip：对文本类资源开启压缩（gzip_types 固定白名单）
        lines = [
            "# gzip 压缩（面板站点增强配置）",
            "    gzip on;",
            "    gzip_min_length 1024;",
            "    gzip_types text/css application/javascript application/json application/xml application/x-font-woff image/svg+xml text/plain;",
        ]
        block.append("\n".join(lines))

    return "\n".join(block)


# ---------------------------------------------------------------------------
# 数据读写（与 sites 路由一致）
# ---------------------------------------------------------------------------
def _load_sites() -> list:
    if not os.path.exists(SITES_FILE):
        return []
    try:
        with open(SITES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_sites(sites: list):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SITES_FILE, "w", encoding="utf-8") as f:
        json.dump(sites, f, ensure_ascii=False, indent=2)


def _load_ext_opts() -> dict:
    """读取外部站点的增强配置（site_id -> opts）。"""
    if not os.path.exists(SITESOPTS_EXT_FILE):
        return {}
    try:
        with open(SITESOPTS_EXT_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
            return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_ext_opts(data: dict):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SITESOPTS_EXT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _external_site(site_id: str) -> Optional[dict]:
    """按 id 在外部真实站点发现结果中反查站点（叠加已存增强配置）。

    外部站点不在 sites.json，必须从真实配置动态反查，才能被编辑/保存。
    """
    try:
        from app.routers.sites import _find_external_site
        ext = _find_external_site(site_id)
        if ext:
            opts = _load_ext_opts().get(site_id) or {}
            ext["hotlink"] = opts.get("hotlink") or {}
            ext["gzip"] = opts.get("gzip") or {}
            ext["cache_expire"] = int(opts.get("cache_expire") or 0)
        return ext
    except Exception:
        return None


def _find_site(site_id: str) -> Optional[dict]:
    for s in _load_sites():
        if s.get("id") == site_id:
            return s
    # 外部站点不在 sites.json：从真实配置反查（叠加已存增强配置）
    return _external_site(site_id)


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
class ApplyOptsRequest(BaseModel):
    site_id: str = Field(default="", max_length=64)
    hotlink_enabled: bool = False
    hotlink_allowed: list = Field(default_factory=list)  # 允许来源域名（白名单校验）
    hotlink_allow_empty_referer: bool = True              # 是否允许无 Referer（直接访问）
    gzip_enabled: bool = False
    cache_expire: int = 0                                 # 静态资源缓存秒数（0=不设置）


@router.get("/sites")
async def opts_sites():
    """返回所有站点（自建 + 外部真实站点）及其当前增强配置。

    外部站点（1Panel 兼容等）不在 sites.json，读取单独持久化的增强配置。
    """
    from app.routers.sites import merged_sites

    ext_opts = _load_ext_opts()
    sites = []
    for s in merged_sites():
        if s.get("external"):
            o = ext_opts.get(s["id"]) or {}
            hotlink = o.get("hotlink") or {}
            gzip_enabled = bool((o.get("gzip") or {}).get("enabled"))
            cache_expire = int(o.get("cache_expire") or 0)
        else:
            hotlink = s.get("hotlink") or {}
            gzip_enabled = bool(s.get("gzip", {}).get("enabled"))
            cache_expire = int(s.get("cache_expire") or 0)
        sites.append({
            "id": s.get("id"),
            "name": s.get("name"),
            "type": s.get("type"),
            "external": bool(s.get("external")),
            "enabled": s.get("enabled", False),
            "hotlink_enabled": bool(hotlink.get("enabled")),
            "hotlink_allowed": hotlink.get("allowed") or [],
            "hotlink_allow_empty_referer": bool(hotlink.get("allow_empty_referer", True)),
            "gzip_enabled": gzip_enabled,
            "cache_expire": cache_expire,
        })
    return {"sites": sites}


@router.post("/apply")
async def apply_opts(req: ApplyOptsRequest):
    """应用站点增强配置（防盗链 / gzip / 缓存），写入 nginx 配置并 reload。

    自建站点写入 sites.json 并重写 nginx 配置；外部真实站点（1Panel 兼容等）
    单独持久化增强配置，并把配置写回其真实 conf 文件，改动真实生效。
    """
    if not req.site_id:
        raise HTTPException(status_code=400, detail="请选择站点")
    site = _find_site(req.site_id)
    if not site:
        raise HTTPException(status_code=404, detail="站点不存在")
    if site.get("type") not in ("static", "subsite"):
        raise HTTPException(status_code=400, detail="仅静态网址与子网站支持防盗链/缓存配置")

    # 清洗允许来源域名（非法值丢弃），拦截配置注入
    allowed = []
    for d in req.hotlink_allowed or []:
        clean = _sanitize_domain(str(d))
        if clean:
            allowed.append(clean)
    allowed = list(dict.fromkeys(allowed))  # 去重保序
    if len(allowed) > 32:
        raise HTTPException(status_code=400, detail="允许来源域名过多（最多 32 个）")

    # 缓存秒数：仅允许 0 或正数，超上限截断
    cache = int(req.cache_expire or 0)
    if cache < 0:
        raise HTTPException(status_code=400, detail="缓存时长不能为负数")
    if cache > MAX_CACHE_SECONDS:
        cache = MAX_CACHE_SECONDS

    opts = {
        "hotlink": {
            "enabled": bool(req.hotlink_enabled),
            "allowed": allowed,
            "allow_empty_referer": bool(req.hotlink_allow_empty_referer),
        },
        "gzip": {"enabled": bool(req.gzip_enabled)},
        "cache_expire": cache,
    }

    from app.routers.sites import _apply_nginx_config, _reload_nginx, _web_server_type

    if site.get("external"):
        # 外部站点：增强配置单独持久化，并写回真实 conf 文件
        ext = _load_ext_opts()
        ext[req.site_id] = opts
        _save_ext_opts(ext)
        site["hotlink"] = opts["hotlink"]
        site["gzip"] = opts["gzip"]
        site["cache_expire"] = cache
        if site.get("enabled"):
            from app.routers.sites import _apply_external_nginx_config
            _apply_external_nginx_config(site, True)
        return {"ok": True, "site_id": req.site_id, "engine": _web_server_type()}

    site["hotlink"] = opts["hotlink"]
    site["gzip"] = opts["gzip"]
    site["cache_expire"] = cache

    sites = _load_sites()
    for s in sites:
        if s.get("id") == req.site_id:
            s["hotlink"] = site["hotlink"]
            s["gzip"] = site["gzip"]
            s["cache_expire"] = cache
    _save_sites(sites)

    # 重新生成 nginx 配置并 reload（仅当站点已启用）
    ws = _web_server_type()
    if site.get("enabled") and ws == "nginx":
        _apply_nginx_config(req.site_id, site, True)
        _reload_nginx()
    return {"ok": True, "site_id": req.site_id, "engine": ws}


@router.post("/clear")
async def clear_opts(req: dict):
    """清除站点全部增强配置（防盗链 / gzip / 缓存）。"""
    site_id = (req or {}).get("site_id", "")
    if not site_id:
        raise HTTPException(status_code=400, detail="请选择站点")
    site = _find_site(site_id)
    if not site:
        raise HTTPException(status_code=404, detail="站点不存在")

    from app.routers.sites import _apply_nginx_config, _reload_nginx, _web_server_type

    if site.get("external"):
        # 外部站点：清掉单独持久化的增强配置，并把改动写回真实 conf 文件
        ext = _load_ext_opts()
        ext.pop(site_id, None)
        _save_ext_opts(ext)
        site.pop("hotlink", None)
        site.pop("gzip", None)
        site.pop("cache_expire", None)
        if site.get("enabled"):
            from app.routers.sites import _apply_external_nginx_config
            _apply_external_nginx_config(site, True)
        return {"ok": True, "site_id": site_id}

    sites = _load_sites()
    for s in sites:
        if s.get("id") == site_id:
            s.pop("hotlink", None)
            s.pop("gzip", None)
            s.pop("cache_expire", None)
    _save_sites(sites)

    ws = _web_server_type()
    if site.get("enabled") and ws == "nginx":
        _apply_nginx_config(site_id, site, True)
        _reload_nginx()
    return {"ok": True, "site_id": site_id}
