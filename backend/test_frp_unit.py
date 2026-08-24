# -*- coding: utf-8 -*-
"""
Frp 管理核心逻辑单元测试（不依赖运行中的后端服务）

覆盖：
  - frps / frpc TOML 渲染（token 转义、dashboard、代理块）
  - 禁用代理不写入 toml（enabled=False 过滤）
  - 字段白名单校验（代理名 / 域名 / 端口 / 控制字符注入拒绝）
  - 端点 CRUD（代理增删改/启停、模式切换、预览）

用法：
  python test_frp_unit.py
"""
import os
import sys
import tempfile
import unittest

# 确保可导入 app 包
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.routers import frp  # noqa: E402


class FrpUnitTest(unittest.TestCase):
    """主要验证渲染与校验的纯函数逻辑。"""

    def setUp(self):
        # 临时数据目录，避免污染 backend/data/frp.json
        self._tmp = tempfile.mkdtemp()
        frp.FRP_FILE = os.path.join(self._tmp, "frp.json")
        # 让写盘 / 探测走本地临时目录语义，避免真实触碰系统路径
        frp.node_manager.host_path = lambda p: p
        if os.path.dirname(os.path.abspath(frp.FRP_FILE)):
            pass

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_render_server(self):
        """服务端 toml：bindAddr/bindPort/token/dashboard/log。"""
        s = {
            "bindAddr": "0.0.0.0", "bindPort": 7000, "token": 'abc"def\\g',
            "dashboardAddr": "127.0.0.1", "dashboardPort": 7500,
            "dashboardUser": "admin", "dashboardPwd": "p@ss", "logLevel": "info",
        }
        toml = frp._render_server(s)
        self.assertIn('bindAddr = "0.0.0.0"', toml)
        self.assertIn("bindPort = 7000", toml)
        # token 中双引号与反斜杠必须被转义，防止破坏 TOML 结构
        self.assertIn(r'auth.token = "abc\"def\\g"', toml)
        self.assertIn("webServer.port = 7500", toml)
        self.assertIn('webServer.user = "admin"', toml)
        self.assertIn('log.level = "info"', toml)

    def test_render_client_proxies(self):
        """客户端 toml：各类代理正确写出；禁用代理被剔除。"""
        c = {
            "serverAddr": "1.2.3.4", "serverPort": 7000, "token": "t",
            "loginFailExit": True, "logLevel": "info",
            "proxies": [
                {"id": "1", "name": "web", "type": "tcp", "localIp": "127.0.0.1",
                 "localPort": 80, "remotePort": 8080, "customDomains": "",
                 "useEncryption": False, "useCompression": False, "enabled": True},
                {"id": "2", "name": "http", "type": "http", "localIp": "127.0.0.1",
                 "localPort": 3000, "remotePort": None, "customDomains": "a.com,*.b.com",
                 "useEncryption": True, "useCompression": True, "enabled": True},
                {"id": "3", "name": "off", "type": "tcp", "localIp": "127.0.0.1",
                 "localPort": 1, "remotePort": 1, "customDomains": "",
                 "useEncryption": False, "useCompression": False, "enabled": False},
            ],
        }
        toml = frp._render_client(c)
        self.assertIn('serverAddr = "1.2.3.4"', toml)
        self.assertIn("remotePort = 8080", toml)
        self.assertIn("transport.useEncryption = true", toml)
        self.assertIn("customDomains = [\"a.com\", \"*.b.com\"]", toml)
        # 禁用的代理不可写入 toml（整体剔除）
        self.assertNotIn("name = \"off\"", toml)
        self.assertNotIn("remotePort = 1", toml)

    def test_validation_injection(self):
        """白名单校验：拒绝代理名/域名/端口的非法与注入值。"""
        from fastapi import HTTPException
        # 代理名含空格 / 中文 / 结尾点 → 拒绝
        with self.assertRaises(HTTPException):
            frp._check_proxy_name("bad name")
        with self.assertRaises(HTTPException):
            frp._check_proxy_name("ab..cd")
        with self.assertRaises(HTTPException):
            frp._check_proxy_name("a\nb")
        with self.assertRaises(HTTPException):
            frp._check_proxy_name("a@b")
        self.assertEqual(frp._check_proxy_name("my_proxy-1"), "my_proxy-1")
        # 域名：以 - 开头 / 含控制字符 → 拒绝
        with self.assertRaises(HTTPException):
            frp._check_domains("-evil.com")
        with self.assertRaises(HTTPException):
            frp._check_domains("evil..com")
        with self.assertRaises(HTTPException):
            frp._check_domains("*.bad.*.com")
        with self.assertRaises(HTTPException):
            frp._check_domains("ok.com\r\nserverPort=1")
        self.assertEqual(frp._check_domains("a.com,*.b.com"), "a.com,*.b.com")
        # 端口边界
        with self.assertRaises(HTTPException):
            frp._check_port(0, "端口")
        with self.assertRaises(HTTPException):
            frp._check_port(65536, "端口")
        self.assertEqual(frp._check_port(8080, "端口"), 8080)


