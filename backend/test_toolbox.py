# -*- coding: utf-8 -*-
"""
工具箱路由测试：单元 + 集成（TestClient 风格）

单元部分（ToolboxApiTest）：
  - 独立 FastAPI 应用挂载 toolbox 路由（无鉴权），逐项验证各工具执行与参数校验：
    Base64 编解码 / MD5-SHA1-SHA256 哈希 / 时间戳互转 / 端口扫描 / Whois。
  - whois 依赖系统 whois 命令，用 mock 模拟，避免环境差异。

集成部分（ToolboxIntegrationTest）：
  - 使用 app.main 完整应用 + TestClient，注入临时管理员/普通用户账号。
  - 登录时带 X-ShunX-Entry 头（从 data/shunx.json 动态读取，与生产一致）。
  - 验证 /api/toolbox/exec 的权限分级：无 token 401 / 普通用户 403 / 管理员 200。

用法：
  python test_toolbox.py          # 直接运行（unittest）
"""
import json
import os
import shutil
import socket
import sys
import unittest
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.routers import toolbox  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def _entry_headers() -> dict:
    """从 data/shunx.json 读取安全入口路径，构造登录所需头（未配置则返回空）。"""
    entry = None
    try:
        with open(os.path.join(HERE, "data", "shunx.json"), "r", encoding="utf-8") as f:
            entry = json.load(f).get("entry_path")
    # CodeQL [py/empty-except] 读取可选 shunx.json：未配置/损坏时静默忽略
    except Exception:
        pass
    return {"X-ShunX-Entry": entry} if entry else {}


