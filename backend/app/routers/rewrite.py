# -*- coding: utf-8 -*-
"""
rewrite.py - 伪静态规则库

为网站提供常用框架（WordPress / ThinkPHP / Laravel 等）的伪静态规则，
一键应用到站点（写入 nginx 配置并 reload），并支持清除。

设计要点：
  1. 规则模板由后端硬编码白名单维护（绝不允许用户直接提交任意文本），
     从源头阻断「伪静态内容 → nginx 配置注入」的攻击链。
  2. 应用/清除仅更新站点数据中的 rewrite 字段 + 重新生成 nginx 配置，
     复用 sites 路由的写入与 reload 逻辑，保证一致性。
  3. 模板同时提供 nginx 与 apache 两种片段，按站点当前 web 引擎生效。
"""
import json
import os
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.hostfs import host_path

router = APIRouter()

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
SITES_FILE = os.path.join(DATA_DIR, "sites.json")

# ---------------------------------------------------------------------------
# 伪静态规则模板库（白名单，后端硬编码）
# nginx：注入到 server 块内的 location 片段；apache：.htaccess RewriteRule 片段
# ---------------------------------------------------------------------------
_REWRITE_TEMPLATES = [
    {
        "id": "wordpress",
        "name": "WordPress",
        "desc": "WordPress 博客/企业站常用伪静态",
        "nginx": (
            "location / {\n"
            "    try_files $uri $uri/ /index.php?$args;\n"
            "}"
        ),
        "apache": (
            "RewriteEngine On\n"
            "RewriteBase /\n"
            "RewriteRule ^index\\.php$ - [L]\n"
            "RewriteCond %{REQUEST_FILENAME} !-f\n"
            "RewriteCond %{REQUEST_FILENAME} !-d\n"
            "RewriteRule . /index.php [L]"
        ),
    },
    {
        "id": "thinkphp",
        "name": "ThinkPHP",
        "desc": "ThinkPHP 5/6/8 框架路由伪静态",
        "nginx": (
            "location / {\n"
            "    if (!-e $request_filename) {\n"
            "        rewrite ^(.*)$ /index.php?s=$1 last;\n"
            "    }\n"
            "}"
        ),
        "apache": (
            "RewriteEngine On\n"
            "RewriteCond %{REQUEST_FILENAME} !-f\n"
            "RewriteCond %{REQUEST_FILENAME} !-d\n"
            "RewriteRule ^(.*)$ index.php?s=/$1 [QSA,PT,L]"
        ),
    },
    {
        "id": "laravel",
        "name": "Laravel",
        "desc": "Laravel 框架路由伪静态（public 入口）",
        "nginx": (
            "location / {\n"
            "    try_files $uri $uri/ /index.php?$query_string;\n"
            "}"
        ),
        "apache": (
            "RewriteEngine On\n"
            "RewriteCond %{REQUEST_FILENAME} !-f\n"
            "RewriteCond %{REQUEST_FILENAME} !-d\n"
            "RewriteRule ^ index.php [L]"
        ),
    },
    {
        "id": "typecho",
        "name": "Typecho",
        "desc": "Typecho 轻量博客伪静态",
        "nginx": (
            "location / {\n"
            "    if (!-e $request_filename) {\n"
            "        rewrite ^(.*)$ /index.php$1 last;\n"
            "    }\n"
            "}"
        ),
        "apache": (
            "RewriteEngine On\n"
            "RewriteBase /\n"
            "RewriteCond %{REQUEST_FILENAME} !-f\n"
            "RewriteCond %{REQUEST_FILENAME} !-d\n"
            "RewriteRule ^(.*)$ index.php/$1 [L]"
        ),
    },
    {
        "id": "discuz",
        "name": "Discuz!",
        "desc": "Discuz 论坛伪静态（X3 系列）",
        "nginx": (
            "location / {\n"
            "    rewrite ^([^\\.]*)/topic-(.+)\\.html$ $1/portal.php?mod=topic&topic=$2 last;\n"
            "    rewrite ^([^\\.]*)/article-([0-9]+)-([0-9]+)\\.html$ $1/portal.php?mod=article&aid=$2&page=$3 last;\n"
            "    rewrite ^([^\\.]*)/forum-(\\w+)-([0-9]+)\\.html$ $1/forum.php?mod=forum&fid=$2&page=$3 last;\n"
            "    rewrite ^([^\\.]*)/thread-([0-9]+)-([0-9]+)-([0-9]+)\\.html$ $1/forum.php?mod=viewthread&tid=$2&extra=page%3D$4&page=$3 last;\n"
            "    if (!-e $request_filename) {\n"
            "        rewrite ^([^\\.]*)/([^/]+)\\.html?$ $1/forum.php?mod=viewthread&tid=$2 last;\n"
            "    }\n"
            "}"
        ),
        "apache": (
            "RewriteEngine On\n"
            "RewriteCond %{QUERY_STRING} ^(.*)$\n"
            "RewriteRule ^(.*)/topic-(.+)\\.html$ $1/portal.php?mod=topic&topic=$2&%1\n"
            "RewriteRule ^(.*)/article-([0-9]+)-([0-9]+)\\.html$ $1/portal.php?mod=article&aid=$2&page=$3&%1\n"
            "RewriteRule ^(.*)/forum-(\\w+)-([0-9]+)\\.html$ $1/forum.php?mod=forum&fid=$2&page=$3&%1\n"
            "RewriteRule ^(.*)/thread-([0-9]+)-([0-9]+)-([0-9]+)\\.html$ $1/forum.php?mod=viewthread&tid=$2&extra=page%3D$4&page=$3&%1\n"
            "RewriteRule ^(.*)/([^/]+)\\.html?$ $1/forum.php?mod=viewthread&tid=$2&%1"
        ),
    },
    {
        "id": "dedecms",
        "name": "DedeCMS",
        "desc": "织梦 CMS 栏目/文章伪静态",
        "nginx": (
            "location / {\n"
            "    rewrite ^/list-([0-9]+)\\.html$ /plus/list.php?tid=$1 last;\n"
            "    rewrite ^/view-([0-9]+)-([0-9]+)\\.html$ /plus/view.php?aid=$1&pageno=$2 last;\n"
            "    rewrite ^/show-([0-9]+)\\.html$ /plus/view.php?aid=$1 last;\n"
            "}"
        ),
        "apache": (
            "RewriteEngine On\n"
            "RewriteRule ^list-([0-9]+)\\.html$ /plus/list.php?tid=$1\n"
            "RewriteRule ^view-([0-9]+)-([0-9]+)\\.html$ /plus/view.php?aid=$1&pageno=$2\n"
            "RewriteRule ^show-([0-9]+)\\.html$ /plus/view.php?aid=$1"
        ),
    },
    {
        "id": "empirecms",
        "name": "帝国CMS",
        "desc": "帝国 CMS 信息门户伪静态",
        "nginx": (
            "location / {\n"
            "    rewrite ^/e/show/?([0-9]+)([0-9]+)?$ /e/action/ShowInfo.php?classid=$1&id=$2 last;\n"
            "    rewrite ^/e/list/?([0-9]+)([0-9]+)?$ /e/action/ListInfo.php?classid=$1&page=$2 last;\n"
            "}"
        ),
        "apache": (
            "RewriteEngine On\n"
            "RewriteRule ^e/show/?([0-9]+)([0-9]+)?$ /e/action/ShowInfo.php?classid=$1&id=$2\n"
            "RewriteRule ^e/list/?([0-9]+)([0-9]+)?$ /e/action/ListInfo.php?classid=$1&page=$2"
        ),
    },
    {
        "id": "shopex",
        "name": "ShopEx",
        "desc": "ShopEx 电商系统商品页伪静态",
        "nginx": (
            "location / {\n"
            "    rewrite ^/goods-([0-9]+)\\.html$ /index.php?gOo=goods_details.dwt&goodsid=$1 last;\n"
            "    rewrite ^/category-([0-9]+)-b([0-9]+)\\.html$ /index.php?gOo=goods_list.dwt&category=$1&brand=$2 last;\n"
            "}"
        ),
        "apache": (
            "RewriteEngine On\n"
            "RewriteRule ^goods-([0-9]+)\\.html$ /index.php?gOo=goods_details.dwt&goodsid=$1\n"
            "RewriteRule ^category-([0-9]+)-b([0-9]+)\\.html$ /index.php?gOo=goods_list.dwt&category=$1&brand=$2"
        ),
    },
]