class FrpApiTest(unittest.TestCase):
    """通过 TestClient 验证代理 CRUD / 模式切换 / 预览端点。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        frp.FRP_FILE = os.path.join(self._tmp, "frp.json")
        frp.node_manager.host_path = lambda p: p
        # 避免真实探测系统 frp / 写系统路径
        frp.node_manager.write_text = lambda path, content: None
        frp.node_manager.host_cmd = lambda *a, **k: _FakeProc("", "")
        frp.node_manager.host_shell = lambda *a, **k: _FakeProc("", "")
        frp.node_manager.host_which = lambda c: "/usr/bin/" + c
        frp._unit_exists = lambda mode: False
        frp._running = lambda data: False
        # 最小应用：只挂 frp 路由，端点内的业务校验不受 ADMIN 依赖影响
        app = FastAPI()
        app.include_router(frp.router)
        self.client = TestClient(app)

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _add(self, name="web", **kw):
        payload = {
            "name": name, "type": "tcp", "localIp": "127.0.0.1",
            "localPort": 80, "remotePort": 8080, "customDomains": "",
            "enabled": True,
        }
        payload.update(kw)
        return self.client.post("/proxies", json=payload)

    def test_proxy_crud(self):
        """代理增删改与启停。"""
        r = self._add()
        self.assertEqual(r.status_code, 200)
        pid = r.json()["id"]
        proxies = self.client.get("/config").json()["client"]["proxies"]
        self.assertEqual(len(proxies), 1)
        self.assertEqual(proxies[0]["name"], "web")

        # 重名拒绝
        self.assertEqual(self._add().status_code, 400)
        # 更新
        r = self.client.put(f"/proxies/{pid}", json={
            "name": "web2", "type": "http", "localIp": "127.0.0.1", "localPort": 3000,
            "customDomains": "x.com", "enabled": True,
        })
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["type"], "http")
        # 禁用
        r = self.client.post(f"/toggle-proxy/{pid}", json={"enabled": False})
        self.assertEqual(r.json()["enabled"], False)
        # 删除
        self.assertEqual(self.client.delete(f"/proxies/{pid}").status_code, 200)
        self.assertEqual(self.client.delete("/proxies/notexist").status_code, 404)

    def test_tcp_requires_remote_port(self):
        """tcp 代理缺 remotePort 应被拒绝。"""
        r = self.client.post("/proxies", json={
            "name": "bad", "type": "tcp", "localIp": "127.0.0.1",
            "localPort": 80, "remotePort": None, "customDomains": "",
        })
        self.assertEqual(r.status_code, 400)

    def test_mode_switch_and_preview(self):
        """模式切换与预览。"""
        r = self.client.post("/mode", json={"mode": "client"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["mode"], "client")
        self.assertIn("serverAddr", self.client.get("/preview").json()["toml"])
        self.assertEqual(self.client.post("/mode", json={"mode": "hack"}).status_code, 400)

    def test_load_default(self):
        """无 frp.json 时读回默认结构。"""
        data = self.client.get("/config").json()
        self.assertEqual(data["mode"], "server")
        self.assertEqual(data["server"]["bindPort"], 7000)
        self.assertEqual(data["client"]["proxies"], [])


# 最小 subprocess 结果替身：避免探测/启停真实触碰系统
from types import SimpleNamespace  # noqa: E402
_FakeProc = lambda out, err: SimpleNamespace(returncode=0, stdout=out, stderr=err)


if __name__ == "__main__":
    unittest.main(verbosity=2)