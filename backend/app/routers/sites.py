import json
import os
import platform
import re
import subprocess
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.hostfs import host_path, host_cmd, host_which
from app import webserver

# ---------------------------------------------------------------------------
# 安全校验：防止 web 服务器配置注入与配置文件路径穿越
#
# 站点的 domains / root / reverse_proxy / upstream / ssl 路径等字段最终会被
# 拼进 nginx/apache 配置文件（<id>.conf）。若允许换行、分号、花括号等字符，
# 攻击者可注入任意 nginx 指令（如 alias / root 指向系统目录读取任意文件）。
# 站点 id 直接用作配置文件名，也必须做白名单校验，防止 ../ 或 Windows 盘符
# （c:）形式穿越写入任意路径。
# ---------------------------------------------------------------------------
# 嵌入配置文件的值绝不允许换行/分号/花括号（可截断当前指令注入新指令）
_CONF_VALUE_FORBIDDEN = re.compile(r"[\r\n;{}]")

# 站点 ID（同时是配置文件名）：仅小写字母/数字/中划线/下划线，防路径穿越
_SITE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

# 域名：支持通配符子域（*.example.com）
_DOMAIN_RE = re.compile(
    r"^(\*\.)?([A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?\.)+[A-Za-z]{2,63}$"
)

# 子域名前缀（拼入 server_name）：字母/数字/中划线，允许 * 通配
_SUBDOMAIN_RE = re.compile(r"^[A-Za-z0-9*][A-Za-z0-9*-]*$")

# 反向代理目标：http(s)://host[:port][/path]
_PROXY_RE = re.compile(r"^https?://[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+$")

# TCP/UDP 上游地址：host:port（域名或 IP + 端口）
_UPSTREAM_RE = re.compile(r"^[A-Za-z0-9._-]+:[0-9]{1,5}$")

router = APIRouter()

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
SITES_FILE = os.path.join(DATA_DIR, "sites.json")
# nginx 系目录与 reload：OpenResty 模式由 webserver 模块提供 openresty 路径/命令。
NGINX_AVAILABLE = webserver.available_dir()  # 兼容引用（写盘推荐用 webserver 动态解析）
NGINX_ENABLED = webserver.enabled_dir()
APACHE_AVAILABLE = "/etc/apache2/sites-available"
APACHE_ENABLED = "/etc/apache2/sites-enabled"

# 站点类型
SITE_TYPE_STATIC = "static"      # 静态网址：根目录 + 域名
SITE_TYPE_PROXY = "proxy"        # 反向代理：监听端口 → 后端地址
SITE_TYPE_TCPUDP = "tcpudp"      # TCP/UDP 代理：协议 + 监听端口 → 上游地址
SITE_TYPE_SUBSITE = "subsite"    # 子网站：子域名绑定到根域名，指向根目录

# 合法站点类型集合（create/update 共用校验）
_SITE_TYPES = {
    SITE_TYPE_STATIC,
    SITE_TYPE_PROXY,
    SITE_TYPE_TCPUDP,
    SITE_TYPE_SUBSITE,
}


def _reject_conf_injection(value, field: str) -> None:
    """拒绝会破坏 web 服务器配置结构的字符（换行/分号/花括号）。"""
    if value and _CONF_VALUE_FORBIDDEN.search(str(value)):
        raise HTTPException(
            status_code=400, detail=f"字段 {field} 包含非法字符（换行/分号/花括号）"
        )


def _validate_conf_path(value, field: str) -> None:
    """校验将写入配置文件的路径：Linux 绝对路径、无注入字符、无 .. 穿越。"""
    if not value:
        return
    _reject_conf_injection(value, field)
    v = str(value)
    if not v.startswith("/") or ".." in v:
        raise HTTPException(
            status_code=400, detail=f"字段 {field} 必须是以 / 开头且不含 .. 的绝对路径"
        )


