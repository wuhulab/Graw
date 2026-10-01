# -*- coding: utf-8 -*-
"""
phpversions.py - PHP 多版本管理（系统 PHP-FPM 探测 + 站点 PHP 版本关联）

功能：
  1. 探测宿主机已安装的 PHP 版本（PHP-FPM）：
     - Linux：扫描 /usr/bin/php* 与 /usr/sbin/php-fpm*，并运行 `php -v` 校验版本号。
     - Windows：检查 PATH 中是否有 `php`，运行 `php -v`（通常为空，优雅降级）。
  2. 站点 PHP 版本关联：为「静态网址 / 子网站」记录其使用的系统 PHP 版本，
     php_version 持久化到 sites.json，供后续 nginx fastcgi_pass 配置使用。

安全设计：
  - 所有外部命令均以列表 argv 调用 subprocess（绝不使用 shell=True），无 shell 注入。
  - 版本号解析使用白名单正则（数字 + 核心/次要版本号），杜绝意外字符进入后续
    拼接的 nginx fastcgi_pass 配置。
  - 探测失败一律返回空列表，绝不让异常阻塞面板其他功能。
"""
import json
import logging
import os
import platform
import re
import shutil

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from typing import Optional

from app import hostfs
from app.hostfs import host_path, unhost_path
from app import node_manager

logger = logging.getLogger("phpversions")

router = APIRouter()

DATA_DIR = os.path.normpath(
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data"))
)
SITES_FILE = os.path.join(DATA_DIR, "sites.json")

# 版本号白名单：PHP 版本形如 8.2 / 8.2.10 / 8（核心、次要、补丁可选），
# 仅允许数字与点，杜绝任意文本进入后续 fastcgi_pass 拼接。
_PHP_VERSION_RE = re.compile(r"^\d+(\.\d+){0,2}$")

# 系统 PHP 可执行文件探测路径（Linux）
_PHP_BIN_DIRS = ["/usr/bin", "/usr/local/bin"]
_PHPFPM_BIN_DIRS = ["/usr/sbin", "/usr/local/sbin", "/usr/bin"]

# 站点类型：仅静态网址 / 子网站支持 PHP 关联
_PHP_SUPPORTED_TYPES = ("static", "subsite")

# 短的 subprocess 超时，避免探测命令卡住面板接口
_EXEC_TIMEOUT = 8


# ---------------------------------------------------------------------------
# 数据库读写（与 sites 路由保持一致）
# ---------------------------------------------------------------------------
def _load_sites() -> list:
    """读取站点列表；文件缺失/损坏返回空列表。"""
    if not os.path.exists(SITES_FILE):
        return []
    try:
        with open(SITES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, list) else []
    except Exception as e:
        logger.warning("读取 sites.json 失败: %s", e)
        return []


def _save_sites(sites: list) -> None:
    """原子写入站点列表。"""
    os.makedirs(DATA_DIR, exist_ok=True)
    tmp = SITES_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(sites, f, ensure_ascii=False, indent=2)
    os.replace(tmp, SITES_FILE)


def _find_site(site_id: str) -> dict:
    """按 id 查找站点，不存在抛 404。"""
    for s in _load_sites():
        if s.get("id") == site_id:
            return s
    raise HTTPException(status_code=404, detail="站点不存在")


# ---------------------------------------------------------------------------
# PHP 版本探测
# ---------------------------------------------------------------------------
def _run(argv: list) -> Optional[str]:
    """安全执行一条外部命令并返回 stdout（去首尾空白）；失败返回 None。

    argv 使用「宿主机视角」路径（如 /usr/bin/php）：容器 /host 挂载模式下经
    node_manager.host_cmd chroot 到宿主机根执行，避免直接用容器内路径调宿主
    二进制时动态库解析错误；非容器模式等价于直接 subprocess 执行。
    """
    try:
        r = node_manager.host_cmd(
            list(argv), capture_output=True, text=True, timeout=_EXEC_TIMEOUT,
            check=False, errors="replace",
        )
        if r.returncode == 0:
            return r.stdout.strip()
        return None
    except Exception as e:
        logger.debug("执行命令失败 %s: %s", argv[:2], e)
        return None


