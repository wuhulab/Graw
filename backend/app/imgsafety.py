# -*- coding: utf-8 -*-
"""
imgsafety.py - Docker 镜像漏洞扫描核心库（本地 advisory 库，不依赖外部 API）

背景：
  面板管理的 Docker 镜像含第三方软件包，可能存在已知漏洞。本模块在
  「当前节点」上基于镜像跑一个临时容器取出包清单（dpkg / rpm），与本地
  自维护的 advisory 库（data/advisory.json）做版本比对，输出命中 CVE 列表。

设计要点：
  - 无外部 CVE 数据源：advisory 由管理员导入（常见格式 JSON），扫描纯本地；
    未导入任何数据时扫描结果为空（仅返回包清单）。
  - 版本比较用轻量「语义段比较」，容忍常见后缀（alpha/beta/rc/p1 等），
    不引入第三方库。
  - 运行一次性容器：--rm --entrypoint sh，无卷、无网络依赖命令；
    容器无 sh 时返回可读错误；输出截断防滥用。
  - 结果按 image_id 缓存（data/image_scan_cache.json），同镜像不重复扫。
  - image_id 白名单（复用 docker 引用安全清洗），杜绝 CLI 注入。
"""

import json
import logging
import os
import re
import threading
import time
import uuid
from typing import List, Optional

logger = logging.getLogger("graw.imgsafety")

DATA_DIR = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "data"))
ADVISORY_FILE = os.path.join(DATA_DIR, "advisory.json")
SCAN_CACHE_FILE = os.path.join(DATA_DIR, "image_scan_cache.json")

# 镜像引用安全清洗（docker 对 image 标识符严格限制字符集）
_SAFE_IMG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./:@-]{0,255}$")
# 单次扫描超时（秒）与输出上限（包清单超过即截断）
SCAN_TIMEOUT = 120
MAX_PKG_OUTPUT = 2 * 1024 * 1024

# 容器内取包清单的 shell 脚本（dpkg 优先，rpm 兜底，均可用时都收集）
_PKG_SCRIPT = (
    "if command -v dpkg-query >/dev/null 2>&1; then "
    "dpkg-query -W -f '${Package}\\t${Version}\\n' 2>/dev/null; fi; "
    "if command -v rpm >/dev/null 2>&1; then "
    "rpm -qa --queryformat '%{NAME}\\t%{VERSION}\\n' 2>/dev/null; fi"
)

# advisory 单条结构约束（导入时校验）
_REQ_ADVISORY_FIELDS = ("name", "versions", "cve")

_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Advisory 库
# ---------------------------------------------------------------------------
def _default_advisory() -> dict:
    return {"version": 1, "packages": []}


def load_advisory() -> list:
    """读取本地 advisory（损坏/缺失返回空）。"""
    if not os.path.exists(ADVISORY_FILE):
        return []
    try:
        with open(ADVISORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        pkgs = data.get("packages", []) if isinstance(data, dict) else []
        return pkgs if isinstance(pkgs, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def import_advisory(packages: list) -> dict:
    """校验并合入 advisory（按 (name, cve) 去重），返回新增/总数。

    每项须包含 name/versions(约束串，如 '<= 3.0.14')/cve/severity/desc。
    """
    valid, added = [], 0
    for p in packages:
        if not isinstance(p, dict):
            continue
        if not all(isinstance(p.get(k), str) and p[k].strip() for k in _REQ_ADVISORY_FIELDS):
            continue
        if not _parse_constraint(p.get("versions", "")):
            continue
        valid.append(
            {
                "name": p["name"].strip().lower(),
                "versions": p["versions"].strip(),
                "cve": p["cve"].strip().upper(),
                "severity": str(p.get("severity", "unknown")).strip().lower(),
                "desc": str(p.get("desc", "")).strip()[:500],
            }
        )
    with _lock:
        existing = load_advisory()
        seen = {(e.get("name"), e.get("cve")) for e in existing}
        for p in valid:
            key = (p["name"], p["cve"])
            if key not in seen:
                existing.append(p)
                seen.add(key)
                added += 1
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = ADVISORY_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "packages": existing}, f, ensure_ascii=False, indent=2)
        os.replace(tmp, ADVISORY_FILE)
    return {"imported": added, "total": len(existing)}


# ---------------------------------------------------------------------------
# 版本比较（语义段比较，容忍常见后缀）
# ---------------------------------------------------------------------------
def _split_version(v: str) -> list:
    """把版本串拆成可比较段：全小写，数字保留为数值，其余按字符串段。"""
    out = []
    for part in re.split(r"[.\-_+]", v.strip().lower()):
        part = part.strip()
        if not part:
            continue
        if part.isdigit():
            out.append((0, int(part)))
        else:
            # 文本段按字典序（alpha<beta<...）；统一后移保证数字优先
            out.append((1, part))
    return out


def _vtuple(v: str) -> list:
    parts = _split_version(v)
    # 尾部空段补齐，保证长度比较安全
    return parts + [(0, 0)] * (10 - len(parts)) if len(parts) < 10 else parts


