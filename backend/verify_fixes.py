# -*- coding: utf-8 -*-
"""
verify_fixes.py - 回归验证 Graw 安全修复

验证项：
  1. files.py mkdir/rename 远程命令注入：真实端点链路，捕获最终交给对端 shell
     的命令字符串，断言注入符已被 shlex.quote 包裹（修复生效）。
  2. cron.py 标准任务构造器：content 含注入符时被 shlex.quote 转义。
  3. databases.py _sqlite_file：相对路径 ../ 逃逸被拒绝（超过数据目录）。
  4. files.py 本地符号链接加固后仍能正常返回普通路径（不误伤功能）。

运行：python verify_fixes.py  （在 backend 目录）
"""
import asyncio
import os
import subprocess
import sys
import unittest
from unittest import mock

BACKEND = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BACKEND)

CAPTURED: list = []


def _stub_remote_node():
    """打桩 node_manager：模拟远程 SSH 节点，捕获发给对端 shell 的命令字符串。"""
    import app.node_manager as nm

    def fake_run_ssh(node, remote_cmd, **kwargs):
        CAPTURED.append(remote_cmd)
        cp = subprocess.CompletedProcess([], 0)
        # 让 exists 的前置探测命令返回"存在"
        cp.stdout = "1"
        cp.returncode = 0
        return cp

    nm._run_ssh = fake_run_ssh
    nm.get_current_node = lambda: {
        "id": "n1", "type": "ssh", "host": "127.0.0.1", "port": 22,
        "user": "root", "auth": "password", "password": "x",
    }


def _stub_local_node():
    """恢复本地节点（用于路径可用性测试）。"""
    import app.node_manager as nm
    nm.get_current_node = lambda: {"id": "local", "type": "local"}


class TestFixes(unittest.TestCase):
    def tearDown(self):
        # 默认恢复远程桩，避免影响其他用例
        _stub_remote_node()

    def _call_mkdir(self, path: str) -> None:
        import app.routers.files as files
        req = files.MkdirRequest(path=path)
        with mock.patch("app.auditlog.record", return_value=None):
            asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
                files.mkdir(req, request=mock.MagicMock(), user={"username": "t"})
            )

    def test_mkdir_injection_quoted(self):
        """修复后：发给对端 shell 的 mkdir 命令中注入符被单引号包裹。"""
        CAPTURED.clear()
        payload = "/tmp/x; touch /tmp/pwned_$(id -u); #"
        self._call_mkdir(payload)
        self.assertTrue(CAPTURED, "应捕获到远程命令")
        cmd = CAPTURED[0]
        self.assertTrue(cmd.startswith("mkdir -p '"), cmd)
        # 整个 payload 应被引号内联，裸注入符不允许出现
        self.assertIn("; touch", cmd)
        self.assertIn("'", cmd)
        # 修复证据：payload 被 '...' 括起，shell 会把整串当字面路径
        self.assertIn(f"'{payload}'", cmd, cmd)

    def test_rename_injection_quoted(self):
        import app.routers.files as files
        CAPTURED.clear()
        src = "/tmp/a; reboot"
        dst = "/tmp/b"
        with mock.patch("app.auditlog.record", return_value=None):
            asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
                files.rename(files.RenameRequest(src=src, dst=dst),
                             request=mock.MagicMock(), user={"username": "t"})
            )
        self.assertTrue(CAPTURED, "应捕获到远程命令")
        mv_cmd = next((c for c in CAPTURED if c.startswith("mv ")), None)
        self.assertIsNotNone(mv_cmd, str(CAPTURED))
        self.assertTrue(mv_cmd.startswith("mv "), mv_cmd)
        # 恶意 src 含注入符，必须被单引号包裹；安全 dst 可被 shlex.quote 保持原样
        self.assertIn("'/tmp/a; reboot'", mv_cmd, mv_cmd)
        self.assertIn("/tmp/b", mv_cmd, mv_cmd)

    def test_cron_builders_quoted(self):
        import app.routers.cron as cron
        for t in ("visit_url", "clean_logs", "backup_container"):
            payload = "x; touch /tmp/pwned"
            cmd = cron._STANDARD_BUILDERS[t](payload)
            self.assertIn("'x; touch /tmp/pwned'", cmd, f"cron[{t}]: {cmd}")

    def test_databases_sqlite_escape_blocked(self):
        import app.routers.databases as databases
        from fastapi import HTTPException
        escaped = "../../etc/passwd.sqlite"
        try:
            databases._sqlite_file({"database": escaped})
        except HTTPException as e:
            self.assertEqual(e.status_code, 400)
        else:
            self.fail("相对路径../逃逸未被拒绝")

    def test_databases_sqlite_legit_allowed(self):
        import app.routers.databases as databases
        p = databases._sqlite_file({"database": "sub/db.sqlite"})
        norm = os.path.normpath(databases.DATA_DIR)
        self.assertIn(norm, os.path.normpath(p))

    def test_files_local_path_still_works(self):
        _stub_local_node()
        import app.routers.files as files
        here = files._safe_path(os.getcwd())
        self.assertTrue(os.path.isdir(here), here)

    def test_databases_filter_blocks_pg_file_primitives(self):
        """回归：SQL 安全过滤器必须拦截各数据库专有服务端文件读写原语。

        原实现为子串黑名单，遗漏 PostgreSQL 的 pg_read_file / pg_ls_dir /
        pg_write_file 等，在特权账号下可升级为宿主机 RCE。
        """
        import app.routers.databases as databases

        for payload in (
            "SELECT pg_read_file('/etc/passwd')",
            "SELECT pg_read_binary_file('/etc/shadow')",
            "SELECT pg_ls_dir('/')",
            "SELECT pg_write_file('/tmp/x','y')",
            "SELECT pg_ls_waldir()",
            "SELECT pg_logdir_ls()",
            "SELECT pg_stat_file('/etc/hostname')",
            "SELECT 1 INTO graw_steal FROM pg_read_file('/etc/passwd')",
            "SELECT 1 INTO OUTFILE '/tmp/x'",
            "DROP TABLE users",
            "CREATE TABLE evil(x int)",
            "ALTER TABLE users ADD col int",
            "TRUNCATE TABLE logs",
            "GRANT ALL ON db.* TO 'u'@'%'",
            "CALL do_evil()",
            "PREPARE stmt FROM 'DROP TABLE x'",
            "COPY t FROM PROGRAM 'id'",
        ):
            self.assertTrue(
                databases._reject_dangerous_sql(payload),
                f"过滤器未拦截危险 SQL: {payload}",
            )

    def test_databases_filter_allows_legit_queries(self):
        """回归：合法查询（含字面量/标识符中的 drop、create、order 等）不应被误拦截。"""
        import app.routers.databases as databases

        for q in (
            "SELECT * FROM users ORDER BY id DESC",
            "SELECT id FROM products WHERE note LIKE '%drop table%'",
            "SHOW TABLES",
            "EXPLAIN SELECT * FROM users WHERE id=1",
            "WITH cte AS (SELECT 1) SELECT * FROM cte",
            "INSERT INTO logs(msg) VALUES ('create backup done')",
            "UPDATE users SET name='a' WHERE id=1",
            "DELETE FROM sessions WHERE id=1",
            "PRAGMA table_info(users)",
            "SELECT * FROM 'order' WHERE x=1",
            "SELECT 1 /* drop */ FROM dual",
            "SELECT * FROM t WHERE col='create'",
        ):
            self.assertFalse(
                databases._reject_dangerous_sql(q),
                f"合法查询被误拦截: {q}",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)