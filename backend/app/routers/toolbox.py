# -*- coding: utf-8 -*-
"""
toolbox.py - Graw 工具箱路由（仅管理员）

提供一组常用运维小工具，统一通过 POST /api/toolbox/exec 调用：
  1. base64_encode / base64_decode       - Base64 编解码
  2. hash                                - MD5 / SHA1 / SHA256 哈希
  3. timestamp_to_datetime               - 时间戳 -> 可读时间
  4. datetime_to_timestamp               - 可读时间 -> 时间戳
  5. port_scan                           - 主机端口连通性扫描（TCP 三次握手探测）
  6. whois                               - Whois 域名查询（依赖系统 whois 命令）

安全设计（与 sitesopts / rewrite 等路由同思路）：
  - 工具名走白名单（ALLOWED_TOOLS），绝不反射执行任意字符串。
  - 端口仅允许 1-65535，单次扫描数量上限 MAX_PORTS，防止被滥用为扫描器。
  - 主机名 / IP / 域名均经严格正则与 ipaddress 校验，阻断 shell 注入与任意连接。
  - whois 走 subprocess 固定命令（list 参数，无 shell=True），命令缺失直接报错。
  - 端口扫描与外部命令在 asyncio.to_thread 中执行，不阻塞事件循环。
"""
import asyncio
import base64
import binascii
import hashlib
import ipaddress
import logging
import re
import shutil
import socket
import subprocess
import time
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger("graw.toolbox")

router = APIRouter()

# ---------------------------------------------------------------------------
# 常量与校验白名单
# ---------------------------------------------------------------------------
# 文本类工具输入最大长度（字符），防止超大 payload 打爆内存
MAX_TEXT_LEN = 65536

# 单次端口扫描上限 / 单端口连接超时（秒）。0.5s * 200 = 最坏约 100s，
# 前端限制同样为 200，后端兜底，避免被当作无节制扫描器。
MAX_PORTS = 200
SCAN_TIMEOUT = 0.5

# whois 外部命令超时（秒）与输出截断长度（字符）
WHOIS_TIMEOUT = 10
WHOIS_MAX_OUTPUT = 20000

# 主机名/域名白名单：字母/数字/中划线分段，长度上限贴合 RFC 约束。
# 覆盖 localhost、example.com、子域名；杜绝空格/分号等 shell 特殊字符。
_HOSTNAME_RE = re.compile(
    r"^[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?"
    r"(\.[A-Za-z0-9]([A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*$"
)

# 哈希算法白名单：仅允许内置的 MD5 / SHA1 / SHA256
_HASHES = {
    "md5": hashlib.md5,
    "sha1": hashlib.sha1,
    "sha256": hashlib.sha256,
}

# 日期时间解析格式（由宽松到严格），均支持常见输入
_DT_FORMATS = (
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%Y/%m/%d %H:%M:%S",
    "%Y/%m/%d %H:%M",
    "%Y/%m/%d",
)


# ---------------------------------------------------------------------------
# 输入校验辅助
# ---------------------------------------------------------------------------
def _validate_text(text, label="输入内容") -> str:
    """校验非空字符串输入并限制长度。"""
    if not isinstance(text, str) or not text.strip():
        raise HTTPException(status_code=400, detail=f"{label}不能为空")
    if len(text) > MAX_TEXT_LEN:
        raise HTTPException(status_code=400, detail=f"{label}过长（最多 {MAX_TEXT_LEN} 字符）")
    return text


def _validate_port(p) -> int:
    """校验端口为 1-65535 的整数。"""
    try:
        p = int(p)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="端口必须为 1-65535 的整数")
    if not 1 <= p <= 65535:
        raise HTTPException(status_code=400, detail="端口必须为 1-65535 的整数")
    return p


def _validate_host(host) -> str:
    """校验主机地址：仅允许 IP / 域名 / localhost，拒绝任意 shell 字符。"""
    host = (host or "").strip().lower()
    if not host or len(host) > 253:
        raise HTTPException(status_code=400, detail="主机地址不能为空或过长")
    if host == "localhost":
        return host
    # 合法 IPv4 / IPv6
    try:
        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    if _HOSTNAME_RE.match(host):
        return host
    raise HTTPException(status_code=400, detail="主机地址不合法（仅支持 IP / 域名 / localhost）")


def _validate_domain(domain) -> str:
    """校验域名：复用主机名校验并要求至少包含一个点（含 TLD）。"""
    domain = _validate_text(domain, label="域名").strip().lower()
    if not _HOSTNAME_RE.match(domain) or "." not in domain:
        raise HTTPException(status_code=400, detail="域名格式不合法（如 example.com）")
    return domain


# ---------------------------------------------------------------------------
# 工具实现
# ---------------------------------------------------------------------------
async def _base64_encode(args: dict) -> str:
    """Base64 编码：UTF-8 文本 -> Base64 字符串。"""
    text = _validate_text(args.get("text"))
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


async def _base64_decode(args: dict) -> str:
    """Base64 解码：Base64 字符串 -> 文本（非 UTF-8 内容以 repr 形式返回）。"""
    text = _validate_text(args.get("text"), label="Base64 内容")
    # 容忍换行/空格（部分来源会换行输出），去除空白后再严格校验
    compact = re.sub(r"\s+", "", text)
    try:
        raw = base64.b64decode(compact, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="Base64 解码失败：内容不是有效的 Base64")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        # 二进制内容无法按 UTF-8 解码时，返回 Python 字节 repr，避免 500
        return repr(raw)