def _validate_site_payload(
    *,
    site_type=None,
    domains=None,
    root=None,
    reverse_proxy=None,
    locations=None,
    protocol=None,
    upstream=None,
    subdomain=None,
    domain=None,
    ssl=None,
) -> None:
    """统一校验将写入 web 服务器配置的站点字段（create / update 共用）。

    任何会拼进 nginx/apache 配置文件的值都在入口处校验，
    从源头阻断「配置注入 → 任意指令/任意文件读取」的攻击链。
    """
    if site_type is not None and site_type not in _SITE_TYPES:
        raise HTTPException(status_code=400, detail="站点类型非法")
    if domains is not None:
        if not isinstance(domains, list) or len(domains) > 32:
            raise HTTPException(status_code=400, detail="domains 必须是最多 32 项的列表")
        for d in domains:
            if not isinstance(d, str) or not _DOMAIN_RE.match(d):
                raise HTTPException(status_code=400, detail=f"域名格式非法: {d!r}")
    if root is not None:
        _validate_conf_path(root, "root")
    if reverse_proxy is not None and reverse_proxy:
        if not _PROXY_RE.match(reverse_proxy):
            raise HTTPException(
                status_code=400, detail="反向代理地址必须形如 http(s)://host[:port][/path]"
            )
        _reject_conf_injection(reverse_proxy, "reverse_proxy")
    if locations is not None:
        if not isinstance(locations, list) or len(locations) > 64:
            raise HTTPException(status_code=400, detail="locations 必须是最多 64 项的列表")
        for loc in locations:
            if not isinstance(loc, dict):
                raise HTTPException(status_code=400, detail="location 项必须是对象")
            path = loc.get("path", "/")
            if (
                not isinstance(path, str)
                or not path.startswith("/")
                or _CONF_VALUE_FORBIDDEN.search(path)
            ):
                raise HTTPException(status_code=400, detail=f"location 路径非法: {path!r}")
            _validate_conf_path(loc.get("root") or "", "location.root")
    if protocol is not None and protocol not in ("tcp", "udp"):
        raise HTTPException(status_code=400, detail="protocol 仅支持 tcp/udp")
    if upstream is not None and upstream:
        if not _UPSTREAM_RE.match(upstream):
            raise HTTPException(status_code=400, detail="上游地址必须形如 host:port")
        _reject_conf_injection(upstream, "upstream")
    if subdomain is not None and subdomain:
        if not _SUBDOMAIN_RE.match(subdomain):
            raise HTTPException(
                status_code=400, detail="子域名前缀仅允许字母、数字、中划线（可含 *）"
            )
    if domain is not None and domain:
        if not _DOMAIN_RE.match(domain):
            raise HTTPException(status_code=400, detail=f"根域名格式非法: {domain!r}")
    if ssl:
        if not isinstance(ssl, dict):
            raise HTTPException(status_code=400, detail="ssl 配置必须是对象")
        for key in ("cert", "key"):
            _validate_conf_path(ssl.get(key) or "", f"ssl.{key}")


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


def _which(cmd: str) -> Optional[str]:
    # 在宿主机环境中查找命令（容器模式经 hostfs 映射）
    return host_which(cmd)