def _parse_version(stdout: str) -> Optional[str]:
    """从 `php -v` 输出解析主版本号（如 8.2.10 -> 8.2）。

    匹配第一行的 PHP 版本声明（如 "PHP 8.2.10 (cli) ..."），并归一化为
    「最大两位」的版本号（major.minor），便于作为 nginx fastcgi_pass 标识。
    """
    if not stdout:
        return None
    m = re.search(r"PHP\s+v?(\d+(?:\.\d+){0,2})", stdout)
    if not m:
        return None
    raw = m.group(1)
    parts = raw.split(".")
    # 白名单再次校验；归一化为 major.minor（两位）
    ver2 = ".".join(parts[:2]) if len(parts) >= 2 else parts[0]
    ver2 = ver2 or parts[0]
    if not _PHP_VERSION_RE.match(ver2):
        return None
    return ver2


def _fpm_socket_for(version: str) -> str:
    """根据版本号推断常见的 PHP-FPM Unix socket 路径。

    Debian/Ubuntu 下 php-fpm 的默认监听 socket 通常为
    /run/php/php<major.minor>-fpm.sock。仅用于展示，真实 socket 以
    实际检测到的 php-fpm 池配置为准。
    """
    return f"/run/php/php{version}-fpm.sock"


def _detect_linux() -> list:
    """Linux：扫描 php 与 php-fpm 可执行文件，返回已安装版本列表。

    容器 /host 挂载模式：_PHP_BIN_DIRS 是宿主机视角目录（/usr/bin 等），必须先经
    host_path() 映射到容器内实际路径再扫描；对外（path 字段）统一返回宿主机视角
    路径，供后续 nginx 配置/展示与经 host_cmd 的 php -v 解析使用。
    """
    found = []
    discovered = set()

    # 1) 扫描系统目录中的 php<版本> / php-fpm<版本> 可执行文件
    for d in _PHP_BIN_DIRS:
        real_dir = host_path(d)
        if not os.path.isdir(real_dir):
            continue
        for name in os.listdir(real_dir):
            if not name.startswith("php"):
                continue
            m = re.match(r"^php(?P<ver>\d+(?:\.\d+){0,2})$", name)
            if not m:
                continue
            path = os.path.join(real_dir, name)
            if not os.path.isfile(path) or not os.access(path, os.X_OK):
                continue
            ver = _PHP_VERSION_RE.match(m.group("ver"))
            if not ver:
                continue
            if ver.group(0) not in discovered:
                discovered.add(ver.group(0))
                found.append({
                    "version": ver.group(0),
                    "sapi": "cli",
                    "path": unhost_path(path),
                    "fpm_sock": _fpm_socket_for(ver.group(0)),
                })

    # 2) 扫描 php-fpm 可执行文件路径
    for d in _PHPFPM_BIN_DIRS:
        real_dir = host_path(d)
        if not os.path.isdir(real_dir):
            continue
        for name in os.listdir(real_dir):
            if not name.startswith("php-fpm"):
                continue
            m = re.match(r"^php-fpm(?P<ver>\d+(?:\.\d+){0,2})$", name)
            ver = m.group("ver") if m else ""
            path = os.path.join(real_dir, name)
            if not os.path.isfile(path) or not os.access(path, os.X_OK):
                continue
            # 版本号归一化：php-fpm8.2 -> 8.2；无版本号则后续用 php -v 推断
            ver2 = _PHP_VERSION_RE.match(ver).group(0) if ver and _PHP_VERSION_RE.match(ver) else ""
            found.append({
                "version": ver2,
                "sapi": "fpm",
                "path": unhost_path(path),
                "fpm_sock": _fpm_socket_for(ver2) if ver2 else "",
            })

    # 3) 若扫描到通用 php / php-fpm（无版本号），运行 `php -v` 推断版本。
    #    优先用扫描到的 cli 路径（宿主机视角，经 host_cmd chroot 执行）；
    #    容器模式下不使用 shutil.which（那是容器内 PATH，宿主 php 不在其中）。
    bin_php = next(
        (c["path"] for c in found if c["sapi"] == "cli" and c["version"]),
        None,
    )
    if bin_php is None and not hostfs.is_host_mounted():
        bin_php = shutil.which("php")
    if bin_php:
        ver = _parse_version(_run([bin_php, "-v"]))
        if ver and ver not in discovered:
            discovered.add(ver)
            found.append({
                "version": ver,
                "sapi": "cli",
                "path": bin_php,
                "fpm_sock": _fpm_socket_for(ver),
            })

    # 去重：同版本仅保留一条（优先 cli 记录）
    seen = set()
    result = []
    for c in found:
        if not c["version"]:
            continue
        if c["version"] in seen:
            continue
        seen.add(c["version"])
        result.append(c)
    result.sort(key=lambda x: x["version"])
    return result


