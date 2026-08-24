# -*- coding: utf-8 -*-
"""
ssrf_guard.py - 统一的出站 HTTP URL SSRF 防护

背景
----
Graw 中多处功能接受「用户提供的 URL」并发起到该地址的服务器出站请求：
  - uptime   站点可用性监控（被动+手动探测）
  - backup   远程备份目标（WebDAV）
  - netstorage 网络储存（WebDAV / SMB / 对象存储）
  - appstore 远程索引（已自带 _assert_public_http_url）

早期这些模块只校验了 URL scheme（http/https），未对目标主机做网络位置
校验，也未拦截重定向绕过，导致攻击者可令面板向回环 / 链路本地 / 私网 /
保留地址发起请求：
  - 探测内网服务、端口扫描（uptime 返回状态码/延迟）；
  - 经 http://169.254.169.254/ 读取云厂商实例元数据（IAM 临时凭据）；
  - 经 WebDAV read 回显任意内网 HTTP 响应体（数据外带）。

本模块提供与 appstore._assert_public_http_url 同基线的统一防护，供上述
模块复用，避免「防护不一致」再次引入漏洞。

设计要点
--------
  1. 仅允许 http / https scheme（拒绝 file:// / ftp:// / gopher:// 等）。
  2. 解析主机名得到的「全部」 IP 逐一判定，任一命中受保护区间即拒绝
     （防御 DNS 返回多个记录中夹带内网 IP，亦缓解部分 DNS rebinding 场景）。
  3. 始终拒绝：回环 / 链路本地（含云 metadata 169.254.0.0/16、fe80::/10）/
     组播 / 保留 / 未指定（0.0.0.0）地址——这些目标即便对「内网存储」
     场景也毫无正当性。
  4. allow_private=False（默认）时额外拒绝私网地址（RFC1918 / ULA），
     与 appstore 行为一致；内网存储（WebDAV/SMB）可显式 allow_private=True，
     但仍受第 3 条的始终拒绝集合约束。
  5. 调用方应在「每次实际发起请求前」重新校验（而非仅创建时），以缓解
     DNS rebinding 的 TOCTOU 窗口；重定向应禁用或逐跳复用本校验。
"""
from urllib.parse import urlparse
import re
import socket
import ipaddress


def _blocked_reason(ip: "ipaddress.IPv4Address | ipaddress.IPv6Address") -> "str | None":
    """若 IP 命中「始终拒绝」集合则返回原因字符串，否则 None。"""
    if ip.is_loopback:
        return "loopback"
    if ip.is_link_local:
        return "link_local"
    if ip.is_multicast:
        return "multicast"
    if ip.is_reserved:
        return "reserved"
    if ip.is_unspecified:
        return "unspecified"
    return None


def assert_safe_http_url(url: str, *, allow_private: bool = False) -> None:
    """校验用户提供的出站 URL 是否可安全请求。

    通过则不返回（None），否则抛 ValueError（调用方转为 HTTP 400）。

    :param url: 待校验 URL
    :param allow_private: True 时允许私网地址（RFC1918/ULA），仅用于
        明确的内网存储场景；回环/链路本地/保留地址始终拒绝。
    """
    if not url or not isinstance(url, str):
        raise ValueError("URL 不能为空")
    scheme = (urlparse(url).scheme or "").lower()
    if scheme not in ("http", "https"):
        raise ValueError("仅支持 http/https 地址（SSRF 防护）")
    hostname = urlparse(url).hostname or ""
    if not hostname:
        raise ValueError("URL 缺少主机名（SSRF 防护）")
    _assert_resolved_host(hostname, allow_private=allow_private, strict_dns=True)


