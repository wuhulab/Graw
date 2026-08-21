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
    # 优先按当前引擎模式返回（设置里选了 OpenResty 就显示 openresty，而非笼统 nginx）；
    # 否则回退 nginx 系探测。配置生成逻辑对 nginx/openresty 共用同一套格式。
    if webserver.is_openresty() and webserver.available(webserver.MODE_OPENRESTY):
        return "openresty"
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


# ---------------------------------------------------------------------------
# 真实站点发现：把当前节点上「已存在的 nginx/openresty 站点配置」识别出来，
# 与面板自建站点（sites.json）合并展示，让用户能直接在「网站」里看到管理。
# 覆盖两类目录：
#   - 标准引擎可用目录（webserver.available_dir()，如 ……/sites-available）
#   - 1Panel 等面板把站点放在 /opt/1panel/www/conf.d（容器内由 conf.d 加载）
# 解析 .conf 提取 server_name / root，仅展示不回写面板数据。
# ---------------------------------------------------------------------------
_EXT_SITE_ID_RE = re.compile(r"[^a-z0-9_-]+")


def _ext_site_id(server_name: str, index: int) -> str:
    """为真实站点生成稳定的面板内 id（避免与自建站点 id 冲突）。"""
    base = _EXT_SITE_ID_RE.sub("-", (server_name or "ext").lower()).strip("-") or "ext"
    return f"ext-{base}-{index}"


def _parse_server_name(conf: str) -> str:
    """从 nginx 配置提取 server_name（取第一个命中，含 _ 通配）。"""
    for m in re.finditer(r"server_name\s+([^;]+);", conf):
        names = m.group(1).split()
        if names:
            return names[0].strip()
    return ""


