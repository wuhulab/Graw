# -*- coding: utf-8 -*-
"""
netmap.py（路由） - 内网映射：把「当前节点」的内网 HTTP 服务反代到面板侧

背景：
  节点（本机或子节点）上常跑着不对外暴露的内网服务（如 http://localhost:3000
  的 Gitea / n8n / 开发服务）。浏览器无法直达节点内网地址，本模块提供
  「映射条目 + HTTP 反向代理」：
    - 条目存于「节点视角」的 data/netmap.json（子节点经 Agent 隧道自动代理
      CRUD，数据落在各子节点自己的 data/ 下，天然按节点隔离）；
    - 代理端点 /api/netmap/proxy/{id}/<path> 把请求转发到该节点本机的目标
      服务，浏览器在窗口 iframe / 新标签里即可直接访问。

鉴权：
  - CRUD 与代理统一要求「登录 + netmap 模块权限」（受限管理员按白名单收窄）；
  - 代理端点额外支持 ?token=<jwt> 查询参数鉴权：iframe 无法携带自定义
    Authorization 头（与终端/监控 WS 的 ?token= 惯例一致）。转发目标前会把
    该鉴权参数从 query 中剥离，避免污染目标服务。

安全：
  - 目标 URL 仅允许 http/https 且 host 非空；条目由具备 netmap 权限的管理员
    显式配置（代理目标是白名单式的显式条目，而非任意地址转发）；
  - 转发请求剔除 hop-by-hop 头；响应剥离目标的 X-Frame-Options /
    CSP frame-ancestors（面板窗口需要 iframe 嵌套），并把面板自身的
    frame 头放宽为 SAMEORIGIN（先于 security_headers 中间件设置，
    setdefault 不会覆盖）；
  - 本路由登记于 remote_cap.LOCAL_PREFIX：裸 SSH 远端节点（无 Agent）下
    禁用，避免「以为是远端服务、实际代理的是面板本机」的语义错位。
"""

import asyncio
import json
import logging
import os
import re
import threading
import time
import uuid
from typing import Optional

import requests as http_client
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel, Field
from urllib.parse import urlsplit

from app import auth as auth_mod

logger = logging.getLogger("graw.netmap")


def _safe_log(value) -> str:
    """日志字段清洗：去掉换行，避免用户可控内容伪造日志行（CWE-117）。"""
    return str(value).replace("\n", " ").replace("\r", "")


router = APIRouter()

# 代理路由单独一组：不能叠加 main.py 挂在 CRUD 组上的 PERM("netmap")
# （那是 Bearer-only 依赖，会先于本组的 ?token= 弹性鉴权拦截掉 iframe 请求）
proxy_router = APIRouter()

DATA_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "data"))
NM_FILE = os.path.join(DATA_DIR, "netmap.json")

_nm_lock = threading.Lock()

# 条目 ID 白名单（与 portforward 一致的防穿越校验）
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

# 代理支持转发的方法（覆盖常见 REST / 表单场景；WS 升级不在支持范围）
_PROXY_METHODS = ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS")

# 逐跳头（RFC 7230）：不该在代理两侧透传
_HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length",
}

# 响应侧需要剥离的嵌入限制头：目标服务的防嵌套策略对「面板同源 iframe」无意义
_STRIP_RESP_HEADERS = ("content-security-policy", "x-frame-options", "frame-ancestors")


# ---------------------------------------------------------------------------
# 鉴权（Bearer 头 或 ?token= 查询参数，与 WS 惯例对齐）
# ---------------------------------------------------------------------------
def _resolve_user(request: Request, token: str) -> Optional[dict]:
    """与 auth.get_current_user 等价的弹性鉴权：优先 Bearer 头，缺省回退 ?token=。

    校验链：签名/有效期 → 用户存在 → token_version → 会话未吊销 → 非默认密码。
    任何一环失败返回 None（由调用方统一抛 401/403）。
    """
    if not token:
        # 从 Authorization 头提取（iframe 场景拿不到头，仅普通 fetch 场景走到这）
        raw = (request.headers.get("authorization") or "").strip()
        if raw.lower().startswith("bearer "):
            token = raw[7:].strip()
    if not token:
        return None
    payload = auth_mod.decode_token(token)
    if payload is None:
        return None
    user = auth_mod._get_user(payload.get("sub", ""))
    if user is None:
        return None
    if not auth_mod.verify_token_version(payload):
        return None
    if not auth_mod.session_active(payload.get("sid", "")):
        return None
    if auth_mod.is_default_password(user.get("password", "")):
        return None
    return user