def assert_safe_host(host: str, *, allow_private: bool = True, allow_loopback: bool = False) -> None:
    """校验裸主机名 / IP（供 FTP/SMB/S3 等非 HTTP 协议的出站连接使用）。

    与 assert_safe_http_url 同基线：解析主机名得到的全部 IP 逐一判定，任一
    命中受保护区间（回环 / 链路本地 / 组播 / 保留 / 未指定，含云 metadata
    169.254.169.254）即拒绝；allow_private 控制是否放行 RFC1918/ULA 私网
    地址（网络存储场景默认允许内网，但受保护地址始终拒绝）。

    安全修复（第八轮审计，Medium）：此前 FTP/FTPS/SMB/S3 连接仅做字符
    白名单校验，169.254.169.254 等云元数据地址可直接连接，构成管理员级
    SSRF（内网探测 / 元数据凭据窃取）。

    安全修复（本轮审计）：新增 allow_loopback 参数——用于「本机回环服务
    属合法场景」的连接（如通知中心的 SMTP 本机 postfix）。默认 False 保持
    原有语义（回环始终拒绝）；置 True 时放行回环地址，但链路本地（含云
    元数据 169.254.169.254）/ 组播 / 保留 / 未指定地址仍始终拒绝。
    """
    if not host or not isinstance(host, str):
        raise ValueError("主机不能为空（SSRF 防护）")
    hostname = (host or "").strip()
    # 剥离可能的端口/协议前缀（如 "host:port" / "https://host" / "[::1]:22"）
    if "://" in hostname:
        hostname = urlparse(hostname).hostname or hostname
    _ipv6_bracket = re.match(r"^\[([0-9A-Fa-f:.]+)\](?::\d+)?$", hostname)
    if _ipv6_bracket:
        hostname = _ipv6_bracket.group(1)
    elif hostname.startswith("[") and hostname.endswith("]"):
        hostname = hostname.strip("[]")
    # IPv6 字面量（如 fe80::1）整体处理，不做冒号拆端口
    try:
        ipaddress.ip_address(hostname)
        is_literal = True
    except ValueError:
        is_literal = False
    if not is_literal and ":" in hostname:
        hostname = hostname.split(":", 1)[0].strip()
    if not hostname:
        raise ValueError("主机缺少有效主机名（SSRF 防护）")
    # 注意：strict_dns=False —— 主机名当前解析失败（如内网主机名仅能在服务器
    # 侧网络解析 / 离线环境）时放行交由连接阶段兜底：解析不了就连接不上，
    # 无 SSRF 风险；IP 字面量与「能解析到受保护地址」的主机名仍在此处拒绝。
    _assert_resolved_host(
        hostname,
        allow_private=allow_private,
        strict_dns=False,
        allow_loopback=allow_loopback,
    )


def _assert_resolved_host(hostname: str, *, allow_private: bool, strict_dns: bool = False, allow_loopback: bool = False) -> None:
    """解析主机名并对全部解析 IP 执行受保护地址判定（HTTP/裸主机共用）。

    :param strict_dns: True（HTTP URL 场景）时，主机名无法解析即拒绝
        （fail-closed）；False（FTP/SMB/S3 裸主机场景）时解析失败放行，
        交由实际连接阶段兜底。
    :param allow_loopback: True 时放行回环地址（如本机 SMTP postfix）；
        链路本地（含云 metadata）/ 组播 / 保留 / 未指定仍始终拒绝。
    """
    # IP 字面量快速路径：直接判定，无需 DNS（也避免离线环境解析失败）
    try:
        literal_ip = ipaddress.ip_address(hostname)
    except ValueError:
        literal_ip = None
    if literal_ip is not None:
        _assert_ip(literal_ip, hostname, allow_private, allow_loopback)
        return
    try:
        infos = socket.getaddrinfo(hostname, None)
    except OSError:
        if strict_dns:
            raise ValueError(f"无法解析主机名: {hostname}（SSRF 防护）")
        return  # 非严格模式：解析失败放行，连接阶段自然失败（无 SSRF 风险）
    if not infos:
        if strict_dns:
            raise ValueError(f"主机名无可解析地址: {hostname}（SSRF 防护）")
        return
    for info in infos:
        addr = info[4][0] if isinstance(info[4], (tuple, list)) else info[4]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError as exc:
            raise ValueError(f"非法 IP 地址: {addr}（SSRF 防护）") from exc
        _assert_ip(ip, hostname, allow_private, allow_loopback)


def _assert_ip(ip, hostname: str, allow_private: bool, allow_loopback: bool = False) -> None:
    """对单个 IP 执行受保护区间判定（HTTP/裸主机共用）。"""
    if allow_loopback and ip.is_loopback:
        return  # 显式放行回环（本机服务场景，如 SMTP postfix）
    reason = _blocked_reason(ip)
    if reason is not None:
        raise ValueError(
            f"目标主机 {hostname} 解析为受保护地址（{reason}: {ip}），已拒绝（SSRF 防护）"
        )
    if not allow_private and ip.is_private:
        raise ValueError(
            f"目标主机 {hostname} 不是公网地址（{ip}），已拒绝（SSRF 防护）"
        )