def _iter_location_blocks(conf: str):
    """迭代 nginx 顶层 location 块，产出 (path, body)。

    用逐个字符匹配花括号的方式保留嵌套块（如 location 内再套 location），
    避免简单正则误截。识别含修饰符的 location 入口（location = /、~ /、
    ~* /、^~ /）：正则先吃可选的 = | ~ | ~* | ^~ 修饰符，再捕获真实路径。
    """
    for m in re.finditer(
        r"\blocation\s+(?:(?:=|~{1,2}|\^~)\s*)?(\S+)\s*\{",
        conf,
    ):
        path = m.group(1)
        if not path or path in ("{"):
            continue
        start = m.end() - 1  # 指向入口 '{'
        depth = 1
        i = start + 1
        while i < len(conf) and depth:
            c = conf[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
            i += 1
        yield path, conf[start + 1 : i - 1]


def _parse_listen(conf: str) -> int:
    """从 nginx 配置提取 server 块监听端口（handle listen 80、listen 443 ssl 等）。"""
    for m in re.finditer(r"\blisten\s+(?:[^\s:;]+:)?(\d+)", conf):
        return int(m.group(1))
    return 80


def _parse_proxy_pass(conf: str) -> str:
    """从 nginx 配置提取「根路径 location / 的 proxy_pass」（整站反向代理目标）。

    仅当 location 路径为 /（未限定子路径）且块内存在 proxy_pass 时才视为反向代理，
    避免把静态站点中某个子路径（如 /api）误判为整站反向代理。命中返回如
    http://127.0.0.1:15874 的后端地址，否则返回空串。
    """
    for path, body in _iter_location_blocks(conf):
        if path != "/" or not body:
            continue
        pm = re.search(r"proxy_pass\s+([^;]+);", body)
        if pm:
            return pm.group(1).strip()
    return ""


def _parse_root_dir(conf: str) -> str:
    """从 nginx 配置提取 server 块的 root 目录（site 根目录）。

    排除 location 块内的 root（如 /.well-known 里的 /usr/share/nginx/html），
    优先取「server 块级别」的 root——取最后一个非 location 内的 root 命中。
    """
    # 找到所有 location 块的起止，据此挖掉它们，剩余再看 root
    try:
        # 简单策略：把 `location` 开头的块整体剔除后，剩下的非嵌套 root 即 server root
        # 用正则切掉 location ... { ... } 块，再取最后的 root
        stripped = re.sub(
            r"\blocation\b[^{}]*\{[^{}]*\}",
            "",
            conf,
            flags=re.DOTALL,
        )
        matches = list(re.finditer(r"(?:^|\s)root\s+(.+?);", stripped))
        if matches:
            return matches[-1].group(1).strip()
    except Exception:
        pass
    return ""


def _resolve_site_conf(conf_path: str) -> str:
    """读取站点主配置，并递归拼接其 `include` 的片段（如 1Panel 的反代配置）。

    1Panel 会在主配置里写 `include .../proxy/*.conf;` 装载反向代理等片段，
    而该 include 路径是「容器内视角」（/www/sites/...）。代理在宿主机运行，
    实际文件位于 /opt/1panel/www/sites/...，因此这里把 include 路径做一次
    /www/sites/ → /opt/1panel/www/sites/ 映射后再 glob 取文件内容。
    """
    parts = []
    try:
        with open(conf_path, "r", encoding="utf-8", errors="replace") as f:
            parts.append(f.read())
    except Exception:
        return "".join(parts)
    for inc in re.finditer(r"\binclude\s+([^;]+);", parts[0]):
        pattern = inc.group(1).strip()
        # 容器内 /www/sites 未直接挂在宿主上 → 映射到 1Panel 宿主站点目录
        pattern = re.sub(r"^/www/sites/", "/opt/1panel/www/sites/", pattern)
        try:
            import glob as _glob

            for p in _glob.glob(pattern) or []:
                try:
                    with open(p, "r", encoding="utf-8", errors="replace") as f:
                        parts.append("\n" + f.read())
                except Exception:
                    pass
        except Exception:
            pass
    return "\n".join(parts)


def _existing_site_dirs() -> List[str]:
    """返回要扫描的站点配置目录（去重、仅存在的）。"""
    dirs = []
    try:
        d = webserver.available_dir()
        if d:
            dirs.append(d)
    except Exception:
        pass
    # 1Panel：宿主 /opt/1panel/www/conf.d（容器内 conf.d 加载，同一份配置）
    for extra in ("/opt/1panel/www/conf.d",):
        if extra not in dirs:
            dirs.append(extra)
    return dirs


def _discover_existing_sites() -> List[dict]:
    """扫描当前节点上已存在的站点配置，返回「外部站点」列表（来源外部，不写改）。"""
    found = []
    seen_ids = set()
    idx = 0
    for d in _existing_site_dirs():
        try:
            if not os.path.isdir(d):
                continue
            names = sorted(os.listdir(d))
        except Exception:
            continue
        for conf_name in names:
            if not conf_name.endswith(".conf"):
                continue
            conf_path = os.path.join(d, conf_name)
            conf = _resolve_site_conf(conf_path)
            server_name = _parse_server_name(conf)
            site_id = _ext_site_id(server_name or conf_name, idx)
            idx += 1
            if site_id in seen_ids:
                continue
            seen_ids.add(site_id)
            # 识别类型：根路径 location / 带 proxy_pass → 反向代理；否则当静态站点
            proxy = _parse_proxy_pass(conf)
            site_type = SITE_TYPE_PROXY if proxy else SITE_TYPE_STATIC
            listen_port = _parse_listen(conf)
            found.append({
                "id": site_id,
                "name": server_name or os.path.splitext(conf_name)[0],
                "type": site_type,
                "domains": [server_name] if server_name else [],
                "root": _parse_root_dir(conf),
                "port": listen_port,
                "ssl": {},
                "reverse_proxy": proxy,
                "locations": [],
                "protocol": "tcp",
                "upstream": "",
                "subdomain": "",
                "domain": "",
                "enabled": True,
                "external": True,  # 标记：来自服务器真实配置，面板只读展示
                "config_file": conf_path,
                "created_at": "",
            })
    return found


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
            # 伪静态规则：用户通过「伪静态规则库」应用的模板 id（白名单）
            # 若存在，用模板片段替代默认 location /（try_files 直出）
            rewrite_id = site.get("rewrite", "") or ""
            rewrite_frag = ""
            if rewrite_id:
                # 仅在规则 id 命中白名单模板时注入，绝不允许任意文本进配置
                from app.routers.rewrite import get_nginx_fragment
                rewrite_frag = get_nginx_fragment(rewrite_id)
            for loc in locations:
                path = _conf_token(loc.get("path") or "/", "/")
                loc_root = _conf_token(loc.get("root") or root_dir, root_dir)
                lines.append(f"    location {path} {{")
                lines.append(f"        root {loc_root};")
                lines.append(f"        try_files $uri $uri/ =404;")
                lines.append(f"    }}")
            if not locations:
                if rewrite_frag:
                    # 伪静态片段本身是完整的 location 块，注入时统一加缩进
                    lines.extend("    " + ln if ln.strip() else ln for ln in rewrite_frag.split("\n"))
                else:
                    lines.append(f"    location / {{")
                    lines.append(f"        try_files $uri $uri/ =404;")
                    lines.append(f"    }}")

    # 站点增强配置：防盗链 / gzip / 静态资源缓存（见 sitesopts 路由）
    try:
        from app.routers.sitesopts import get_nginx_extra

        extra = get_nginx_extra(site)
        if extra:
            lines.extend(ln if ln.strip() else ln for ln in extra.split("\n"))
    except Exception:
        # 增强配置生成失败不阻断站点配置主体（静态配置仍可用）
        pass

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
    # 合并「真实存在的站点」（服务器上已配置，面板只读展示），与自建站点去重
    builtin_domains = {
        d.lower()
        for s in sites
        for d in (s.get("domains") or [])
    }
    external = [_ for _ in _discover_existing_sites()
                if not any(d.lower() in builtin_domains for d in (_["domains"] or []))]
    merged = sites + external
    for s in merged:
        s["web_server"] = ws
        s["online"] = _site_status_by_port(s.get("port", 80))
        s.setdefault("external", False)
    return {"sites": merged, "web_server": ws}


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
