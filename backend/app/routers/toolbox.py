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
  7. ping                                - ICMP 连通性测试（参数列表执行，多节点自动透传）
  8. traceroute                          - 链路路由追踪（Linux 优先 mtr，回退 traceroute；Windows tracert）
  9. dns_lookup                          - DNS 解析查询（Linux 优先 dig，回退 nslookup）
  10. http_probe                         - HTTP(S) 探测（curl 输出状态码/耗时/TLS 信息）

另提供（排查与处置闭环）：
  - GET /api/toolbox/portview            - 监听端口排查看板：端口/协议/进程/容器归属映射
  - GET/POST/PUT/DELETE /api/toolbox/scripts - 常用脚本片段库（data/toolbox_scripts.json 原子写）

安全设计（与 sitesopts / rewrite 等路由同思路）：
  - 工具名走白名单（ALLOWED_TOOLS），绝不反射执行任意字符串。
  - 端口仅允许 1-65535，单次扫描数量上限 MAX_PORTS，防止被滥用为扫描器。
  - 主机名 / IP / 域名均经严格正则与 ipaddress 校验，阻断 shell 注入与任意连接。
  - whois / ping / curl / dig 全部走 subprocess 固定参数（list 参数，无 shell=True），
    命令缺失返回明确错误；http_probe 仅接受 http/https 且拒绝 userinfo。
  - 端口扫描与外部命令在 asyncio.to_thread 中执行，不阻塞事件循环。