def _detect_windows() -> list:
    """Windows：检查 PATH 中的 php，运行 `php -v` 推断版本（通常为空）。

    说明：Windows 上无法安装系统 PHP-FPM，故走通用 `php` 探测；未安装则返回空。
    """
    bin_php = shutil.which("php")
    if not bin_php:
        return []
    ver = _parse_version(_run([bin_php, "-v"]))
    if not ver:
        return []
    return [{
        "version": ver,
        "sapi": "cli",
        "path": bin_php,
        "fpm_sock": "",  # Windows 无 FPM socket
    }]


def detect_php_versions() -> list:
    """探测当前平台已安装的 PHP 版本，优雅降级：

    - Linux：扫描系统 php / php-fpm 并运行 php -v。
    - Windows：检查 PATH 中的 php。
    - 任何异常都返回空列表，不阻塞面板。
    """
    try:
        if platform.system() == "Windows":
            return _detect_windows()
        return _detect_linux()
    except Exception as e:
        logger.warning("PHP 版本探测失败: %s", e)
        return []


# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------
class SetPhpRequest(BaseModel):
    """为站点设置 PHP 版本：version 可为空串（表示清除）。"""

    version: str = Field("", max_length=16)


# ---------------------------------------------------------------------------
# API 端点
# ---------------------------------------------------------------------------
@router.get("/list")
async def list_php_versions():
    """返回当前系统已安装的 PHP 版本列表。"""
    found = detect_php_versions()
    return {
        "available": bool(found),
        "php_versions": found,
        "reason": "" if found else "未检测到已安装的系统 PHP 版本",
        "platform": platform.system(),
    }


@router.get("/status")
async def php_versions_status():
    """返回 PHP 多版本功能的状态摘要（是否可用 + 版本列表 + 原因）。"""
    found = detect_php_versions()
    if found:
        reason = f"检测到 {len(found)} 个 PHP 版本"
    else:
        reason = "未检测到已安装的系统 PHP 版本（Windows 需先安装 PHP-FPM）"
    return {"available": bool(found), "php_versions": found, "reason": reason}


@router.get("/sites")
async def sites_php_versions():
    """列出所有站点及其当前绑定的 PHP 版本。"""
    sites = _load_sites()
    result = []
    for s in sites:
        result.append({
            "id": s.get("id"),
            "name": s.get("name"),
            "type": s.get("type"),
            "enabled": s.get("enabled", False),
            "php_version": s.get("php_version") or "",
        })
    return {"sites": result}


@router.post("/site/{site_id}/set-php")
async def set_site_php(site_id: str, req: SetPhpRequest):
    """为站点（静态网址 / 子网站）设置绑定的 PHP 版本。

    version 可为空串（清除绑定）。仅 static / subsite 类型支持 PHP 关联；
    反向代理与 TCP/UDP 代理由自身后端负责，不在此设置。
    """
    version = (req.version or "").strip()
    if version and not _PHP_VERSION_RE.match(version):
        raise HTTPException(status_code=400, detail="PHP 版本格式不合法（仅数字与点）")

    sites = _load_sites()
    site = next((s for s in sites if s.get("id") == site_id), None)
    if not site:
        raise HTTPException(status_code=404, detail="站点不存在")

    if site.get("type") not in _PHP_SUPPORTED_TYPES:
        raise HTTPException(
            status_code=400,
            detail="仅静态网址 / 子网站类型支持绑定系统 PHP 版本",
        )

    site["php_version"] = version
    _save_sites(sites)
    logger.info("站点 %s 绑定 PHP 版本: %s", repr(site_id), repr(version))
    return site