def _open_listener() -> socket.socket:
    """在本机 127.0.0.1 上创建一个监听 socket，用于构造确定「开放」的端口。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    return s


class ToolboxApiTest(unittest.TestCase):
    """单元测试：独立应用挂载 toolbox 路由，验证各工具与校验逻辑。"""

    def setUp(self):
        app = FastAPI()
        app.include_router(toolbox.router)
        self.client = TestClient(app)

    def _exec(self, tool, args=None, **kw):
        return self.client.post("/exec", json={"tool": tool, "args": args or {}}, **kw)

    # ---------------- Base64 ----------------
    def test_base64_encode(self):
        r = self._exec("base64_encode", {"text": "hello"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["result"], "aGVsbG8=")

    def test_base64_encode_unicode(self):
        r = self._exec("base64_encode", {"text": "你好"})
        self.assertEqual(r.status_code, 200)
        # 解码验证回原文本
        r2 = self._exec("base64_decode", {"text": r.json()["result"]})
        self.assertEqual(r2.json()["result"], "你好")

    def test_base64_decode_roundtrip(self):
        r = self._exec("base64_decode", {"text": "aGVsbG8="})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["result"], "hello")

    def test_base64_decode_invalid(self):
        r = self._exec("base64_decode", {"text": "!!!not-base64!!!"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("Base64", r.json()["detail"])

    def test_base64_empty(self):
        self.assertEqual(self._exec("base64_encode", {"text": ""}).status_code, 400)

    # ---------------- 哈希 ----------------
    def test_hash_md5(self):
        r = self._exec("hash", {"text": "hello", "algo": "md5"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["result"], "MD5: 5d41402abc4b2a76b9719d911017c592")

    def test_hash_sha1_sha256(self):
        sha1 = self._exec("hash", {"text": "hello", "algo": "sha1"}).json()["result"]
        sha256 = self._exec("hash", {"text": "hello", "algo": "sha256"}).json()["result"]
        self.assertTrue(sha1.startswith("SHA1: "))
        self.assertTrue(sha256.startswith("SHA256: "))
        self.assertEqual(len(sha1.split(": ")[1]), 40)
        self.assertEqual(len(sha256.split(": ")[1]), 64)

    def test_hash_invalid_algo(self):
        r = self._exec("hash", {"text": "hello", "algo": "crc32"})
        self.assertEqual(r.status_code, 400)

    # ---------------- 时间戳 ----------------
    def test_timestamp_to_datetime(self):
        r = self._exec("timestamp_to_datetime", {"timestamp": "0"})
        self.assertEqual(r.status_code, 200)
        data = r.json()["result"]
        self.assertEqual(data["timestamp"], 0)
        self.assertIn("1970", data["utc"])

    def test_timestamp_invalid(self):
        self.assertEqual(self._exec("timestamp_to_datetime", {"timestamp": "abc"}).status_code, 400)
        self.assertEqual(self._exec("timestamp_to_datetime", {"timestamp": "999999999999999"}).status_code, 400)

    def test_datetime_to_timestamp(self):
        r = self._exec("datetime_to_timestamp", {"datetime": "2026-08-20 00:00:00"})
        self.assertEqual(r.status_code, 200)
        self.assertIsInstance(r.json()["result"]["timestamp"], int)

    def test_datetime_invalid(self):
        r = self._exec("datetime_to_timestamp", {"datetime": "not a date"})
        self.assertEqual(r.status_code, 400)

    # ---------------- 端口扫描 ----------------
    def test_port_scan(self):
        srv = _open_listener()
        try:
            port = srv.getsockname()[1]
            r = self._exec("port_scan", {"host": "127.0.0.1", "start_port": port, "end_port": port + 2})
            self.assertEqual(r.status_code, 200)
            data = r.json()["result"]
            self.assertIn(port, data["open_ports"])
            self.assertEqual(data["closed_count"], (port + 2 - port + 1) - len(data["open_ports"]))
        finally:
            srv.close()

    def test_port_scan_invalid_port(self):
        self.assertEqual(
            self._exec("port_scan", {"host": "127.0.0.1", "start_port": 0, "end_port": 1}).status_code, 400
        )
        self.assertEqual(
            self._exec("port_scan", {"host": "127.0.0.1", "start_port": 1, "end_port": 70000}).status_code, 400
        )

    def test_port_scan_invalid_host(self):
        self.assertEqual(
            self._exec("port_scan", {"host": "bad host; rm -rf /", "start_port": 1, "end_port": 2}).status_code, 400
        )

    def test_port_scan_too_many(self):
        r = self._exec("port_scan", {"host": "127.0.0.1", "start_port": 1, "end_port": 1 + toolbox.MAX_PORTS})
        self.assertEqual(r.status_code, 400)

    # ---------------- Whois ----------------
    def test_whois_unavailable(self):
        with mock.patch("shutil.which", return_value=None):
            r = self._exec("whois", {"domain": "example.com"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("whois", r.json()["detail"])

    def test_whois_ok(self):
        fake = SimpleNamespace(stdout="Domain Name: EXAMPLE.COM", stderr="", returncode=0)
        with mock.patch("shutil.which", return_value="/usr/bin/whois"), \
             mock.patch("subprocess.run", return_value=fake) as run:
            r = self._exec("whois", {"domain": "example.com"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("EXAMPLE.COM", r.json()["result"]["result"])
        # 命令必须以参数列表方式调用（无 shell），防注入
        run.assert_called_once()
        args, _ = run.call_args
        self.assertEqual(args[0], ["/usr/bin/whois", "example.com"])

    def test_whois_invalid_domain(self):
        self.assertEqual(self._exec("whois", {"domain": "not a domain"}).status_code, 400)

    # ---------------- 工具名白名单 ----------------
    def test_unknown_tool(self):
        r = self._exec("rm_rf")
        self.assertEqual(r.status_code, 400)
        self.assertIn("不支持的工具", r.json()["detail"])

    def test_tool_case_insensitive(self):
        r = self._exec("BASE64_ENCODE", {"text": "hi"})
        self.assertEqual(r.status_code, 200)


class ToolboxIntegrationTest(unittest.TestCase):
    """集成测试：真实 auth 登录 + ShunX 安全入口 + ADMIN 权限分级 + toolbox 端点。

    为保持测试稳定，此处按生产 main.py 的接线方式自行组装集成应用：
    auth 路由（/api/auth，含安全入口校验）+ toolbox 路由（挂 ADMIN 依赖），
    不直接依赖 app.main（避免受其他模块并发改动影响）。
    """

    @classmethod
    def setUpClass(cls):
        from fastapi import Depends, FastAPI

        from app import auth as auth_mod
        from app.routers import auth as auth_router
        from app.routers import toolbox as toolbox_router

        cls.auth_mod = auth_mod
        cls.users_file = auth_mod.USERS_FILE
        cls.backup = cls.users_file + ".toolboxbk"
        if os.path.exists(cls.users_file):
            shutil.copy2(cls.users_file, cls.backup)
        users = auth_mod._load_users() or {}
        users["tbtestadmin"] = {
            "username": "tbtestadmin",
            "password": auth_mod.hash_password("TbTest#12345"),
            "role": "admin",
            "must_change_password": False,
            "token_version": 0,
            "created_at": 0,
        }
        users["tbtestuser"] = {
            "username": "tbtestuser",
            "password": auth_mod.hash_password("TbTest#12345"),
            "role": "user",
            "must_change_password": False,
            "token_version": 0,
            "created_at": 0,
        }
        auth_mod._save_users(users)

        # 组装集成应用：与生产接线一致（auth + toolbox 挂 ADMIN 依赖）
        cls.app = FastAPI()
        cls.app.include_router(auth_router.router, prefix="/api/auth", tags=["auth"])
        cls.app.include_router(
            toolbox_router.router,
            prefix="/api/toolbox",
            tags=["toolbox"],
            dependencies=[Depends(auth_mod.require_admin)],
        )
        cls.client = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls):
        if os.path.exists(cls.backup):
            shutil.copy2(cls.backup, cls.users_file)
            os.remove(cls.backup)

    def _login(self, username, password="TbTest#12345", with_entry=True):
        headers = _entry_headers() if with_entry else {}
        return self.client.post(
            "/api/auth/login",
            json={"username": username, "password": password},
            headers=headers,
        )

    def test_login_requires_entry(self):
        """已配置安全入口时，不带 X-ShunX-Entry 的登录必须被拒绝。"""
        if not _entry_headers():
            self.skipTest("未配置 ShunX 安全入口，跳过")
        r = self._login("tbtestadmin", with_entry=False)
        self.assertEqual(r.status_code, 403)

    def test_no_token_401(self):
        r = self.client.post("/api/toolbox/exec", json={"tool": "base64_encode", "args": {"text": "hi"}})
        self.assertEqual(r.status_code, 401)

    def test_user_forbidden_403(self):
        r = self._login("tbtestuser")
        self.assertEqual(r.status_code, 200, r.text)
        token = r.json()["token"]
        r2 = self.client.post(
            "/api/toolbox/exec",
            json={"tool": "base64_encode", "args": {"text": "hi"}},
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(r2.status_code, 403)

    def test_admin_exec_ok(self):
        r = self._login("tbtestadmin")
        self.assertEqual(r.status_code, 200, r.text)
        token = r.json()["token"]
        r2 = self.client.post(
            "/api/toolbox/exec",
            json={"tool": "base64_encode", "args": {"text": "hello"}},
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(r2.status_code, 200, r2.text)
        self.assertEqual(r2.json()["result"], "aGVsbG8=")

    def test_admin_invalid_tool_400(self):
        r = self._login("tbtestadmin")
        token = r.json()["token"]
        r2 = self.client.post(
            "/api/toolbox/exec",
            json={"tool": "evil_tool", "args": {}},
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(r2.status_code, 400)


if __name__ == "__main__":
    unittest.main(verbosity=2)
