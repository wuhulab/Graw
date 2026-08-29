import hashlib
import json
import logging
import os
import asyncio
import time
import platform
import re
import subprocess
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.hostfs import host_path, host_cmd, host_which
from app import webserver

logger = logging.getLogger("graw.sites")

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
# 安全：锚点用 \Z 而非 $——Python 的 $ 允许匹配尾换行前的位置，
# "evil.com\n" 可通过校验并把换行注入 nginx 配置行。
_DOMAIN_RE = re.compile(
    r"^(\*\.)?([A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?\.)+[A-Za-z]{2,63}\Z"
)

# 子域名前缀（拼入 server_name）：字母/数字/中划线，允许 * 通配
_SUBDOMAIN_RE = re.compile(r"^[A-Za-z0-9*][A-Za-z0-9*-]*$")

# 反向代理/TCP/UDP 的「域名」：靠端口/上游地址访问，允许 IP、localhost、
# 单级或多级域名、通配符。仅拒绝会破坏配置的字符（冒号端口/斜杠/空白/分号/花括号）。
_PROXY_DOMAIN_RE = re.compile(r"^[A-Za-z0-9*.-]+\Z")

# 反向代理目标：http(s)://host[:port][/path]（支持下划线的服务名/主机名）
_PROXY_RE = re.compile(r"^https?://[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%_-]+$")

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


def _normalize_proxy(value) -> str:
    """规范反向代理目标地址：缺少协议时自动补 http://，避免因省略协议被 400 拒绝。"""
    if not value:
        return ""
    v = str(value).strip()
    if v and not re.match(r"^https?://", v, re.IGNORECASE):
        v = "http://" + v
    return v


def _proxy_ssl_options(proxy: str) -> list:
    """当上游为 https（如宝塔/Graw 兼容面板仅接受 TLS、明文 HTTP 会 RST 导致 502）时，
    生成关闭证书校验与 SNI 下发所需的 nginx 指令。

    面板类上游常使用自签或非承载域名匹配的证书，若按系统 CA 校验握手会失败，
    从而让代理稳定返回 502。这里仅对 https 上游生效，http 上游不受影响。
    """
    if not str(proxy or "").lower().startswith("https://"):
        return []
    return [
        "        proxy_ssl_verify off;",
        "        proxy_ssl_server_name off;",
        "        proxy_ssl_session_reuse on;",
    ]


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
        # 反向代理/TCP-UDP 靠端口与上游地址访问，允许 IP/localhost/单级域名；
        # 静态与子网站必须提供真实多级域名用于域名解析。
        loose = site_type in (SITE_TYPE_PROXY, SITE_TYPE_TCPUDP)
        rule = _PROXY_DOMAIN_RE if loose else _DOMAIN_RE
        for d in domains:
            if not isinstance(d, str) or not rule.match(d):
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
    # 站点列表变更，使 merged_sites 的 TTL 缓存立即失效（下次重新发现/解析）
    _invalidate_merged_cache()


# 外部站点「显示名称」覆盖：外部站点由真实配置驱动（每次发现 name 复原），
# 用户自定义的名称单独存于此，list 时叠加上去保持改名不丢。
SITES_NAMES_FILE = os.path.join(DATA_DIR, "sites_names.json")


