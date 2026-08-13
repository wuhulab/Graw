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
APACHE_AVAILABLE = "/etc/apache2/sites-available"
APACHE_ENABLED = "/etc/apache2/sites-enabled"


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


def _nginx_site_config(site: dict) -> str:
    domains = " ".join(site.get("domains", []))
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
    lines.append(f"    server_name {domains};")
    lines.append(f"    root {root_dir};")
    lines.append(f"    index index.html index.htm index.php;")

    if enable_ssl and ssl.get("cert") and ssl.get("key"):
        lines.append(f"    ssl_certificate {ssl['cert']};")
        lines.append(f"    ssl_certificate_key {ssl['key']};")

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


def _apply_nginx_config(site_id: str, site: dict, enabled: bool):
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
    domains: List[str]
    root: str = Field(..., min_length=1)
    port: int = Field(80, ge=1, le=65535)
    ssl: Optional[dict] = Field(default_factory=dict)
    reverse_proxy: Optional[str] = ""


class SiteAction(BaseModel):
    action: str  # start, stop, restart, enable, disable


class UpdateSite(BaseModel):
    domains: Optional[List[str]] = None
    root: Optional[str] = None
    port: Optional[int] = None
    ssl: Optional[dict] = None
    reverse_proxy: Optional[str] = None
    locations: Optional[List[dict]] = None


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
    site = {
        "id": req.name.lower().replace(" ", "-").replace(".", "-"),
        "name": req.name,
        "domains": req.domains,
        "root": req.root,
        "port": req.port,
        "ssl": req.ssl or {},
        "reverse_proxy": req.reverse_proxy or "",
        "locations": [],
        "enabled": False,
        "created_at": datetime.now().isoformat(),
    }
    os.makedirs(host_path(req.root), exist_ok=True)
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
    if req.domains is not None:
        site["domains"] = req.domains
    if req.root is not None:
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