"""
import asyncio
import base64
import binascii
import hashlib
import ipaddress
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

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

# 网络诊断外部命令统一超时（秒）与输出截断长度（字符）
CMD_TIMEOUT = 30
CMD_MAX_OUTPUT = 30000

# HTTP(S) 探测：URL 总长度上限与默认超时
URL_MAX_LEN = 2048
HTTP_PROBE_TIMEOUT = 15

# DNS 查询类型白名单
_DNS_TYPES = ("A", "AAAA", "CNAME", "MX", "TXT", "NS")

# 数据存储：脚本库（与 svcmonitor.json 同目录，原子写，权限随 data/ 收紧）
DATA_DIR = os.path.normpath(
    os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data"))
)
SCRIPTS_FILE = os.path.join(DATA_DIR, "toolbox_scripts.json")
# 脚本正文长度上限（字符）：允许较长的运维片段，同时防超大 payload
SCRIPT_MAX_LEN = 65536
# 脚本库并发写锁（JSON 读写为 read-whole/write-whole，需互斥）
_scripts_lock = threading.Lock()

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


# ---------------------------------------------------------------------------
# 网络诊断工具（P0：排查与处置闭环）
# 所有外部命令均以「参数列表」方式执行（无 shell=True），参数经白名单校验；
# 经 node_manager.host_cmd 统一入口执行，SSH/Agent 节点自动透传。
# ---------------------------------------------------------------------------
def _run_cmd(args: list, timeout: float = CMD_TIMEOUT) -> subprocess.CompletedProcess:
    """在当前管理主机执行一条 argv 命令（无 shell），返回 CompletedProcess。

    以 bytes 模式捕获输出再容错解码（Windows 本地编码可能是 GBK 等非 UTF-8，
    直接 text=True 会抛 UnicodeDecodeError）；超时返回 124 占位，命令缺失
    由 host_cmd 返回 returncode=127 占位，上层据此回退或友好报错。
    """
    from app import node_manager

    try:
        r = node_manager.host_cmd(args, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, 124, "", "命令执行超时")

    def _dec(v) -> str:
        # 兼容 bytes（本地 subprocess）与 str（SSH 节点远程结果）
        if isinstance(v, bytes):
            return v.decode("utf-8", "replace")
        return v or ""

    return subprocess.CompletedProcess(r.args, r.returncode,
                                       _dec(r.stdout), _dec(r.stderr))


async def _ping(args: dict) -> dict:
    """ICMP 连通性测试：ping -c/-n 固定参数，解析丢包率与平均 RTT。"""
    host = _validate_host(args.get("host"))
    try:
        count = min(max(int(args.get("count") or 4), 1), 10)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="count 必须为 1-10 的整数")
    # Windows: ping -n；Linux/macOS: ping -c
    cmd = (["ping", "-n", str(count), host] if os.name == "nt"
           else ["ping", "-c", str(count), host])

    def _run() -> dict:
        r = _run_cmd(cmd, timeout=CMD_TIMEOUT)
        output = (r.stdout or "") + (r.stderr or "")
        output = output[:CMD_MAX_OUTPUT]
        # 丢包率：Linux/macOS "x% packet loss"；Windows "Lost = x" / "x% 丢失"
        m = re.search(r"(\d{1,3})%?\s*(?:packet\s*loss|loss|丢失)", output, re.I)
        loss = int(m.group(1)) if m else None
        # 平均 RTT：Linux "min/avg/max"；Windows "Average/平均 = x ms"
        avg = None
        m = re.search(r"(?:rtt\s+)?min/avg/max/.*?([\d.]+)/([\d.]+)", output, re.I)
        if m:
            avg = round(float(m.group(2)), 2)
        else:
            m = re.search(r"(?:avg|average|平均)\s*[=:]\s*(\d+(?:\.\d+)?)", output, re.I)
            avg = round(float(m.group(1)), 2) if m else None
        return {
            "host": host,
            "count": count,
            "exit_code": r.returncode,
            "packet_loss_pct": loss,
            "avg_rtt_ms": avg,
            "output": output,
        }

    return await asyncio.to_thread(_run)


def _parse_traceroute_hops(output: str) -> list:
    """解析 traceroute 输出为逐跳列表（兼容 mtr 报告 / traceroute / tracert）。"""
    hops = []
    for line in output.splitlines():
        line = line.strip()
        # 标题行（HOST:/Loss%/Start: 等）与空行跳过
        if not line or "HOST:" in line or line.startswith("Start:") or "Loss" in line:
            if "|--" not in line:
                continue
        m = re.match(r"^(\d+)(?:\.\|--|\s+)(.*)$", line)
        if not m:
            continue
        hop_no = int(m.group(1))
        rest = m.group(2).strip()
        if not rest:
            continue
        tokens = rest.split()
        loss_pct = None
        lm = re.search(r"(\d{1,3})%", rest)
        if lm:
            loss_pct = int(lm.group(1))
        # 宿主提取（区分三种输出风格）：
        #   mtr 报告:  "1.|-- 10.0.0.1 0.0% 1 0.4 ..."  host 在第一列（带 Loss%）
        #   iputils:   " 1  10.0.0.1  0.396 ms ..."      host 在第一列
        #   tracert:   "  1    <1 ms    <1 ms   1 ms  192.168.1.1"  host 在末列
        host = tokens[0].strip("()")
        # 时间标记：纯数字 或 "<1"（Windows tracert 对亚毫秒显示 <1）
        is_time_tok = bool(re.match(r"^<?\d+(\.\d+)?$", tokens[0]))
        is_ms_tail = len(tokens) >= 2 and (
            tokens[1].lower().startswith("ms") or tokens[1].lower().startswith("毫秒")
        )
        if tokens[0] in ("*", "???", "***", "---", "!!!"):
            host = "（无响应）"
        elif "%" not in rest and is_time_tok and is_ms_tail:
            host = tokens[-1].strip("()")
            if host in ("*", "***"):
                host = "（无响应）"
        if "timed out" in rest.lower() or "超时" in rest or "无响应" in host:
            host = "（无响应）"
        hops.append({"hop": hop_no, "host": host, "loss_pct": loss_pct, "detail": rest[:300]})
    return hops[:30]


async def _traceroute(args: dict) -> dict:
    """链路路由追踪：Linux 优先 mtr -r 报告（结构化），回退 traceroute；Windows 用 tracert。"""
    host = _validate_host(args.get("host"))
    try:
        max_hops = min(max(int(args.get("max_hops") or 15), 1), 30)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="max_hops 必须为 1-30 的整数")

    def _run() -> dict:
        if os.name == "nt":
            r = _run_cmd(["tracert", "-d", "-h", str(max_hops), host], timeout=CMD_TIMEOUT)
            fmt = "tracert"
        else:
            # 优先 mtr 报告模式；命令缺失（rc=127）时回退 traceroute
            r = _run_cmd(["mtr", "-r", "-n", "-c", "1", "-m", str(max_hops), host], timeout=CMD_TIMEOUT)
            fmt = "mtr"
            if r.returncode == 127:
                r = _run_cmd(["traceroute", "-n", "-m", str(max_hops), host], timeout=CMD_TIMEOUT)
                fmt = "traceroute"
        output = (r.stdout or "") + (r.stderr or "")
        output = output[:CMD_MAX_OUTPUT]
        hops = _parse_traceroute_hops(output)
        return {"host": host, "format": fmt, "exit_code": r.returncode,
                "hops": hops, "output": output}

    return await asyncio.to_thread(_run)


async def _dns_lookup(args: dict) -> dict:
    """DNS 解析查询：Linux 优先 dig +short，回退 nslookup；Windows 用 nslookup。"""
    domain = _validate_domain(args.get("domain"))
    rtype = (args.get("type") or "A").strip().upper()
    if rtype not in _DNS_TYPES:
        raise HTTPException(status_code=400, detail=f"支持的 DNS 类型：{', '.join(_DNS_TYPES)}")

    def _run() -> list:
        if os.name != "nt":
            # dig +short：输出干净，每行一条记录
            r = _run_cmd(["dig", "+short", "-t", rtype, domain], timeout=15)
            if r.returncode != 127:
                records = []
                for line in (r.stdout or "").splitlines():
                    line = line.strip()
                    if line and not line.startswith((";;", ";")):
                        records.append(line[:500])
                return records
        # nslookup 输出信息多，过滤提示/头信息行
        r = _run_cmd(["nslookup", f"-type={rtype}", domain], timeout=15)
        skip_prefixes = ("server:", "address:", "addresses:", "non-authoritative", "name:",
                         "aliases:", "incomplete", "dns request timed out", "#53")
        records = []
        for line in ((r.stdout or "") + (r.stderr or "")).splitlines():
            line = line.strip(" \t\r\n>")
            low = line.lower()
            if not line or low.startswith(skip_prefixes) or low.startswith("默认服务器"):
                continue
            if ":" in low and low.split(":")[0].strip() in ("地址", "默认服务器"):
                continue
            records.append(line[:500])
        return records if records else ["（DNS 查询无返回记录）"]

    records = await asyncio.to_thread(_run)
    return {"domain": domain, "type": rtype, "records": records}


async def _http_probe(args: dict) -> dict:
    """HTTP(S) 探测：curl 输出状态码/各阶段耗时/TLS 校验结果，一次出全。"""
    url = (args.get("url") or "").strip()
    if len(url) > URL_MAX_LEN:
        raise HTTPException(status_code=400, detail="URL 过长")
    try:
        parsed = urlparse(url)
    except ValueError:
        raise HTTPException(status_code=400, detail="URL 无法解析")
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="仅支持 http/https 协议")
    if parsed.username or parsed.password:
        raise HTTPException(status_code=400, detail="URL 不允许携带账号密码信息")
    host = _validate_host(parsed.hostname)  # 复用主机名校验，阻断注入
    if parsed.hostname != host:
        raise HTTPException(status_code=400, detail="URL 主机名非法")
    try:
        timeout = min(max(int(args.get("timeout") or HTTP_PROBE_TIMEOUT), 3), 60)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="timeout 必须为 3-60 的整数")

    # -w 输出一行 JSON 指标；body 丢弃（os.devnull 跨平台）
    wfmt = ("{\"http_code\":%{http_code},\"time_total\":%{time_total},"
            "\"time_connect\":%{time_connect},\"time_namelookup\":%{time_namelookup},"
            "\"time_starttransfer\":%{time_starttransfer},\"size_download\":%{size_download},"
            "\"num_redirects\":%{num_redirects},\"ssl_verify_result\":%{ssl_verify_result}}")
    exe = "curl.exe" if os.name == "nt" else "curl"

    def _run() -> dict:
        r = _run_cmd(
            [exe, "-sS", "-L", "--max-time", str(timeout),
             "--connect-timeout", str(min(timeout, 10)),
             "-A", "Graw-Toolbox/1.0", "-o", os.devnull, "-w", wfmt, url],
            timeout=timeout + 5,
        )
        output = ((r.stdout or "") + (r.stderr or "")).strip()[:CMD_MAX_OUTPUT]
        metrics = None
        # 仅当退出码为 0 且 stdout 恰好是一行 JSON 时解析
        m = re.search(r"\{.*\}", output, re.S)
        if r.returncode == 0 and m:
            try:
                metrics = json.loads(m.group(0))
                output = r.stdout.replace(m.group(0), "").strip()
            except Exception:
                metrics = None
        return {"url": url, "exit_code": r.returncode,
                "metrics": metrics, "output": output or "（探测成功）"}

    return await asyncio.to_thread(_run)


# ---------------------------------------------------------------------------
# 端口排查看板（GET /api/toolbox/portview）
# ---------------------------------------------------------------------------
def _parse_local_address(local: str):
    """从监听地址提取端口与协议族。支持 '0.0.0.0:80'、'[::]:80'、'*:80'。"""
    if not local:
        return None, None
    if local.startswith("["):
        end = local.find("]:")
        if end == -1:
            return None, None
        port = local[end + 2:]
        return (port, "tcp6") if port.isdigit() else (None, None)
    parts = local.rsplit(":", 1)
    if len(parts) != 2 or not parts[1].isdigit():
        return None, None
    ip = parts[0]
    proto = "tcp6" if ":" in ip else "tcp"
    return parts[1], proto


def _parse_ss_proc_field(proc: str) -> list:
    """解析 ss/netstat 的进程列。

    ss:     users:(("nginx",pid=1234,fd=13))
    netstat: 1234/nginx
    返回 [(pid, name), ...]；无进程信息时返回 []。
    """
    ret = []
    if not proc:
        return ret
    for name, pid in re.findall(r'"([^"]+)"\s*,\s*pid=(\d+)', proc):
        ret.append((int(pid), name))
    if not ret:
        for pid, name in re.findall(r"^(\d+)/(\S+)", proc.strip()):
            ret.append((int(pid), name))
    return ret


def _parse_ss_output(output: str) -> list:
    """解析 `ss -tlnp`（Linux）监听端口条目。"""
    entries = []
    for line in output.splitlines():
        if "LISTEN" not in line:
            continue
        parts = line.split()
        # ss: LISTEN RecvQ SendQ Local Peer [Process...]
        # netstat(linux): tcp 0 0 Local Peer LISTEN [pid/proc]
        if len(parts) < 5 or parts[0].lower() in ("tcp", "tcp6", "udp", "udp6"):
            continue
        local = parts[3]
        proc = " ".join(parts[5:])
        port, proto = _parse_local_address(local)
        if not port:
            continue
        procs = _parse_ss_proc_field(proc) or [(None, "")]
        for pid, pname in procs:
            entries.append({"port": port, "proto": proto,
                            "pid": pid, "process": pname})
    return entries


def _parse_netstat_linux(output: str) -> list:
    """解析 Linux `netstat -tlnp` 回退分支。"""
    entries = []
    for line in output.splitlines():
        m = re.match(r"^\s*(tcp6?|udp6?)\s+\S+\s+\S+\s+(\S+)\s+\S+\s+LISTEN\s*(\S*)\s*$", line)
        if not m:
            continue
        local = m.group(2)
        port, proto = _parse_local_address(local)
        if not port:
            continue
        proc = m.group(3)
        procs = _parse_ss_proc_field(proc) or [(None, "")]
        for pid, pname in procs:
            entries.append({"port": port, "proto": proto,
                            "pid": pid, "process": pname})
    return entries


def _parse_netstat_windows(output: str) -> list:
    """解析 Windows `netstat -ano`：本地地址含 '0.0.0.0:135' 与 '[::]:445'。"""
    entries = []
    for line in output.splitlines():
        m = re.match(r"^\s*TCP\s+(\S+):(\d+)\s+\S+\s+LISTENING\s+(\d+)\s*$", line)
        if not m:
            continue
        local = m.group(1)
        proto = "tcp6" if ":" in local else "tcp"
        entries.append({"port": m.group(2), "proto": proto,
                        "pid": int(m.group(3)), "process": ""})
    return entries


# 容器 PID -> 容器 ID 短映射的内存缓存（15 秒），避免频繁 inspect
_pid_cache = {"ts": 0.0, "map": {}}


def _container_pid_map() -> dict:
    """运行中容器 {State.Pid: id[:12]} 映射，用于端口归属判断。

    一次 docker inspect 批量读取（避免逐容器调用）；缓存 15 秒。
    """
    now = time.time()
    if now - _pid_cache["ts"] < 15 and _pid_cache["map"] is not None:
        return _pid_cache["map"]
    mapping = {}
    try:
        r = _run_cmd(["docker", "ps", "--no-trunc", "-q"], timeout=20)
        ids = [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip()]
        if r.returncode == 0 and ids:
            ids = ids[:100]  # 容器数兜底，避免超长参数
            r2 = _run_cmd(["docker", "inspect", "--format", "{{.State.Pid}} {{.Id}}", *ids],
                          timeout=25)
            for ln in (r2.stdout or "").splitlines():
                parts = ln.split()
                if len(parts) >= 2 and parts[0].isdigit():
                    mapping[int(parts[0])] = parts[1][:12]
    except Exception as e:
        logger.warning("端口视图读取容器 PID 映射失败: %s", e)
    _pid_cache["ts"] = now
    _pid_cache["map"] = mapping
    return mapping


def _pid_name_map(pids: list) -> dict:
    """用 psutil 把 PID 回填为进程名（Windows netstat 无进程列时兜底）。"""
    names = {}
    try:
        import psutil
    except Exception:
        return names
    for pid in set(pids):
        if not pid:
            continue
        try:
            names[pid] = psutil.Process(pid).name() or ""
        except (psutil.NoSuchProcess, psutil.AccessDenied, ValueError):
            names[pid] = ""
    return names


async def port_view(filter: str = ""):
    """监听端口排查看板：端口/协议/进程/归属容器，支持关键字过滤。"""
    keyword = (filter or "").strip()[:64]

    def _run() -> list:
        if os.name == "nt":
            r = _run_cmd(["netstat", "-ano"], timeout=20)
            entries = _parse_netstat_windows((r.stdout or "") + (r.stderr or ""))
        else:
            r = _run_cmd(["ss", "-tlnp"], timeout=20)
            if r.returncode == 127:
                r = _run_cmd(["netstat", "-tlnp"], timeout=20)
                entries = _parse_netstat_linux((r.stdout or "") + (r.stderr or ""))
            else:
                entries = _parse_ss_output((r.stdout or "") + (r.stderr or ""))
        # 容器归属映射（一次性批量获取）
        pid_map = _container_pid_map()
        # Windows / 缺进程列的条目用 psutil 回填进程名
        missing_proc = [e["pid"] for e in entries if e.get("pid") and not e.get("process")]
        name_map = _pid_name_map(missing_proc) if missing_proc else {}
        for e in entries:
            e["container_id"] = pid_map.get(e.get("pid"), "")
            if not e.get("process"):
                e["process"] = name_map.get(e.get("pid"), "")
        # 关键字过滤：端口/进程名/容器ID 任一种命中
        if keyword:
            kw = keyword.lower()
            entries = [e for e in entries if kw in (
                str(e["port"]) + " " + (e["process"] or "") + " " + (e["container_id"] or "")
            ).lower()]
        entries.sort(key=lambda e: (int(e["port"]), e.get("proto") or ""))
        return entries

    items = await asyncio.to_thread(_run)
    logger.info("端口排查看板查询完成（%d 条监听项）", len(items))
    return {"items": items}


# ---------------------------------------------------------------------------
# 脚本片段库（data/toolbox_scripts.json）
# ---------------------------------------------------------------------------
# 危险命令模式：命中后前端展示二次确认（仅提示，不阻止管理员保存）
_DANGER_PATTERNS = (
    re.compile(r"\brm\s+-[a-z]*r[a-z]*f[a-z]*(\s|/|$)", re.I),                 # rm -rf /
    re.compile(r"\brm\s+-[a-z]*r[a-z]*f[a-z]*\s+[^|;]*/\*", re.I),             # rm -rf /*
    re.compile(r"\bmkfs(?:\.[a-z0-9]+)?\b", re.I),                             # mkfs 格式化
    re.compile(r"\bdd\b", re.I),                                               # dd 磁盘直写
    re.compile(r"\bchmod\s+-R\s+777\s+/\s*$", re.I),                           # chmod -R 777 /
    re.compile(r"\b(?:shutdown|reboot|poweroff|init\s+0|init\s+6)\b", re.I),   # 关/重启
    re.compile(r"\b:\(\)\s*\{", re.I),                                         # fork 炸弹
    re.compile(r">>\s*/etc/(?:passwd|shadow)", re.I),                          # 覆写账号文件
)


def _dangerous_scan(content: str) -> list:
    """扫描脚本内容命中的危险模式，返回命中正则字符串列表。"""
    return [p.pattern for p in _DANGER_PATTERNS if p.search(content or "")]


def _scripts_default() -> dict:
    return {"scripts": []}


def _load_scripts() -> dict:
    if not os.path.exists(SCRIPTS_FILE):
        return _scripts_default()
    try:
        with open(SCRIPTS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning("读取 toolbox_scripts.json 失败，按默认处理: %s", e)
        return _scripts_default()
    if not isinstance(data, dict):
        return _scripts_default()
    data.setdefault("scripts", [])
    return data


def _save_scripts(data: dict) -> None:
    """原子写脚本库：临时文件 + os.replace，崩溃不产生半截文件。"""
    with _scripts_lock:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = SCRIPTS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, SCRIPTS_FILE)


def _find_script(scripts: list, script_id: str) -> dict:
    script = next((s for s in scripts if s.get("id") == script_id), None)
    if not script:
        raise HTTPException(status_code=404, detail="脚本不存在")
    return script


def _validate_script(name: str, content: str, target: str, tags: list) -> dict:
    """校验脚本字段并计算危险标记，返回规范化字段。"""
    name = (name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="脚本名称不能为空")
    if len(name) > 64:
        raise HTTPException(status_code=400, detail="脚本名称过长（最多 64 字符）")
    content = content or ""
    if len(content) > SCRIPT_MAX_LEN:
        raise HTTPException(status_code=400, detail=f"脚本内容过长（最多 {SCRIPT_MAX_LEN} 字符）")
    target = (target or "host").strip().lower()
    if target not in ("host", "node", "container"):
        raise HTTPException(status_code=400, detail="适用目标必须是 host/node/container")
    clean_tags = []
    for t in (tags or [])[:8]:
        t = str(t).strip()
        if t and len(t) <= 32:
            clean_tags.append(t)
    return {"name": name, "content": content, "target": target, "tags": clean_tags}


# 工具白名单：tool 名 -> 处理函数
TOOL_HANDLERS = {
    "base64_encode": _base64_encode,
    "base64_decode": _base64_decode,
    "hash": _hash,
    "timestamp_to_datetime": _timestamp_to_datetime,
    "datetime_to_timestamp": _datetime_to_timestamp,
    "port_scan": _port_scan,
    "whois": _whois_lookup,
    "ping": _ping,
    "traceroute": _traceroute,
    "dns_lookup": _dns_lookup,
    "http_probe": _http_probe,
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
        logger.exception("工具箱工具（类型=%s）执行异常: %s", type(tool).__name__, type(e).__name__)
        raise HTTPException(status_code=500, detail=f"工具执行失败：{e}")
    logger.info("工具箱工具 %s 执行成功（耗时 %.2fs）", repr(tool), time.time() - start)
    return {"ok": True, "tool": tool, "result": result}


# 脚本库请求模型
class ScriptRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    desc: str = Field("", max_length=500)
    target: str = Field("host", max_length=32)
    content: str = ""
    tags: list = Field(default_factory=list)


class ScriptUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=64)
    desc: Optional[str] = Field(None, max_length=500)
    target: Optional[str] = Field(None, max_length=32)
    content: Optional[str] = None
    tags: Optional[list] = None


@router.get("/portview")
async def port_view_endpoint(filter: str = ""):
    """监听端口排查看板：端口/协议/进程/归属容器，支持关键字过滤。"""
    return await port_view(filter)


@router.get("/scripts")
async def list_scripts():
    """返回脚本片段库列表（含危险标记，供前端提示）。"""
    data = _load_scripts()
    return {"scripts": data.get("scripts", [])}


@router.post("/scripts")
async def create_script(req: ScriptRequest):
    """新增脚本片段。"""
    fields = _validate_script(req.name, req.content, req.target, req.tags)
    now = datetime.now().isoformat()
    dangerous = _dangerous_scan(fields["content"])
    script = {
        "id": "scr_" + uuid.uuid4().hex[:10],
        "name": fields["name"],
        "desc": (req.desc or "").strip()[:500],
        "target": fields["target"],
        "content": fields["content"],
        "tags": fields["tags"],
        "dangerous": bool(dangerous),
        "danger_hits": dangerous,
        "created_at": now,
        "updated_at": now,
    }
    data = _load_scripts()
    data.setdefault("scripts", []).append(script)
    _save_scripts(data)
    logger.info("新增脚本片段：%s", repr(script["name"]))
    return script


@router.put("/scripts/{script_id}")
async def update_script(script_id: str, req: ScriptUpdateRequest):
    """更新脚本片段（局部更新）。"""
    data = _load_scripts()
    script = _find_script(data.get("scripts", []), script_id)
    if req.name is not None:
        script["name"] = (req.name or "").strip()
    if req.desc is not None:
        script["desc"] = (req.desc or "").strip()[:500]
    if req.content is not None:
        script["content"] = req.content or ""
    if req.target is not None:
        script["target"] = (req.target or "host").strip().lower()
    if req.tags is not None:
        script["tags"] = [str(t).strip() for t in req.tags[:8] if str(t).strip()]
    # 名称/内容/目标关键字段重新过校验，防止绕过
    fields = _validate_script(script.get("name", ""), script.get("content", ""),
                              script.get("target", "host"), script.get("tags", []))
    script.update({**fields})
    script["updated_at"] = datetime.now().isoformat()
    dangerous = _dangerous_scan(script.get("content", ""))
    script["dangerous"] = bool(dangerous)
    script["danger_hits"] = dangerous
    _save_scripts(data)
    logger.info("更新脚本片段：%s", repr(script.get("name")))
    return script


@router.delete("/scripts/{script_id}")
async def delete_script(script_id: str):
    """删除脚本片段。"""
    data = _load_scripts()
    before = len(data.get("scripts", []))
    data["scripts"] = [s for s in data.get("scripts", []) if s.get("id") != script_id]
    if len(data["scripts"]) == before:
        raise HTTPException(status_code=404, detail="脚本不存在")
    _save_scripts(data)
    logger.info("删除脚本片段：%s", script_id)
    return {"ok": True}
