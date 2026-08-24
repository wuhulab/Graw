# -*- coding: utf-8 -*-
"""
test_security_regression.py - 第八轮安全审计回归测试

覆盖已修复的漏洞，防止后续改动重新引入：
  1. SQL 危险原语过滤器双引号标识符绕过（High，databases.py _normalize_sql）
  2. Redis 命令反射黑名单缺口（Medium，databases.py _is_forbidden_redis）
  3. remote_cap 前缀笔误漏拦 sitesopts（Medium，remote_cap.py LOCAL_PREFIX）
  4. WebDAV SSRF 重定向绕过（High，netstorage.py / backup.py allow_redirects=False）

第十轮安全审计回归测试（本轮新增）：
  5. MySQL 可执行注释 /*!...*/ / /*M!...*/ 绕过（High，databases.py _normalize_sql）
  6. PostgreSQL `#` 操作符被误当注释剥离绕过（High，databases.py _normalize_sql）
  7. Redis rdb_save/rdb_bgsave 反射绕过（High，databases.py _is_forbidden_redis）
  8. logs.py Windows UNC 网络路径绕过 data 目录防护（Medium，logs.py _safe_log_path）
  9. webstats.py log_path 任意文件读取（Medium，webstats.py _reject_forbidden_log_path）
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


# ---------------------------------------------------------------------------
# 5. MySQL 可执行注释 /*!...*/ 不得绕过 SQL 危险原语过滤（第十轮审计，High）
# ---------------------------------------------------------------------------
class TestSqlExecutableCommentBypass:
    @pytest.mark.parametrize(
        "payload",
        [
            "SELECT /*!50000 LOAD_FILE('/etc/passwd') */",
            "SELECT 1 /*! INTO OUTFILE '/tmp/pwned' */",
            "SELECT /*!50000 LOAD_FILE(CONCAT('/etc/', 'passwd')) */",
            "SELECT /*!99999 LOAD_FILE('/etc/shadow') */",
            "SELECT /*M!100000 LOAD_FILE('/etc/passwd') */",   # MariaDB 变体
            "SELECT /*!50000 pg_read_file('/etc/passwd') */",  # 通用原语变体
            "SELECT /*!50000 INTO DUMPFILE '/tmp/x' */ FROM t",
        ],
    )
    def test_executable_comment_rejected(self, payload):
        """MySQL/MariaDB 可执行注释内容会被数据库实际执行，必须整体拒绝。"""
        assert _reject_dangerous_sql(payload) is True, f"应拦截: {payload}"

    def test_normal_comment_still_allowed(self):
        """普通 /*...*/ 注释不改变语句语义，正常查询仍应放行。"""
        assert _reject_dangerous_sql("SELECT id, name FROM users /* 普通注释 */") is False


# ---------------------------------------------------------------------------
# 6. PostgreSQL `#` 操作符不得被误当注释剥离（第十轮审计，High）
# ---------------------------------------------------------------------------
class TestSqlHashOperatorNoStripping:
    @pytest.mark.parametrize(
        "payload",
        [
            # PG 中 `#` 是位异或操作符，`#>`/`#>>` 是 jsonb 操作符，不是注释。
            # 剥离后危险原语会消失导致绕过，现在必须保留并拦截。
            "SELECT 1 # length(pg_read_file('/etc/passwd'))",
            "SELECT CASE WHEN (1 # length(pg_read_file('/etc/passwd'))) > 0 THEN 1 ELSE 0 END",
            "SELECT data #> '{a}' FROM t",
            "SELECT 1 # 1",
        ],
    )
    def test_hash_not_stripped(self, payload):
        cleaned = _normalize_sql(payload)
        # `#` 必须保留在清洗结果中（不再被当作注释删除）
        assert "#" in cleaned, f"`#` 被错误剥离: {payload!r} -> {cleaned!r}"
        # 含 pg_read_file 的语句必须被拦截
        if "pg_read_file" in payload:
            assert _reject_dangerous_sql(payload) is True

    def test_mysql_hash_comment_still_works(self):
        """MySQL 中 `#` 是行注释，正常使用不受影响（数据库自行解析）。"""
        assert _reject_dangerous_sql("SELECT 1 # 普通注释") is False


# ---------------------------------------------------------------------------
# 7. Redis rdb_save/rdb_bgsave 反射绕过（第十轮审计，High）
# ---------------------------------------------------------------------------
class TestRedisRdbSaveBlocked:
    @pytest.mark.parametrize("method", ["rdb_save", "rdb_bgsave", "rdb_load"])
    def test_rdb_methods_forbidden(self, method):
        """rdb_* 方法族内部执行 CONFIG SET dir/dbfilename + SAVE，与
        config_set/save 相同文件写 RCE 链，必须全部封禁。"""
        assert _is_forbidden_redis(method) is True, f"应拦截: {method}"

    def test_legit_commands_still_allowed(self):
        """正常只读/键操作命令不受影响。"""
        for ok in ("get", "set", "keys", "lrange", "hgetall", "ttl", "memory_usage"):
            assert _is_forbidden_redis(ok) is False, f"不应拦截: {ok}"


# ---------------------------------------------------------------------------
# 8. logs.py Windows UNC 网络路径不得绕过 data 目录防护（第十轮审计，Medium）
# ---------------------------------------------------------------------------
class TestLogsUncPathBlocked:
    def test_unc_management_share_rejected(self):
        from fastapi import HTTPException
        from app.routers.logs import _safe_log_path

        for p in (
            r"\\localhost\S$\Graw\backend\data\secret.key",
            r"\\127.0.0.1\c$\Graw\backend\data\users.json",
            "//localhost/S$/Graw/backend/data/secret.key",
        ):
            with pytest.raises(HTTPException) as exc:
                _safe_log_path(p)
            assert exc.value.status_code == 400, f"应 400 拒绝: {p}"

    def test_normal_log_path_still_allowed(self):
        from app.routers.logs import _safe_log_path, _DATA_DIR_NORM

        # 正常日志路径与面板自身日志仍可访问
        _safe_log_path("/var/log/nginx/access.log")
        _safe_log_path("C:\\Windows\\System32\\winevt\\Logs\\System.evtx")
        assert "panel.log" in _safe_log_path(os.path.join(_DATA_DIR_NORM, "panel.log"))


# ---------------------------------------------------------------------------
# 9. webstats.py log_path 不得读取面板 data 目录（第十轮审计，Medium）
# ---------------------------------------------------------------------------
class TestWebstatsDataDirBlocked:
    def test_data_dir_and_unc_rejected(self):
        from fastapi import HTTPException
        from app.routers.webstats import _reject_forbidden_log_path, _DATA_DIR

        payloads = [
            os.path.join(_DATA_DIR, "secret.key"),
            os.path.join(_DATA_DIR, "users.json"),
            os.path.join(_DATA_DIR, "..", "data", "secret.key"),  # .. 混淆
            r"\\localhost\S$\Graw\backend\data\secret.key",
            "//localhost/S$/Graw/backend/data/secret.key",
            "relative/path.log",
        ]
        for p in payloads:
            with pytest.raises(HTTPException):
                _reject_forbidden_log_path(p)

    def test_legit_log_path_allowed(self):
        from app.routers.webstats import _reject_forbidden_log_path

        _reject_forbidden_log_path("/var/log/nginx/access.log")
        _reject_forbidden_log_path("/opt/1panel/www/sites/example.com/log/access.log")