def list_templates() -> list:
    """返回规则模板库（含片段预览，供前端选择与展示）。"""
    return [
        {
            "id": t["id"],
            "name": t["name"],
            "desc": t["desc"],
            "nginx": t["nginx"],
            "apache": t["apache"],
        }
        for t in _REWRITE_TEMPLATES
    ]


def get_template(template_id: str) -> Optional[dict]:
    """按 id 查找模板；不存在返回 None。"""
    for t in _REWRITE_TEMPLATES:
        if t["id"] == template_id:
            return t
    return None


def get_nginx_fragment(template_id: str) -> str:
    """返回指定模板的 nginx 片段；模板不存在返回空串。"""
    t = get_template(template_id)
    return t["nginx"] if t else ""


def _load_sites() -> list:
    if not os.path.exists(SITES_FILE):
        return []
    try:
        with open(SITES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _save_sites(sites: list):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SITES_FILE, "w", encoding="utf-8") as f:
        json.dump(sites, f, ensure_ascii=False, indent=2)


class ApplyRewriteRequest(BaseModel):
    template_id: str  # 伪静态模板 id（仅允许白名单模板）
    site_id: str = ""  # 目标站点 id


def _find_site(site_id: str) -> Optional[dict]:
    for s in _load_sites():
        if s.get("id") == site_id:
            return s
    return None