def _web_server_type() -> str:
    # nginx 与 openresty 生成同一套配置格式：任一生效即视为 nginx 系
    if webserver.nginx_like_available():
        return "nginx"
    if _which("apache2") or _which("httpd"):
        return "apache"
    if platform.system() == "Windows" and _which("powershell"):
        # Detect IIS
        try:
            r = subprocess.run(
                [
                    "powershell",
                    "-Command",
                    "Get-WindowsFeature Web-Server -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Installed",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if "True" in r.stdout:
                return "iis"
        except Exception:
            pass
    return "none"


def _site_status_by_port(port: int) -> bool:
    try:
        import psutil

        for conn in psutil.net_connections(kind="inet"):
            if (
                conn.laddr
                and conn.laddr.port == port
                and conn.status == psutil.CONN_LISTEN
            ):
                return True
    except Exception:
        pass
    return False


def _conf_token(value, default: str = "") -> str:
    """纵深防御：清洗将嵌入 web 服务器配置的值（历史存量数据兜底）。

    入口校验（_validate_site_payload）已阻断非法字符；此处对已存储
    的历史数据再做一次清洗，确保配置生成/预览绝无注入可能。
    """
    if value is None:
        return default
    return _CONF_VALUE_FORBIDDEN.sub("", str(value))


def _site_server_name(site: dict) -> str:
    """生成 server_name；非法/缺省时回退通配符 _。

    纵深防御：入口校验（_validate_site_payload）已拒绝注入字符，
    此处对历史存量脏数据再做白名单过滤——仅放行符合域名格式的
    token，任何不符合的值整体丢弃（而非清洗后保留，避免残留
    片段被写入配置）。
    """
    site_type = site.get("type", SITE_TYPE_STATIC)
    if site_type == SITE_TYPE_SUBSITE:
        # 子网站：子域名.根域名，兼容通配子域名
        sub = str(site.get("subdomain") or "").strip()
        domain = str(site.get("domain") or "").strip()
        if sub and domain and _SUBDOMAIN_RE.match(sub) and _DOMAIN_RE.match(domain):
            return f"{sub}.{domain}"
        if domain and _DOMAIN_RE.match(domain):
            return f"*.{domain}"
        return "_"
    # 仅保留符合域名白名单的条目（脏数据整体丢弃）
    names = " ".join(
        d for d in site.get("domains", [])
        if isinstance(d, str) and _DOMAIN_RE.match(d)
    )
    return names or "_"


def _nginx_site_config(site: dict) -> str:
    """根据站点类型生成 nginx http server 配置。"""
    site_type = site.get("type", SITE_TYPE_STATIC)
    server_name = _site_server_name(site)
    # 所有插值经 _conf_token 清洗（纵深防御，见函数注释）
    root_dir = _conf_token(site.get("root") or "/var/www/html")
    port = site.get("port", 80)
    ssl = site.get("ssl", {}) or {}
    enable_ssl = ssl.get("enabled", False)
    proxy = _conf_token(site.get("reverse_proxy", ""))
    locations = site.get("locations", []) or []

    lines = [f"server {{"]
    lines.append(f"    listen {int(port)};")
    if enable_ssl and ssl.get("port", 443):
        lines.append(f"    listen {int(ssl.get('port', 443))} ssl;")
    lines.append(f"    server_name {server_name};")

    if enable_ssl and ssl.get("cert") and ssl.get("key"):
        lines.append(f"    ssl_certificate {_conf_token(ssl['cert'])};")
        lines.append(f"    ssl_certificate_key {_conf_token(ssl['key'])};")

    if site_type == SITE_TYPE_PROXY:
        # 反向代理：整站转发到后端地址
        if proxy:
            lines.append(f"    location / {{")
            lines.append(f"        proxy_pass {proxy};")
            lines.append(f"        proxy_set_header Host $host;")
            lines.append(f"        proxy_set_header X-Real-IP $remote_addr;")
            lines.append(f"        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;")
            lines.append(f"        proxy_set_header X-Forwarded-Proto $scheme;")
            lines.append(f"    }}")
    else:
        # 静态网址 / 子网站：提供根目录静态文件
        lines.append(f"    root {root_dir};")
        lines.append(f"    index index.html index.htm index.php;")
        if proxy:
            lines.append(f"    location / {{")
            lines.append(f"        proxy_pass {proxy};")
            lines.append(f"        proxy_set_header Host $host;")
            lines.append(f"        proxy_set_header X-Real-IP $remote_addr;")
            lines.append(f"    }}")
        else:
            for loc in locations:
                path = _conf_token(loc.get("path") or "/", "/")
                loc_root = _conf_token(loc.get("root") or root_dir, root_dir)
                lines.append(f"    location {path} {{")
                lines.append(f"        root {loc_root};")
                lines.append(f"        try_files $uri $uri/ =404;")
                lines.append(f"    }}")
            if not locations:
                lines.append(f"    location / {{")
                lines.append(f"        try_files $uri $uri/ =404;")
                lines.append(f"    }}")

    lines.append("}")
    return "\n".join(lines)


def _nginx_stream_config(site: dict) -> str:
    """生成 TCP/UDP 代理的 nginx stream 配置块。"""
    protocol = site.get("protocol", "tcp")
    if protocol not in ("tcp", "udp"):
        protocol = "tcp"  # 纵深防御：非法协议回退 tcp
    listen_port = int(site.get("port", 443))
    upstream = _conf_token(site.get("upstream", ""))
    sid = _conf_token(site.get("id", "site"), "site")
    lines = [f"stream {{"]
    lines.append(f"    upstream {sid}_upstream {{")
    lines.append(f"        server {upstream};")
    lines.append(f"    }}")
    lines.append(f"    server {{")
    lines.append(f"        listen {listen_port} {protocol};")
    lines.append(f"        proxy_pass {sid}_upstream;")
    lines.append(f"    }}")
    lines.append("}")
    return "\n".join(lines)


def _ensure_stream_include():
    """确保 nginx.conf 中已注入 stream include 行（幂等）。

    include 行与 nginx.conf 路径均按当前引擎（nginx/openresty）动态解析。
    """
    conf_path = host_path(webserver.conf_path())
    stream_include = webserver.stream_include()
    try:
        with open(conf_path, "r", encoding="utf-8") as f:
            content = f.read()
        if stream_include in content:
            return
        # 在 events {} 块之后插入 stream include，保证位于 http 块之外
        marker = "events {"
        if marker in content:
            idx = content.find(marker)
            end = content.find("}", idx) + 1
            content = content[:end] + "\n" + stream_include + "\n" + content[end:]
        else:
            content = content.rstrip() + "\n\n" + stream_include + "\n"
        with open(conf_path, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception:
        pass


def _apply_nginx_config(site_id: str, site: dict, enabled: bool):
    """按站点类型应用 nginx 配置：static/proxy/subsite 走 http，tcpudp 走 stream。"""
    site_type = site.get("type", SITE_TYPE_STATIC)
    if site_type == SITE_TYPE_TCPUDP:
        # TCP/UDP 代理：stream 配置写到专用目录（按当前引擎解析）
        stream_dir = host_path(webserver.stream_dir())
        conf_name = f"{site_id}.conf"
        stream_conf = os.path.join(stream_dir, conf_name)
        if enabled:
            os.makedirs(stream_dir, exist_ok=True)
            with open(stream_conf, "w", encoding="utf-8") as f:
                f.write(_nginx_stream_config(site))
            _ensure_stream_include()
        else:
            if os.path.exists(stream_conf):
                os.remove(stream_conf)
        return

    conf_name = f"{site_id}.conf"
    # 容器模式下映射为 /host/etc/... ，从而操作宿主机配置（按当前引擎解析）
    avail_dir = host_path(webserver.available_dir())
    enab_dir = host_path(webserver.enabled_dir())
    avail = os.path.join(avail_dir, conf_name)
    enab = os.path.join(enab_dir, conf_name)
    if enabled:
        os.makedirs(avail_dir, exist_ok=True)
        os.makedirs(enab_dir, exist_ok=True)
        with open(avail, "w", encoding="utf-8") as f:
            f.write(_nginx_site_config(site))
        if os.path.exists(enab):
            os.remove(enab)
        os.symlink(avail, enab)
    else:
        if os.path.exists(enab):
            os.remove(enab)
        if os.path.exists(avail):
            os.remove(avail)


def _reload_nginx():
    # 按当前引擎（nginx/openresty）执行 reload
    webserver.reload()


def _apache_site_config(site: dict) -> str:
    domains = " ".join(_conf_token(d) for d in site.get("domains", []) if d)
    root_dir = _conf_token(site.get("root") or "/var/www/html")
    port = int(site.get("port", 80))
    lines = [f"<VirtualHost *:{port}>"]
    lines.append(f"    ServerName {domains}")
    lines.append(f"    DocumentRoot {root_dir}")
    lines.append("</VirtualHost>")
    return "\n".join(lines)


def _apply_apache_config(site_id: str, site: dict, enabled: bool):
    conf_name = f"{site_id}.conf"
    avail_dir = host_path(APACHE_AVAILABLE)
    enab_dir = host_path(APACHE_ENABLED)
    avail = os.path.join(avail_dir, conf_name)
    enab = os.path.join(enab_dir, conf_name)
    if enabled:
        os.makedirs(avail_dir, exist_ok=True)
        os.makedirs(enab_dir, exist_ok=True)
        with open(avail, "w", encoding="utf-8") as f:
            f.write(_apache_site_config(site))
        if os.path.exists(enab):
            os.remove(enab)
        os.symlink(avail, enab)
    else:
        if os.path.exists(enab):
            os.remove(enab)
        if os.path.exists(avail):
            os.remove(avail)
    try:
        host_cmd(["a2ensite", conf_name], capture_output=True, check=False, timeout=10)
        host_cmd(
            ["apache2ctl", "graceful"], capture_output=True, check=False, timeout=10
        )
    except Exception:
        pass


class CreateSite(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    type: str = Field(SITE_TYPE_STATIC, pattern=f"^({SITE_TYPE_STATIC}|{SITE_TYPE_PROXY}|{SITE_TYPE_TCPUDP}|{SITE_TYPE_SUBSITE})$")
    domains: List[str] = Field(default_factory=list)
    root: str = Field(default="", min_length=0)
    port: int = Field(80, ge=1, le=65535)
    ssl: Optional[dict] = Field(default_factory=dict)
    reverse_proxy: Optional[str] = ""
    # TCP/UDP 代理专用
    protocol: Optional[str] = "tcp"  # tcp 或 udp
    upstream: Optional[str] = ""     # 上游地址，如 127.0.0.1:3306
    # 子网站专用
    subdomain: Optional[str] = ""    # 子域名前缀
    domain: Optional[str] = ""       # 根域名


class SiteAction(BaseModel):
    action: str  # start, stop, restart, enable, disable


class UpdateSite(BaseModel):
    type: Optional[str] = None
    domains: Optional[List[str]] = None
    root: Optional[str] = None
    port: Optional[int] = None
    ssl: Optional[dict] = None
    reverse_proxy: Optional[str] = None
    locations: Optional[List[dict]] = None
    protocol: Optional[str] = None
    upstream: Optional[str] = None
    subdomain: Optional[str] = None
    domain: Optional[str] = None


@router.get("/list")
async def list_sites():
    sites = _load_sites()
    ws = _web_server_type()
    for s in sites:
        s["web_server"] = ws
        s["online"] = _site_status_by_port(s.get("port", 80))
    return {"sites": sites, "web_server": ws}


@router.post("/create")
async def create_site(req: CreateSite):
    sites = _load_sites()
    if any(s["name"] == req.name for s in sites):
        raise HTTPException(status_code=400, detail="Site name already exists")
    # 安全校验：所有将写入 web 服务器配置的字段必须在入口处校验
    _validate_site_payload(
        site_type=req.type,
        domains=req.domains,
        root=req.root,
        reverse_proxy=req.reverse_proxy,
        protocol=req.protocol,
        upstream=req.upstream,
        subdomain=req.subdomain,
        domain=req.domain,
        ssl=req.ssl,
    )
    # 站点 ID 直接用作配置文件名（<id>.conf）：将名称规范化为白名单字符，
    # 阻止路径分隔符 / Windows 盘符（c:）等造成任意路径写入
    site_id = re.sub(r"[^a-z0-9_-]+", "-", req.name.lower()).strip("-")
    if not _SITE_ID_RE.match(site_id):
        raise HTTPException(
            status_code=400, detail="站点名称仅允许字母、数字与空格（自动转中划线）"
        )
    if any(s["id"] == site_id for s in sites):
        raise HTTPException(status_code=400, detail="站点 ID 已存在，请更换名称")
    # 仅静态网址/子网站需要创建根目录
    if req.type in (SITE_TYPE_STATIC, SITE_TYPE_SUBSITE) and req.root:
        os.makedirs(host_path(req.root), exist_ok=True)
    site = {
        "id": site_id,
        "name": req.name,
        "type": req.type,
        "domains": req.domains,
        "root": req.root,
        "port": req.port,
        "ssl": req.ssl or {},
        "reverse_proxy": req.reverse_proxy or "",
        "locations": [],
        "protocol": req.protocol or "tcp",
        "upstream": req.upstream or "",
        "subdomain": req.subdomain or "",
        "domain": req.domain or "",
        "enabled": False,
        "created_at": datetime.now().isoformat(),
    }
    sites.append(site)
    _save_sites(sites)
    return site


@router.post("/{site_id}/action")
async def site_action(site_id: str, req: SiteAction):
    sites = _load_sites()
    site = next((s for s in sites if s["id"] == site_id), None)
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    ws = _web_server_type()
    if req.action == "enable":
        site["enabled"] = True
        if ws == "nginx":
            _apply_nginx_config(site_id, site, True)
            _reload_nginx()
        elif ws == "apache":
            _apply_apache_config(site_id, site, True)
    elif req.action == "disable":
        site["enabled"] = False
        if ws == "nginx":
            _apply_nginx_config(site_id, site, False)
            _reload_nginx()
        elif ws == "apache":
            _apply_apache_config(site_id, site, False)
    elif req.action == "start":
        site["enabled"] = True
        if ws == "nginx":
            _apply_nginx_config(site_id, site, True)
            _reload_nginx()
    elif req.action == "stop":
        site["enabled"] = False
        if ws == "nginx":
            _apply_nginx_config(site_id, site, False)
            _reload_nginx()
    elif req.action == "restart":
        if ws == "nginx":
            _reload_nginx()
    _save_sites(sites)
    return {"ok": True, "enabled": site["enabled"]}


@router.get("/{site_id}/config")
async def get_site_config(site_id: str):
    sites = _load_sites()
    site = next((s for s in sites if s["id"] == site_id), None)
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    ws = _web_server_type()
    if ws == "nginx":
        config = _nginx_site_config(site)
    elif ws == "apache":
        config = _apache_site_config(site)
    else:
        config = "# No web server detected on this system."
    return {"site": site, "config": config}


@router.post("/{site_id}/update")
async def update_site(site_id: str, req: UpdateSite):
    sites = _load_sites()
    site = next((s for s in sites if s["id"] == site_id), None)
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    # 安全校验：与 create 一致，更新值同样禁止配置注入字符
    _validate_site_payload(
        site_type=req.type,
        domains=req.domains,
        root=req.root,
        reverse_proxy=req.reverse_proxy,
        locations=req.locations,
        protocol=req.protocol,
        upstream=req.upstream,
        subdomain=req.subdomain,
        domain=req.domain,
        ssl=req.ssl,
    )
    if req.type is not None:
        site["type"] = req.type
    if req.domains is not None:
        site["domains"] = req.domains
    if req.root is not None:
        if req.root:
            os.makedirs(host_path(req.root), exist_ok=True)
        site["root"] = req.root
    if req.port is not None:
        site["port"] = req.port
    if req.ssl is not None:
        site["ssl"] = req.ssl
    if req.reverse_proxy is not None:
        site["reverse_proxy"] = req.reverse_proxy
    if req.locations is not None:
        site["locations"] = req.locations
    if req.protocol is not None:
        site["protocol"] = req.protocol
    if req.upstream is not None:
        site["upstream"] = req.upstream
    if req.subdomain is not None:
        site["subdomain"] = req.subdomain
    if req.domain is not None:
        site["domain"] = req.domain
    ws = _web_server_type()
    if site.get("enabled"):
        if ws == "nginx":
            _apply_nginx_config(site_id, site, True)
            _reload_nginx()
        elif ws == "apache":
            _apply_apache_config(site_id, site, True)
    _save_sites(sites)
    return site


@router.post("/{site_id}/delete")
async def delete_site(site_id: str):
    sites = _load_sites()
    site = next((s for s in sites if s["id"] == site_id), None)
    if not site:
        raise HTTPException(status_code=404, detail="Site not found")
    ws = _web_server_type()
    if ws == "nginx":
        _apply_nginx_config(site_id, site, False)
        _reload_nginx()
    elif ws == "apache":
        _apply_apache_config(site_id, site, False)
    sites = [s for s in sites if s["id"] != site_id]
    _save_sites(sites)
    return {"ok": True}