def _load_external_names() -> dict:
    if not os.path.exists(SITES_NAMES_FILE):
        return {}
    try:
        with open(SITES_NAMES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_external_names(names: dict):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(SITES_NAMES_FILE, "w", encoding="utf-8") as f:
        json.dump(names, f, ensure_ascii=False, indent=2)
    # 显示名称变更同样影响合并结果，使 TTL 缓存失效
    _invalidate_merged_cache()


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

    路径处理：conf_path 是「宿主机视角」绝对路径，读取前经 host_path() 映射
    到容器内实际路径（HOST_ROOT=/host 挂载模式），否则在容器内找不到宿主的
    /opt/1panel/www/conf.d 从而漏掉全部外部站点；非容器模式映射为原样。
    """
    parts = []
    try:
        with open(host_path(conf_path), "r", encoding="utf-8", errors="replace") as f:
            parts.append(f.read())
    except Exception as e:
        logger.warning("站点配置读取失败: %s (%s)", host_path(conf_path), e)
        return "".join(parts)
    for inc in re.finditer(r"\binclude\s+([^;]+);", parts[0]):
        pattern = inc.group(1).strip()
        # 容器内 /www/sites 未直接挂在宿主上 → 映射到 1Panel 宿主站点目录
        pattern = re.sub(r"^/www/sites/", "/opt/1panel/www/sites/", pattern)
        try:
            import glob as _glob

            for p in _glob.glob(host_path(pattern)) or []:
                try:
                    with open(p, "r", encoding="utf-8", errors="replace") as f:
                        parts.append("\n" + f.read())
                except Exception as e:
                    logger.debug("include 片段读取失败: %s (%s)", p, e)
        except Exception as e:
            logger.debug("include 路径解析失败: %s (%s)", pattern, e)
    return "\n".join(parts)


def _existing_site_dirs() -> List[dict]:
    """返回要扫描的站点配置目录（去重、仅存在的），并标注其来源。

    source 用于区分站点来自标准 nginx/openresty 目录（nginx）还是
    1Panel 专用目录（1panel），前端据此展示「1Panel兼容」标签。
    """
    dirs = []
    try:
        d = webserver.available_dir()
        if d:
            dirs.append({"path": d, "source": "nginx"})
    except Exception:
        pass
    # 1Panel：宿主 /opt/1panel/www/conf.d（容器内 conf.d 加载，同一份配置）
    existing = {x["path"] for x in dirs}
    if "/opt/1panel/www/conf.d" not in existing:
        dirs.append({"path": "/opt/1panel/www/conf.d", "source": "1panel"})
    return dirs


def _discover_existing_sites() -> List[dict]:
    """扫描当前节点上已存在的站点配置，返回「外部站点」列表（来源外部，不写改）。

    目录与文件路径统一按 host_path() 映射：_existing_site_dirs 返回的是「宿主机
    视角」路径（如 /opt/1panel/www/conf.d），容器 /host 挂载模式下实际文件在
    HOST_ROOT 前缀下，不映射会扫描不到任何外部站点（自建站点仍显示，造成
    「1Panel兼容/反向代理站点不展示」）。config_file 仍保存宿主机视角路径，
    与 _apply_external_nginx_config 中的 host_path() 写回逻辑呼应。
    """
    found = []
    seen_ids = set()
    idx = 0
    for dir_entry in _existing_site_dirs():
        d = dir_entry["path"]
        source = dir_entry.get("source", "nginx")
        real_dir = host_path(d)
        try:
            if not os.path.isdir(real_dir):
                logger.debug("站点配置目录不存在，跳过扫描: %s", real_dir)
                continue
            names = sorted(os.listdir(real_dir))
        except Exception as e:
            logger.warning("站点配置目录扫描失败: %s (%s)", real_dir, e)
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
                "source": source,  # 来源目录：nginx / 1panel（供「1Panel兼容」标签）
                "config_file": conf_path,
                "created_at": "",
            })
    return found


def _find_external_site(site_id: str) -> Optional[dict]:
    """按 id 在真实站点发现结果里反查外部站点（供外部站点的编辑/查看配置）。"""
    for s in _discover_existing_sites():
        if s.get("id") == site_id:
            return s
    return None


def _apply_external_nginx_config(site: dict, enabled: bool):
    """把面板生成的 nginx server 配置写回该外部站点的真实 conf 文件。

    外部站点来自服务器真实配置（如 1Panel /opt/1panel/www/conf.d），编辑后
    直接改写其 config_file，使改动对真实网站生效，保持与 1Panel 站点一致。
    """
    conf_path = host_path(site.get("config_file") or "")
    if not conf_path:
        return
    if enabled:
        os.makedirs(os.path.dirname(conf_path), exist_ok=True)
        with open(conf_path, "w", encoding="utf-8") as f:
            f.write(_nginx_site_config(site))
    else:
        if os.path.exists(conf_path):
            os.remove(conf_path)
    _reload_nginx()


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
    """根据站点类型生成 nginx http server 配置。

    在 1Panel/容器化 openresty 下，站点常经 CDN 用 https(443) 回源，
    只 listen 80 会落到默认 443 server 返回 404；故检测到 1Panel 默认
    证书时额外生成一个 443 ssl server，保证 443 也命中该站点。
    """
    site_type = site.get("type", SITE_TYPE_STATIC)
    server_name = _site_server_name(site)
    # 所有插值经 _conf_token 清洗（纵深防御，见函数注释）
    root_dir = _conf_token(site.get("root") or "/var/www/html")
    port = site.get("port", 80)
    ssl = site.get("ssl", {}) or {}
    enable_ssl = ssl.get("enabled", False) and ssl.get("cert") and ssl.get("key")
    proxy = _conf_token(site.get("reverse_proxy", ""))
    locations = site.get("locations", []) or []

    # 站点内容（location / 代理或静态）；两种 Server 复用同一套
    def _content() -> list:
        body = []
        if site_type == SITE_TYPE_PROXY:
            if proxy:
                body.append(f"    location / {{")
                body.append(f"        proxy_pass {proxy};")
                body.extend(_proxy_ssl_options(proxy))
                body.append(f"        proxy_set_header Host $host;")
                body.append(f"        proxy_set_header X-Real-IP $remote_addr;")
                body.append(f"        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;")
                body.append(f"        proxy_set_header X-Forwarded-Proto $scheme;")
                body.append(f"    }}")
        else:
            body.append(f"    root {root_dir};")
            body.append(f"    index index.html index.htm index.php;")
            if proxy:
                body.append(f"    location / {{")
                body.append(f"        proxy_pass {proxy};")
                body.extend(_proxy_ssl_options(proxy))
                body.append(f"        proxy_set_header Host $host;")
                body.append(f"        proxy_set_header X-Real-IP $remote_addr;")
                body.append(f"    }}")
            else:
                rewrite_id = site.get("rewrite", "") or ""
                rewrite_frag = ""
                if rewrite_id:
                    from app.routers.rewrite import get_nginx_fragment
                    rewrite_frag = get_nginx_fragment(rewrite_id)
                for loc in locations:
                    path = _conf_token(loc.get("path") or "/", "/")
                    loc_root = _conf_token(loc.get("root") or root_dir, root_dir)
                    body.append(f"    location {path} {{")
                    body.append(f"        root {loc_root};")
                    body.append(f"        try_files $uri $uri/ =404;")
                    body.append(f"    }}")
                if not locations:
                    if rewrite_frag:
                        body.extend("    " + ln if ln.strip() else ln for ln in rewrite_frag.split("\n"))
                    else:
                        body.append(f"    location / {{")
                        body.append(f"        try_files $uri $uri/ =404;")
                        body.append(f"    }}")
        try:
            from app.routers.sitesopts import get_nginx_extra
            extra = get_nginx_extra(site)
            if extra:
                body.extend(ln if ln.strip() else ln for ln in extra.split("\n"))
        except Exception:
            pass
        return body

    servant = []
    # 主 Server（监听站点端口，通常是 80）
    servant.append("server {")
    servant.append(f"    listen {int(port)};")
    if enable_ssl and ssl.get("port", 443):
        servant.append(f"    listen {int(ssl.get('port', 443))} ssl;")
    servant.append(f"    server_name {server_name};")
    if enable_ssl:
        servant.append(f"    ssl_certificate {_conf_token(ssl['cert'])};")
        servant.append(f"    ssl_certificate_key {_conf_token(ssl['key'])};")
    servant.extend(_content())
    servant.append("}")

    # 1Panel 容器化 openresty：额外 443 ssl server（复用默认证书），
    # 避免 CDN/浏览器走 https(443) 回源时落到默认 server 而 404。
    if webserver.is_openresty() and not enable_ssl:
        # 宿主挂载源（检测文件是否存在）与容器内挂载点（写入配置）不同：
        # 宿主机 ../conf/ssl -> 容器 /usr/local/openresty/nginx/conf/ssl
        host_cert = "/opt/1panel/apps/openresty/openresty/conf/ssl/fullchain.pem"
        host_key = "/opt/1panel/apps/openresty/openresty/conf/ssl/privkey.pem"
        if os.path.isfile(host_path(host_cert)) and os.path.isfile(host_path(host_key)):
            if port != 443:
                cert = "/usr/local/openresty/nginx/conf/ssl/fullchain.pem"
                key = "/usr/local/openresty/nginx/conf/ssl/privkey.pem"
                servant.append("server {")
                servant.append("    listen 443 ssl;")
                servant.append(f"    server_name {server_name};")
                servant.append(f"    ssl_certificate {cert};")
                servant.append(f"    ssl_certificate_key {key};")
                servant.extend(_content())
                servant.append("}")

    return "\n".join(servant)


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
    # 1Panel/容器化 openresty：站点 conf 直接落宿主机 conf.d（容器 conf.d 加载，
    # 与外部站点同目录），而标准 nginx 布局的 sites-available/enabled 不会被
    # 该容器读入，会把自建站点写成“读不到”直到 404。检测到 1Panel 挂载点
    # 时优先写 conf.d（普通文件，无需软链）；否则回退标准 sites-enabled 软链。
    conf_dir = None
    if webserver.is_openresty() and os.path.isdir(host_path("/opt/1panel/www/conf.d")):
        conf_dir = host_path("/opt/1panel/www/conf.d")
    if conf_dir is None:
        conf_dir = host_path(webserver.enabled_dir())
    conf = os.path.join(conf_dir, conf_name)
    if enabled:
        os.makedirs(conf_dir, exist_ok=True)
        with open(conf, "w", encoding="utf-8") as f:
            f.write(_nginx_site_config(site))
    else:
        if os.path.exists(conf):
            os.remove(conf)


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
    # 安全：site_id 白名单校验（与 _apply_nginx_config 一致，防止路径穿越）
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{1,127}", site_id or ""):
        raise HTTPException(status_code=400, detail="站点 ID 非法")
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
    name: Optional[str] = Field(None, max_length=64)
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


# merged_sites 结果缓存：站点发现（读 sites.json + 解析真实 nginx conf + glob 目录）
# 与域名去重是相对重的磁盘/SSH 操作，而 list / WAF / 站点增强下拉会在短时间内
# 多次调用。TTL 缓存避免每次请求都重新解析，同时保证配置变更后不超过 2 秒生效。
_merged_cache: Optional[List[dict]] = None
_merged_cache_at = 0.0
_MERGED_CACHE_TTL = 2.0


def _invalidate_merged_cache() -> None:
    """使 merged_sites 的缓存失效（站点/外部名称变更时调用）。"""
    global _merged_cache, _merged_cache_at
    _merged_cache = None
    _merged_cache_at = 0.0


def merged_sites() -> List[dict]:
    """自建站点 + 外部真实站点 的去重合并列表。

    供「网站」列表与 WAF / 站点增强等下拉复用，保证各应用看到同样一组站点。
    带 2 秒 TTL 缓存：站点发现与 nginx conf 解析较重，避免每次请求重复执行。
    """
    global _merged_cache, _merged_cache_at
    now = time.time()
    if _merged_cache is not None and (now - _merged_cache_at) < _MERGED_CACHE_TTL:
        # 深拷贝返回，避免调用方修改污染缓存（如 list_sites 叠加 web_server/online）
        return [dict(s) for s in _merged_cache]
    sites = _load_sites()
    builtin_domains = {
        d.lower()
        for s in sites
        for d in (s.get("domains") or [])
    }
    external = [
        _ for _ in _discover_existing_sites()
        if not any(d.lower() in builtin_domains for d in (_["domains"] or []))
    ]
    # 叠加自定义显示名称（外部站点改名持久化）
    names = _load_external_names()
    for e in external:
        if e.get("id") in names and names[e["id"]]:
            e["name"] = names[e["id"]]
    _merged_cache = sites + external
    # 缓存时间戳用于 merged_sites 的 TTL 判定
    _merged_cache_at = now  # lgtm[py/unused-global-variable]
    return [dict(s) for s in _merged_cache]


@router.get("/list")
async def list_sites():
    # _web_server_type（subprocess/SSH 探测）、_discover_existing_sites（解析
    # nginx conf）、_site_status_by_port（psutil 全量网络连接扫描）均为阻塞操作，
    # 放线程池避免卡事件循环。
    return await asyncio.to_thread(_list_sites_sync)


def _list_sites_sync() -> dict:
    ws = _web_server_type()
    merged = merged_sites()
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
    # 反向代理地址补全协议（缺 http:// 时自动补），保证后续校验通过
    req.reverse_proxy = _normalize_proxy(req.reverse_proxy)
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
    # 阻止路径分隔符 / Windows 盘符（c:）等造成任意路径写入。
    # 中文等无法 ASCII 化的站名，退化为「site-<名称哈希>」稳定 ID，
    # 既保证配置文件名安全可写，也允许用户用中文命名站点。
    site_id = re.sub(r"[^a-z0-9_-]+", "-", req.name.lower()).strip("-")
    if not _SITE_ID_RE.match(site_id):
        site_id = "site-" + hashlib.sha1(req.name.encode("utf-8")).hexdigest()[:10]
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
        if ws in ("nginx", "openresty"):
            _apply_nginx_config(site_id, site, True)
            _reload_nginx()
        elif ws == "apache":
            _apply_apache_config(site_id, site, True)
    elif req.action == "disable":
        site["enabled"] = False
        if ws in ("nginx", "openresty"):
            _apply_nginx_config(site_id, site, False)
            _reload_nginx()
        elif ws == "apache":
            _apply_apache_config(site_id, site, False)
    elif req.action == "start":
        site["enabled"] = True
        if ws in ("nginx", "openresty"):
            _apply_nginx_config(site_id, site, True)
            _reload_nginx()
    elif req.action == "stop":
        site["enabled"] = False
        if ws in ("nginx", "openresty"):
            _apply_nginx_config(site_id, site, False)
            _reload_nginx()
    elif req.action == "restart":
        if ws in ("nginx", "openresty"):
            _reload_nginx()
    _save_sites(sites)
    return {"ok": True, "enabled": site["enabled"]}


@router.get("/{site_id}/config")
async def get_site_config(site_id: str):
    sites = _load_sites()
    site = next((s for s in sites if s["id"] == site_id), None)
    if not site:
        # 外部站点：不在面板 sites.json，从真实配置发现结果反查
        site = _find_external_site(site_id)
        if not site:
            raise HTTPException(status_code=404, detail="Site not found")
    ws = _web_server_type()
    if ws in ("nginx", "openresty"):
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
    external = False
    if not site:
        # 外部站点：不在面板 sites.json，编辑后写回其真实 conf 文件
        site = _find_external_site(site_id)
        if not site:
            raise HTTPException(status_code=404, detail="Site not found")
        external = True
    # 反向代理地址补全协议（缺 http:// 时自动补），保证后续校验通过
    req.reverse_proxy = _normalize_proxy(req.reverse_proxy)
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
    if req.name is not None:
        site["name"] = req.name
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
    # 外部站点：直接改写其真实 conf 文件（不写入面板 sites.json，
    # 列表会从真实配置重新发现，天然反映改动）
    if external:
        # 自定义显示名称单独持久化（外部站点由真实配置驱动，需叠加覆盖）
        if req.name is not None:
            names = _load_external_names()
            if req.name.strip():
                names[site.get("id")] = req.name.strip()
            else:
                names.pop(site.get("id"), None)
            _save_external_names(names)
        if site.get("enabled"):
            _apply_external_nginx_config(site, True)
        return site
    ws = _web_server_type()
    if site.get("enabled"):
        if ws in ("nginx", "openresty"):
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
    if ws in ("nginx", "openresty"):
        _apply_nginx_config(site_id, site, False)
        _reload_nginx()
    elif ws == "apache":
        _apply_apache_config(site_id, site, False)
    sites = [s for s in sites if s["id"] != site_id]
    _save_sites(sites)
    return {"ok": True}