@router.get("/templates")
async def templates():
    """返回伪静态规则模板库。"""
    return {"templates": list_templates()}


@router.get("/sites")
async def rewrite_sites():
    """返回所有站点及其当前伪静态状态（便于前端选择与展示）。"""
    sites = []
    for s in _load_sites():
        sites.append({
            "id": s.get("id"),
            "name": s.get("name"),
            "type": s.get("type"),
            "enabled": s.get("enabled", False),
            "rewrite": s.get("rewrite", ""),
        })
    return {"sites": sites}


@router.post("/apply")
async def apply_rewrite(req: ApplyRewriteRequest):
    """应用伪静态规则到指定站点（白名单模板，写入 nginx 配置并 reload）。"""
    tpl = get_template(req.template_id)
    if not tpl:
        raise HTTPException(status_code=400, detail="伪静态模板不存在")
    if not req.site_id:
        raise HTTPException(status_code=400, detail="请选择站点")
    site = _find_site(req.site_id)
    if not site:
        raise HTTPException(status_code=404, detail="站点不存在")

    # 仅静态网址/子网站支持伪静态（proxy/tcpudp 无静态文件语义）
    if site.get("type") not in ("static", "subsite"):
        raise HTTPException(
            status_code=400, detail="仅静态网址与子网站支持伪静态规则"
        )

    site["rewrite"] = req.template_id
    sites = _load_sites()
    for s in sites:
        if s.get("id") == site["id"]:
            s["rewrite"] = req.template_id
    _save_sites(sites)

    # 重新生成 nginx 配置并 reload（仅当站点已启用）
    from app.routers.sites import _apply_nginx_config, _reload_nginx, _web_server_type
    ws = _web_server_type()
    if site.get("enabled"):
        if ws == "nginx":
            _apply_nginx_config(req.site_id, site, True)
            _reload_nginx()
        elif ws == "apache":
            # apache 伪静态通过 .htaccess 表达，配置无变化，仅提示
            pass
    return {"ok": True, "site_id": req.site_id, "template_id": req.template_id, "engine": ws}


@router.post("/clear")
async def clear_rewrite(req: dict):
    """清除站点伪静态规则。"""
    site_id = (req or {}).get("site_id", "")
    if not site_id:
        raise HTTPException(status_code=400, detail="请选择站点")
    site = _find_site(site_id)
    if not site:
        raise HTTPException(status_code=404, detail="站点不存在")
    sites = _load_sites()
    for s in sites:
        if s.get("id") == site_id:
            s["rewrite"] = ""
    _save_sites(sites)

    from app.routers.sites import _apply_nginx_config, _reload_nginx, _web_server_type
    ws = _web_server_type()
    if site.get("enabled") and ws == "nginx":
        _apply_nginx_config(site_id, site, True)
        _reload_nginx()
    return {"ok": True, "site_id": site_id}