def _vcmp(a: str, b: str) -> int:
    """版本比较：a>b 返回 1，相等返回 0，否则 -1。"""
    aa, bb = _vtuple(a), _vtuple(b)
    for (ta, va), (tb, vb) in zip(aa, bb):
        if va == vb and ta == tb:
            continue
        if ta != tb:
            return 1 if ta < tb else -1
        return 1 if va > vb else -1
    return 0


def _parse_constraint(expr: str) -> Optional[tuple]:
    """解析约束串 '<= 3.0.14' → (op, version)；非法返回 None。"""
    m = re.match(r"^\s*(<=|>=|<|>|=|==)\s*([0-9][A-Za-z0-9.+\-_]*)\s*$", expr)
    if not m:
        return None
    op, ver = m.group(1), m.group(2)
    if op == "==":
        op = "="
    return op, ver


def _match_constraint(installed: str, expr: str) -> bool:
    """判断已安装版本是否命中约束（缺失版本信息按不命中处理）。"""
    parsed = _parse_constraint(expr)
    if not parsed:
        return False
    op, target = parsed
    try:
        r = _vcmp(installed, target)
    except Exception:
        return False
    if op == "<=":
        return r <= 0
    if op == ">=":
        return r >= 0
    if op == "<":
        return r < 0
    if op == ">":
        return r > 0
    return r == 0  # = / ==


def match_packages(packages: list, advisory: list) -> list:
    """把已装包清单与 advisory 比对，返回命中项列表。"""
    findings = []
    for name, version in packages:
        if not name or not version:
            continue
        for adv in advisory:
            if adv.get("name") != name.lower():
                continue
            if _match_constraint(version, adv.get("versions", "")):
                findings.append(
                    {
                        "pkg": name,
                        "version": version,
                        "cve": adv.get("cve", ""),
                        "severity": adv.get("severity", "unknown"),
                        "desc": adv.get("desc", ""),
                    }
                )
    return findings


# ---------------------------------------------------------------------------
# 镜像包清单获取
# ---------------------------------------------------------------------------
def _run_in_image(image_ref: str, script: str) -> str:
    """在目标镜像内运行一次性容器取输出（当前节点上下文）。"""
    from app import node_manager

    cmd = ["docker", "run", "--rm", "--entrypoint", "sh", image_ref, "-c", script]
    r = node_manager.host_cmd(cmd, capture_output=True, text=True, timeout=SCAN_TIMEOUT)
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()
        raise RuntimeError(f"容器执行失败（exit={r.returncode}）：{err[:300] or '镜像无可用 sh'}")
    out = (r.stdout or "")[: MAX_PKG_OUTPUT]
    if len(r.stdout or "") > MAX_PKG_OUTPUT:
        out += "\n…(包清单超出上限已截断)"
    return out


def parse_pkg_list(raw: str) -> list:
    """解析 'name\tversion' 行文本 → [(name, version)]。"""
    pkgs = []
    seen = set()
    for line in raw.splitlines():
        line = line.strip()
        if "\t" not in line:
            continue
        name, version = line.split("\t", 1)
        name = name.strip()
        version = version.strip()
        if not name or not version:
            continue
        key = (name.lower(), version)
        if key in seen:
            continue
        seen.add(key)
        pkgs.append((name, version))
    return pkgs


# ---------------------------------------------------------------------------
# 扫描执行与缓存
# ---------------------------------------------------------------------------
def scan_image(image_ref: str) -> dict:
    """扫描一个本地镜像（同步执行，调用方应放入线程池）。

    返回 {scan_id, image, total_pkgs, findings, cached}。
    """
    image_ref = _sanitize_image(image_ref)
    cache = _load_cache()
    if image_ref in cache:
        cached = dict(cache[image_ref])
        cached["cached"] = True
        return cached
    raw = _run_in_image(image_ref, _PKG_SCRIPT)
    packages = parse_pkg_list(raw)
    advisory = load_advisory()
    findings = match_packages(packages, advisory)
    result = {
        "scan_id": uuid.uuid4().hex[:10],
        "image": image_ref,
        "scanned_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "total_pkgs": len(packages),
        "findings": findings,
        "cached": False,
    }
    cache[image_ref] = result
    _save_cache(cache)
    return result


def _sanitize_image(image_ref: str) -> str:
    """清洗镜像引用，防 CLI 注入；非法抛 ValueError。"""
    ref = (image_ref or "").strip()
    if not _SAFE_IMG_RE.match(ref):
        raise ValueError("镜像引用非法")
    return ref


def _load_cache() -> dict:
    if not os.path.exists(SCAN_CACHE_FILE):
        return {}
    try:
        with open(SCAN_CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_cache(cache: dict) -> None:
    # 缓存有上限，超出丢弃最旧（按插入序）
    if len(cache) > 200:
        cache = dict(list(cache.items())[-200:])
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = SCAN_CACHE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
        os.replace(tmp, SCAN_CACHE_FILE)
    except OSError as e:
        logger.warning("写扫描缓存失败: %s", e)