import json
import os
import platform
import subprocess
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.hostfs import host_path, host_cmd, host_which

router = APIRouter()

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "data")
SITES_FILE = os.path.join(DATA_DIR, "sites.json")
# 以下均为宿主机视角路径，实际访问时经 host_path 映射（容器模式下为 /host 前缀）
NGINX_AVAILABLE = "/etc/nginx/sites-available"
NGINX_ENABLED = "/etc/nginx/sites-enabled"
NGINX_CONF = "/etc/nginx/nginx.conf"
# TCP/UDP 代理使用 nginx stream 模块，单独目录避免与 http server 混放
NGINX_STREAM_DIR = "/etc/nginx/stream-enabled"
# nginx.conf 中需要注入的 stream include 行，用于加载 stream 配置
NGINX_STREAM_INCLUDE = "include /etc/nginx/stream-enabled/*.conf;"
APACHE_AVAILABLE = "/etc/apache2/sites-available"
APACHE_ENABLED = "/etc/apache2/sites-enabled"

# 站点类型
SITE_TYPE_STATIC = "static"      # 静态网址：根目录 + 域名
SITE_TYPE_PROXY = "proxy"        # 反向代理：监听端口 → 后端地址
SITE_TYPE_TCPUDP = "tcpudp"      # TCP/UDP 代理：协议 + 监听端口 → 上游地址
SITE_TYPE_SUBSITE = "subsite"    # 子网站：子域名绑定到根域名，指向根目录


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
    if _which("nginx"):
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


def _site_server_name(site: dict) -> str:
    """根据站点类型计算 server_name。"""
    site_type = site.get("type", SITE_TYPE_STATIC)
    if site_type == SITE_TYPE_SUBSITE:
        # 子网站：子域名.根域名，兼容通配子域名
        sub = (site.get("subdomain") or "").strip()
        domain = (site.get("domain") or "").strip()
        if sub and domain:
            return f"{sub}.{domain}"
        if domain:
            return f"*.{domain}"
    return " ".join(site.get("domains", [])) or "_"


def _nginx_site_config(site: dict) -> str:
    """根据站点类型生成 nginx http server 配置。"""
    site_type = site.get("type", SITE_TYPE_STATIC)
    server_name = _site_server_name(site)
    root_dir = site.get("root", "/var/www/html")
    port = site.get("port", 80)
    ssl = site.get("ssl", {})
    enable_ssl = ssl.get("enabled", False)
    proxy = site.get("reverse_proxy", "")
    locations = site.get("locations", [])

    lines = [f"server {{"]
    lines.append(f"    listen {port};")
    if enable_ssl and ssl.get("port", 443):
        lines.append(f"    listen {ssl.get('port', 443)} ssl;")
    lines.append(f"    server_name {server_name};")

    if enable_ssl and ssl.get("cert") and ssl.get("key"):
        lines.append(f"    ssl_certificate {ssl['cert']};")
        lines.append(f"    ssl_certificate_key {ssl['key']};")

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
                path = loc.get("path", "/")
                loc_root = loc.get("root", root_dir)
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
    listen_port = site.get("port", 443)
    upstream = site.get("upstream", "")
    lines = [f"stream {{"]
    lines.append(f"    upstream {site.get('id', 'site')}_upstream {{")
    lines.append(f"        server {upstream};")
    lines.append(f"    }}")
    lines.append(f"    server {{")
    lines.append(f"        listen {listen_port} {protocol};")
    lines.append(f"        proxy_pass {site.get('id', 'site')}_upstream;")
    lines.append(f"    }}")
    lines.append("}")
    return "\n".join(lines)


def _ensure_stream_include():
    """确保 nginx.conf 中已注入 stream include 行（幂等）。"""
    try:
        conf_path = host_path(NGINX_CONF)
        with open(conf_path, "r", encoding="utf-8") as f:
            content = f.read()
        if NGINX_STREAM_INCLUDE in content:
            return
        # 在 events {} 块之后插入 stream include，保证位于 http 块之外
        marker = "events {"
        if marker in content:
            idx = content.find(marker)
            end = content.find("}", idx) + 1
            content = content[:end] + "\n" + NGINX_STREAM_INCLUDE + "\n" + content[end:]
        else:
            content = content.rstrip() + "\n\n" + NGINX_STREAM_INCLUDE + "\n"
        with open(conf_path, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception:
        pass


def _apply_nginx_config(site_id: str, site: dict, enabled: bool):
    """按站点类型应用 nginx 配置：static/proxy/subsite 走 http，tcpudp 走 stream。"""
    site_type = site.get("type", SITE_TYPE_STATIC)
    if site_type == SITE_TYPE_TCPUDP:
        # TCP/UDP 代理：stream 配置写到专用目录
        stream_dir = host_path(NGINX_STREAM_DIR)
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
    # 容器模式下映射为 /host/etc/nginx/...，从而操作宿主机 nginx 配置
    avail_dir = host_path(NGINX_AVAILABLE)
    enab_dir = host_path(NGINX_ENABLED)
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
    try:
        host_cmd(["nginx", "-s", "reload"], capture_output=True, check=False, timeout=10)
    except Exception:
        pass


def _apache_site_config(site: dict) -> str:
    domains = " ".join(site.get("domains", []))
    root_dir = site.get("root", "/var/www/html")
    port = site.get("port", 80)
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
    # 仅静态网址/子网站需要创建根目录
    if req.type in (SITE_TYPE_STATIC, SITE_TYPE_SUBSITE) and req.root:
        os.makedirs(host_path(req.root), exist_ok=True)
    site = {
        "id": req.name.lower().replace(" ", "-").replace(".", "-"),
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