async def require_perm_netmap_flex(
    request: Request, token: str = Query(default="", alias="__graw_token")
) -> dict:
    """netmap 弹性鉴权依赖：Bearer 或 ?__graw_token= 均可 + netmap 模块权限判定。

    alias 必须与前端 iframe 地址里的参数名（__graw_token）一致——
    普通字符串 Query 名会被 Authorization 头分支覆盖判断，参数名对不上
    会静默拿空 token 导致 401。自带 __perm_modules__ 元数据：
    main._proxy_perm_map 会从路由依赖树读取该属性，受限管理员经 Agent
    隧道代理时在主面板本地即可被等价拦截。
    """
    user = _resolve_user(request, token)
    if user is None:
        raise HTTPException(status_code=401, detail="未登录或令牌无效")
    if not auth_mod.has_perm(user, "netmap"):
        raise HTTPException(status_code=403, detail="需要 netmap 模块权限")
    return user


# 携带模块元组供 Agent 代理前置鉴权识别（须在挂载 Depends 前赋值）
require_perm_netmap_flex.__perm_modules__ = ("netmap",)
# 挂到代理子路由的依赖
_PROXY_DEPS = [Depends(require_perm_netmap_flex)]


# ---------------------------------------------------------------------------
# 存储（原子写）
# ---------------------------------------------------------------------------
def _load() -> dict:
    """读取映射条目存储；文件缺失/损坏时回退空表。"""
    if not os.path.exists(NM_FILE):
        return {"version": 1, "items": []}
    try:
        with open(NM_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("items"), list):
            data.setdefault("version", 1)
            return data
    except (OSError, json.JSONDecodeError):
        logger.warning("netmap.json 读取失败，按空表处理", exc_info=True)
    return {"version": 1, "items": []}


