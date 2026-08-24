# -*- coding: utf-8 -*-
"""
test_security_regression.py - 第八轮安全审计回归测试

覆盖已修复的漏洞，防止后续改动重新引入：
  1. SQL 危险原语过滤器双引号标识符绕过（High，databases.py _normalize_sql）
  2. Redis 命令反射黑名单缺口（Medium，databases.py _is_forbidden_redis）
  3. remote_cap 前缀笔误漏拦 sitesopts（Medium，remote_cap.py LOCAL_PREFIX）
  4. WebDAV SSRF 重定向绕过（High，netstorage.py / backup.py allow_redirects=False）
"""
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

BACKEND = os.path.normpath(os.path.join(os.path.dirname(__file__)))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from app.routers.databases import (  # noqa: E402
    _normalize_sql,
    _reject_dangerous_sql,
    _is_forbidden_redis,
)
from app.remote_cap import is_local_path  # noqa: E402
from app.ssrf_guard import assert_safe_host, assert_safe_http_url  # noqa: E402


# ---------------------------------------------------------------------------
# 1. SQL 过滤器：双引号标识符不得绕过危险原语拦截
# ---------------------------------------------------------------------------
class TestSqlFilterBypass:
    @pytest.mark.parametrize(
        "payload",
        [
            'SELECT "pg_read_file"(\'/etc/passwd\')',
            'SELECT "pg_ls_dir"(\'/\')',
            'SELECT "pg_write_file"(\'/tmp/pwn\',\'x\')',
            'SELECT "pg_read_binary_file"(\'/etc/shadow\')',
            'SELECT "Pg_Read_File"(\'/etc/passwd\')',
            'SELECT "LOAD_FILE"(\'/etc/passwd\')',
            "SELECT `pg_read_file`('/etc/passwd')",
            'SELECT "pg_stat_file"(\'/etc/passwd\')',
            'SELECT "pg_logdir_ls"()',
            'SELECT "pg_ls_waldir"()',
        ],
    )
    def test_quoted_identifier_dangerous_primitives_rejected(self, payload):
        """被双引号/反引号标识符包裹的危险函数必须被拦截（防绕过回归）。"""
        assert _reject_dangerous_sql(payload) is True, f"应拦截: {payload}"

    @pytest.mark.parametrize(
        "payload",
        [
            "SELECT pg_read_file('/etc/passwd')",
            "SELECT pg_ls_dir('/')",
            "SELECT LOAD_FILE('/etc/passwd')",
            "SELECT * INTO OUTFILE '/tmp/x' FROM t",
        ],
    )
    def test_plain_dangerous_primitives_rejected(self, payload):
        """裸写危险函数基线拦截。"""
        assert _reject_dangerous_sql(payload) is True

    @pytest.mark.parametrize(
        "payload",
        [
            "SELECT 1",
            "SELECT 'pg_read_file'",          # 字符串字面量不是函数调用
            "SELECT name FROM users WHERE id = 1",
            "SELECT name, 'it''s fine' FROM t",  # 转义引号不破坏解析
        ],
    )
    def test_benign_sql_allowed(self, payload):
        """正常查询不应被误拦。"""
        assert _reject_dangerous_sql(payload) is False, f"不应拦截: {payload}"

    def test_normalize_keeps_identifier_content(self):
        """双引号/反引号标识符内容应保留（否则正则无法命中）。"""
        norm = _normalize_sql('SELECT "pg_read_file"(\'/etc/passwd\')')
        assert "pg_read_file" in norm
        # 单引号字符串内容仍置空
        norm2 = _normalize_sql("SELECT 'secret-keyword'")
        assert "secret-keyword" not in norm2