async def _hash(args: dict) -> str:
    """计算 MD5 / SHA1 / SHA256 哈希。"""
    text = _validate_text(args.get("text"))
    algo = (args.get("algo") or "md5").strip().lower()
    if algo not in _HASHES:
        raise HTTPException(status_code=400, detail=f"不支持的哈希算法：{algo}")
    digest = _HASHES[algo](text.encode("utf-8")).hexdigest()
    return f"{algo.upper()}: {digest}"


async def _timestamp_to_datetime(args: dict) -> dict:
    """时间戳（秒）-> 可读时间（本地 + UTC）。"""
    raw = args.get("timestamp")
    try:
        ts = int(raw)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="时间戳必须为整数（秒）")
    # 合理范围：1970-01-01 ~ 9999-12-31（约 2.5e11 秒），拦截明显异常值
    if abs(ts) > 253402300799:
        raise HTTPException(status_code=400, detail="时间戳超出合理范围")
    local = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    utc = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    return {"timestamp": ts, "local": local, "utc": utc}


async def _datetime_to_timestamp(args: dict) -> dict:
    """可读时间 -> 时间戳（秒）。支持多种常见格式。"""
    raw = _validate_text(args.get("datetime"), label="日期时间").strip()
    for fmt in _DT_FORMATS:
        try:
            dt = datetime.strptime(raw, fmt)
        except ValueError:
            continue
        return {"datetime": raw, "timestamp": int(dt.timestamp())}
    raise HTTPException(
        status_code=400,
        detail="日期时间格式无法解析，请使用 YYYY-MM-DD HH:MM:SS（如 2026-08-20 12:00:00）",
    )


def _check_port(host: str, port: int) -> bool:
    """尝试 TCP 连接单端口，返回是否开放（可连接）。"""
    family = socket.AF_INET6 if ":" in host else socket.AF_INET
    with socket.socket(family, socket.SOCK_STREAM) as s:
        s.settimeout(SCAN_TIMEOUT)
        try:
            s.connect((host, port))
            return True
        except OSError:
            return False


async def _port_scan(args: dict) -> dict:
    """主机端口连通性扫描：返回开放端口列表与统计。"""
    host = _validate_host(args.get("host"))
    start = _validate_port(args.get("start_port", 1))
    end = _validate_port(args.get("end_port", start))
    if end < start:
        start, end = end, start
    count = end - start + 1
    if count > MAX_PORTS:
        raise HTTPException(
            status_code=400, detail=f"单次最多扫描 {MAX_PORTS} 个端口（当前 {count} 个）"
        )

    def _run() -> list:
        open_ports = []
        for p in range(start, end + 1):
            if _check_port(host, p):
                open_ports.append(p)
        return open_ports

    # 网络 IO 放线程池，避免阻塞事件循环
    open_ports = await asyncio.to_thread(_run)
    return {
        "host": host,
        "start_port": start,
        "end_port": end,
        "open_ports": open_ports,
        "closed_count": count - len(open_ports),
    }


async def _whois_lookup(args: dict) -> dict:
    """Whois 域名查询：调用系统 whois 命令（固定参数，无 shell）。"""
    domain = _validate_domain(args.get("domain"))
    whois_bin = shutil.which("whois")
    if not whois_bin:
        logger.info("工具箱 whois 查询被拒：系统未安装 whois 命令（%s）", domain)
        raise HTTPException(
            status_code=400, detail="whois 命令不可用（系统未安装 whois，无法执行查询）"
        )

    def _run() -> str:
        try:
            proc = subprocess.run(
                [whois_bin, domain],
                capture_output=True,
                text=True,
                timeout=WHOIS_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            return "（whois 查询超时，请稍后重试）"
        except OSError as e:
            return f"（whois 命令执行失败：{e}）"
        output = (proc.stdout or "").strip()
        if proc.stderr and proc.stderr.strip():
            output = (output + "\n" + proc.stderr.strip()).strip()
        return output[:WHOIS_MAX_OUTPUT] or "（whois 无返回内容）"

    output = await asyncio.to_thread(_run)
    return {"domain": domain, "result": output}


# 工具白名单：tool 名 -> 处理函数
TOOL_HANDLERS = {
    "base64_encode": _base64_encode,
    "base64_decode": _base64_decode,
    "hash": _hash,
    "timestamp_to_datetime": _timestamp_to_datetime,
    "datetime_to_timestamp": _datetime_to_timestamp,
    "port_scan": _port_scan,
    "whois": _whois_lookup,
}
ALLOWED_TOOLS = frozenset(TOOL_HANDLERS.keys())


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------
class ExecRequest(BaseModel):
    tool: str = Field(default="", max_length=32)
    args: dict = Field(default_factory=dict)


@router.post("/exec")
async def exec_tool(req: ExecRequest):
    """统一执行入口：{tool, args} -> 结果。

    tool 必须命中白名单；args 按各工具的校验规则严格检查。
    """
    tool = (req.tool or "").strip().lower()
    if tool not in ALLOWED_TOOLS:
        logger.warning("工具箱收到未授权的工具名: %s", tool)
        raise HTTPException(status_code=400, detail=f"不支持的工具：{tool}")

    handler = TOOL_HANDLERS[tool]
    start = time.time()
    try:
        result = await handler(req.args or {})
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("工具箱工具 %s 执行异常: %s", tool, e)
        raise HTTPException(status_code=500, detail=f"工具执行失败：{e}")
    logger.info("工具箱工具 %s 执行成功（耗时 %.2fs）", tool, time.time() - start)
    return {"ok": True, "tool": tool, "result": result}