def _save(data: dict) -> None:
    """原子写存储：临时文件 + os.replace，避免写一半断电损坏。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = NM_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, NM_FILE)


def _validate_url(url: str) -> str:
    """校验并规范化目标服务地址：仅允许 http/https 且 host 非空。"""
    url = (url or "").strip()
    if not url:
        raise ValueError("服务地址不能为空")
    if not url.startswith(("http://", "https://")):
        raise ValueError("服务地址必须以 http:// 或 https:// 开头")
    try:
        parts = urlsplit(url)
    except ValueError:
        raise ValueError("服务地址格式非法")
    if not parts.netloc:
        raise ValueError("服务地址缺少主机名")
    if len(url) > 512:
        raise ValueError("服务地址过长")
    # 去掉末尾多余的 /（拼接子路径时统一补）
    return url.rstrip("/")


# ---------------------------------------------------------------------------
# Pydantic 请求模型
# ---------------------------------------------------------------------------
class NetmapReq(BaseModel):
    """创建/更新映射条目：name 展示名；url 为节点内网服务地址。"""

    name: str = Field(default="", max_length=64)
    url: str = Field(..., min_length=1, max_length=512)


# ---------------------------------------------------------------------------
# CRUD 接口（PERM("netmap") 由 main.py 在 include_router 时统一挂载）
# ---------------------------------------------------------------------------
@router.get("")
def list_items():
    """已配置的内网映射条目列表（当前节点视角）。"""
    return {"items": _load().get("items", [])}


@router.post("")
def create(req: NetmapReq):
    """创建映射条目。"""
    try:
        url = _validate_url(req.url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    item = {
        "id": uuid.uuid4().hex[:10],
        "name": (req.name or "").strip()[:64] or url,
        "url": url,
        "created_at": int(time.time()),
    }
    with _nm_lock:
        data = _load()
        data.setdefault("items", []).append(item)
        _save(data)
    logger.info("创建内网映射 %s -> %s", _safe_log(item["name"]), _safe_log(url))
    return {"ok": True, "item": item}


@router.put("/{iid}")
def update(iid: str, req: NetmapReq):
    """更新映射条目（名称与目标地址均可改）。"""
    if not _ID_RE.match(iid or ""):
        raise HTTPException(status_code=400, detail="非法的条目 ID")
    try:
        url = _validate_url(req.url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    with _nm_lock:
        data = _load()
        item = next((i for i in data.get("items", []) if i.get("id") == iid), None)
        if item is None:
            raise HTTPException(status_code=404, detail="映射条目不存在")
        item["name"] = (req.name or "").strip()[:64] or url
        item["url"] = url
        _save(data)
    logger.info("更新内网映射 %s -> %s", _safe_log(iid), _safe_log(url))
    return {"ok": True, "item": item}


@router.delete("/{iid}")
def remove(iid: str):
    """删除映射条目。"""
    if not _ID_RE.match(iid or ""):
        raise HTTPException(status_code=400, detail="非法的条目 ID")
    with _nm_lock:
        data = _load()
        items = [i for i in data.get("items", []) if i.get("id") != iid]
        if len(items) == len(data.get("items", [])):
            raise HTTPException(status_code=404, detail="映射条目不存在")
        data["items"] = items
        _save(data)
    logger.info("删除内网映射 %s", _safe_log(iid))
    return {"ok": True}


# ---------------------------------------------------------------------------
# HTTP 反向代理
# ---------------------------------------------------------------------------
def _strip_auth_from_query(query: str) -> str:
    """从 query 中剥离 __graw_token 鉴权参数（不透传给目标服务）。"""
    if not query:
        return ""
    kept = [kv for kv in query.split("&") if not kv.startswith("__graw_token=")]
    return "&".join(kept)


def _build_target_url(base: str, sub_path: str, query: str) -> str:
    """拼出目标 URL：base(已去尾斜杠) + /子路径 + ?query。"""
    path = "/" + (sub_path or "").lstrip("/")
    qs = ("?" + query) if query else ""
    return base + path + qs


def _rewrite_location(location: str, base: str, proxy_prefix: str) -> str:
    """重定向 Location 头改写：目标 origin 的绝对地址换成本地代理相对路径。

    浏览器 iframe 内无法直达节点内网地址，绝对 Location 必须换回代理前缀；
    相对 Location（如 /login）本就相对代理路径解析，原样放行。
    """
    if not location:
        return location
    try:
        loc = urlsplit(location)
        tgt = urlsplit(base)
        # 绝对地址且 host 与目标一致 → 重写为代理相对路径（保留编码的路径与查询）
        if loc.scheme in ("http", "https") and loc.netloc == tgt.netloc:
            path = loc.path or "/"
            # base 可能带子目录前缀（如 http://host/app）：剥掉后再拼
            base_path = (tgt.path or "").rstrip("/")
            if base_path and path.startswith(base_path):
                path = path[len(base_path):] or "/"
            return proxy_prefix + path + (("?" + loc.query) if loc.query else "")
    except ValueError as e:
        logger.debug("重写代理重定向地址失败: %s", e)
    return location


def _unreachable_page(status: int) -> HTMLResponse:
    """目标服务不可达时的友好提示页（替代裸 JSON 报错）。

    iframe 直达场景下反代失败若返回 JSON，用户只会看到一段代码；
    这里返回一个可读的 HTML 提示页（主文案「无映射端口」），并带
    SAMEORIGIN / frame-ancestors 'self'，确保能被窗口 iframe 正常渲染
    （全局 security_headers 的 setdefault 不会覆盖已存在的头）。
    """
    html = (
        '<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
        "<title>无映射端口</title>"
        "<style>"
        "body{margin:0;min-height:100vh;display:flex;align-items:center;"
        'justify-content:center;background:#f5f7fa;font-family:-apple-system,'
        'BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;}'
        ".card{text-align:center;padding:40px 48px;border-radius:12px;"
        "background:#fff;box-shadow:0 2px 12px rgba(0,0,0,.08);max-width:420px;}"
        ".code{font-size:44px;line-height:1;margin-bottom:16px;}"
        "h1{font-size:18px;color:#334;margin:0 0 10px;}"
        "p{font-size:13px;color:#889;margin:0;line-height:1.7;}"
        "</style></head><body><div class=\"card\">"
        '<div class="code">&#128279;</div>'
        "<h1>无映射端口</h1>"
        "<p>该节点上的目标服务没有响应（端口未开放或服务未启动）。<br>"
        "请确认服务已运行、端口正确，然后刷新重试。</p>"
        "</div></body></html>"
    )
    return HTMLResponse(
        content=html,
        status_code=status,
        headers={
            "X-Frame-Options": "SAMEORIGIN",
            "Content-Security-Policy": "frame-ancestors 'self'",
        },
    )


async def _proxy_once(request: Request, item: dict, sub_path: str) -> StreamingResponse:
    """执行一次反代：读请求体 → requests 转发（线程池） → 流式回传。"""
    base = item["url"]
    query = _strip_auth_from_query(request.url.query)
    target = _build_target_url(base, sub_path, query)

    # 请求头：剔除逐跳头与鉴权改写相关的头（Host/Content-Length 由 requests 重算）
    fwd_headers = {}
    for k, v in request.headers.items():
        if k.lower() in _HOP_BY_HOP or k.lower() == "authorization":
            continue
        fwd_headers[k] = v

    body = await request.body()

    # requests 是同步库：放到线程池执行，避免阻塞事件循环。
    # proxies 显式置空：内网映射必须直连节点内网地址，绝不能被系统代理
    # 环境变量（HTTP_PROXY 等）劫持——否则配了代理的机器上反代会指向代理
    # 服务器，回环/内网地址全部失败。
    def _do_request():
        return http_client.request(
            request.method,
            target,
            headers=fwd_headers,
            data=body if request.method not in ("GET", "HEAD") else None,
            stream=True,
            allow_redirects=False,   # 重定向交给浏览器按代理路径重新发起
            timeout=(10, 300),       # 连接 10s / 读 300s（内网大响应友好）
            proxies={"http": None, "https": None},
        )

    try:
        resp = await asyncio.to_thread(_do_request)
    except http_client.exceptions.Timeout:
        return _unreachable_page(504)
    except http_client.exceptions.RequestException as e:
        # 不回传异常详情（可能含内网拓扑），仅记日志
        logger.warning("内网映射 %s 请求失败: %s", item.get("id"), type(e).__name__)
        return _unreachable_page(502)

    # 构造响应头：剥离逐跳头与嵌入限制头
    resp_headers = {}
    for k, v in resp.headers.items():
        lk = k.lower()
        if lk in _HOP_BY_HOP or lk in _STRIP_RESP_HEADERS or lk == "content-encoding":
            continue  # content-encoding 剔除：requests 默认自动解压，长度已变
        resp_headers[k] = v
    # iframe 豁免：预置面板侧 frame 头（security_headers 的 setdefault 不会覆盖）
    resp_headers["X-Frame-Options"] = "SAMEORIGIN"
    resp_headers["Content-Security-Policy"] = "frame-ancestors 'self'"
    # 重定向改写：目标绝对 Location → 本地代理相对路径
    if "location" in {k.lower() for k in resp_headers}:
        proxy_prefix = f"/api/netmap/proxy/{item['id']}"
        for k in list(resp_headers.keys()):
            if k.lower() == "location":
                resp_headers[k] = _rewrite_location(resp_headers[k], base, proxy_prefix)

    return StreamingResponse(
        resp.iter_content(chunk_size=65536),
        status_code=resp.status_code,
        headers=resp_headers,
        media_type=resp.headers.get("content-type"),
    )


@proxy_router.api_route(
    "/proxy/{item_id}",
    methods=list(_PROXY_METHODS),
    dependencies=_PROXY_DEPS,
)
@proxy_router.api_route(
    "/proxy/{item_id}/{sub_path:path}",
    methods=list(_PROXY_METHODS),
    dependencies=_PROXY_DEPS,
)
async def proxy(item_id: str, request: Request, sub_path: str = ""):
    """把请求反代到当前节点上配置的内网服务（iframe / 新标签直达入口）。"""
    if not _ID_RE.match(item_id or ""):
        raise HTTPException(status_code=400, detail="非法的条目 ID")
    item = next((i for i in _load().get("items", []) if i.get("id") == item_id), None)
    if item is None:
        raise HTTPException(status_code=404, detail="映射条目不存在")
    # WebSocket 升级请求不支持（本代理仅 HTTP），明确拒绝而非挂起
    if (request.headers.get("upgrade") or "").lower() == "websocket":
        raise HTTPException(status_code=400, detail="内网映射暂不支持 WebSocket 代理")
    return await _proxy_once(request, item, sub_path)