# ---------------------------------------------------------------------------
# 2. Redis 反射黑名单：高危命令必须被 _is_forbidden_redis 拦截
# ---------------------------------------------------------------------------
class TestRedisBlacklist:
    @pytest.mark.parametrize(
        "method",
        [
            "flushall", "flushdb",                # 清库
            "debug_segfault", "debug_object",     # debug_ 家族 (崩溃/调试)
            "config_get", "config_resetstat",     # config_ 家族 (配置泄露)
            "function_load", "fcall", "fcall_ro",  # Lua 函数执行
            "failover", "replconf",               # 主从破坏
            "monitor", "psync", "sync",           # 数据外带
            "memory_purge", "client_pause",       # DoS
            "module_load",                        # 原生模块
            "acl_whoami", "acl_setuser",          # ACL
            "shutdown", "config_set", "eval", "execute_command", "save",
        ],
    )
    def test_dangerous_redis_methods_forbidden(self, method):
        assert _is_forbidden_redis(method) is True, f"应拦截 redis 方法: {method}"

    @pytest.mark.parametrize(
        "method",
        [
            "get", "set", "keys", "hgetall", "lrange", "info", "ttl",
            "expire", "del", "incr", "zrange", "sismember", "ping", "dbsize",
        ],
    )
    def test_benign_redis_methods_allowed(self, method):
        assert _is_forbidden_redis(method) is False, f"不应拦截 redis 方法: {method}"


# ---------------------------------------------------------------------------
# 3. remote_cap：sitesopts 必须属于 local 类（远端节点下禁用）
# ---------------------------------------------------------------------------
class TestRemoteCapPrefix:
    def test_sitesopts_is_local(self):
        assert is_local_path("/api/sitesopts") is True
        assert is_local_path("/api/sitesopts/config") is True
        assert is_local_path("/api/sitesopts/site1/gzip") is True

    def test_sites_is_local(self):
        assert is_local_path("/api/sites/list") is True

    def test_host_class_not_local(self):
        assert is_local_path("/api/system/overview") is False
        assert is_local_path("/api/process/list") is False


# ---------------------------------------------------------------------------
# 5. FTP/SMB/S3 裸主机 SSRF 防护（assert_safe_host）
# ---------------------------------------------------------------------------
class TestBareHostSsrp:
    def test_ip_literal_blocked(self):
        # 云元数据 / 回环 / 链路本地 等 IP 字面量必须被拒绝（无需 DNS）
        for bad in ("169.254.169.254", "127.0.0.1", "127.8.8.8",
                    "0.0.0.0", "169.254.0.1", "fe80::1"):
            with pytest.raises(ValueError):
                assert_safe_host(bad, allow_private=True), bad

    def test_private_ip_allowed_for_storage(self):
        # 内网存储场景允许 RFC1918/ULA，但受保护地址始终拒绝
        for ok in ("10.0.0.1", "192.168.1.5", "172.16.3.4"):
            assert_safe_host(ok, allow_private=True)

    def test_hostname_resolving_to_protected_rejected(self):
        # localhost 解析到回环 → 拒绝（strict 或非 strict 都应拒绝）
        with pytest.raises(ValueError):
            assert_safe_host("localhost", allow_private=True)
        with pytest.raises(ValueError):
            assert_safe_http_url("http://localhost/", allow_private=True)

    def test_port_and_protocol_prefix_stripped(self):
        # "host:port" / "https://host" 形式也应正确剥离后校验
        assert_safe_host("10.0.0.1:445", allow_private=True)
        with pytest.raises(ValueError):
            assert_safe_host("http://169.254.169.254:9000/", allow_private=True)


# ---------------------------------------------------------------------------
# 4. WebDAV SSRF 重定向防护：生产代码的 WebDAVAdapter 不得跟随 3xx
# ---------------------------------------------------------------------------
class _RedirectServer(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(302)
        self.send_header("Location", "http://127.0.0.1:1/secret")  # 指向受保护地址
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *a):
        pass


class TestWebdavRedirectSsrp:
    def test_webdav_adapter_rejects_redirect(self):
        import socket

        from fastapi import HTTPException
        from app.routers.netstorage import WebDAVAdapter

        server = ThreadingHTTPServer(("0.0.0.0", 0), _RedirectServer)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            conn = {
                "id": "t", "type": "webdav", "name": "t",
                # 用非回环地址构造 base 以通过初始校验，模拟"公网攻击者服务器"
                "base": f"http://{socket.gethostbyname(socket.gethostname())}:{port}/",
                "username": "", "password": "",
            }
            adapter = WebDAVAdapter(conn)
            with pytest.raises(HTTPException) as exc:
                adapter.read("x")
            assert "重定向" in exc.value.detail or "SSRF" in exc.value.detail
        finally:
            server.shutdown()
            server.server_close()
