# -*- coding: utf-8 -*-
"""
登录日志 / 异地登录提示 核心逻辑单元测试（不依赖运行中的后端服务）

覆盖：
  - 设备解析（User-Agent → 浏览器 · 系统）
  - 记录成功登录：首次新 IP / 新设备 → 标记异常；重复登录 → 正常
  - 记录失败登录：不触发异常标记
  - 日志环形截断上限
  - API：状态 / 我的登录历史 / 管理员列表 / 权限隔离 / 清空 / 配置开关

用法：
  python test_loginlog_unit.py
"""
import os
import shutil
import sys
import tempfile
import unittest
import unittest.mock as mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.routers import loginlog  # noqa: E402


def _make_app():
    """构建仅含 loginlog 路由的最小应用，并覆盖登录/管理员依赖。"""
    app = FastAPI()
    app.include_router(loginlog.router, prefix="/api/loginlog")

    async def fake_current_user():
        return {"username": "tester", "role": "user"}

    async def fake_admin():
        return {"username": "admin", "role": "admin"}

    # 覆盖 get_current_user（PROTECTED 依赖中引用的是模块级函数对象）
    app.dependency_overrides[loginlog.get_current_user] = fake_current_user
    app.dependency_overrides[loginlog.require_admin] = fake_admin
    return app


class LoginLogUnitTest(unittest.TestCase):
    """纯函数与数据层逻辑。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        loginlog.LOG_FILE = os.path.join(self._tmp, "login_logs.json")
        loginlog.KNOWN_FILE = os.path.join(self._tmp, "login_known.json")
        loginlog.MAX_LOG_ENTRIES = 5  # 缩小上限便于测试截断

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    # ------------------------------------------------------------------
    def test_parse_device(self):
        self.assertEqual(
            loginlog.parse_device(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0 Safari/537.36"
            ),
            "Chrome · Windows 10/11",
        )
        self.assertEqual(
            loginlog.parse_device(
                "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) Version/17.0 Mobile Safari/604.1"
            ),
            "Safari · iPhone",
        )
        self.assertEqual(
            loginlog.parse_device("curl/8.0.1"), "命令行工具 · 未知系统"
        )
        # 空 UA → 未知设备
        self.assertEqual(loginlog.parse_device(""), "未知设备")
        self.assertEqual(loginlog.parse_device(None), "未知设备")

    def test_first_login_is_abnormal(self):
        entry = loginlog.record_login(
            "alice", "1.2.3.4", "Mozilla/5.0 Chrome/120.0", "success"
        )
        self.assertTrue(entry["abnormal"])
        self.assertIn("新IP", entry["abnormal_reason"])
        self.assertIn("新设备", entry["abnormal_reason"])
        # 已记录到日志文件
        logs = loginlog._load_logs()
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["username"], "alice")
        self.assertEqual(logs[0]["ip"], "1.2.3.4")

    def test_repeat_login_is_normal(self):
        ua = "Mozilla/5.0 Chrome/120.0"
        loginlog.record_login("alice", "1.2.3.4", ua, "success")
        # 相同 IP + 相同设备 → 不再异常
        entry = loginlog.record_login("alice", "1.2.3.4", ua, "success")
        self.assertFalse(entry["abnormal"])

    def test_new_ip_only_triggers_abnormal(self):
        ua = "Mozilla/5.0 Chrome/120.0"
        loginlog.record_login("alice", "1.2.3.4", ua, "success")
        # 同设备新 IP → 异常（新IP）
        entry = loginlog.record_login("alice", "5.6.7.8", ua, "success")
        self.assertTrue(entry["abnormal"])
        self.assertEqual(entry["abnormal_reason"], "新IP")
        # 再换回旧 IP → 正常（设备也在基线内）
        entry = loginlog.record_login("alice", "1.2.3.4", ua, "success")
        self.assertFalse(entry["abnormal"])

    def test_failed_login_not_abnormal(self):
        ua = "Mozilla/5.0 Chrome/120.0"
        loginlog.record_login("alice", "1.2.3.4", ua, "success")
        entry = loginlog.record_login("alice", "9.9.9.9", ua, "failed", "密码错误")
        # 失败登录不参与异常标记，也不更新指纹
        self.assertFalse(entry["abnormal"])
        self.assertEqual(entry["status"], "failed")
        self.assertEqual(entry["detail"], "密码错误")

    def test_log_cap(self):
        for i in range(10):
            loginlog.record_login("bob", f"10.0.0.{i}", f"UA-{i}", "success")
        logs = loginlog._load_logs()
        self.assertEqual(len(logs), 5)  # 环形截断
        # 最新在最前
        self.assertEqual(logs[0]["ip"], "10.0.0.9")

    @mock.patch("app.routers.notify.push_all", return_value=(1, 0))
    def test_notify_called_on_abnormal(self, mock_push):
        # record_login 内部延迟 `from app.routers.notify import push_all`，
        # patch 模块级 push_all 后，该 import 会在调用时取到打桩对象
        entry = loginlog.record_login(
            "alice", "8.8.8.8", "Mozilla/5.0 Chrome/120.0", "success"
        )
        self.assertTrue(entry["abnormal"])
        mock_push.assert_called_once()
        self.assertIn("异常登录提醒", mock_push.call_args[0][0])

    # ------------------------------------------------------------------
    def test_api_status_and_mine(self):
        app = _make_app()
        client = TestClient(app)
        loginlog.record_login("tester", "1.2.3.4", "UA-Chrome", "success")
        loginlog.record_login("other", "2.2.2.2", "UA-Chrome", "success")
        # status（登录即可）
        r = client.get("/api/loginlog/status")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["total"], 2)
        self.assertEqual(body["success"], 2)
        # mine（只返回当前用户 tester 的）
        r = client.get("/api/loginlog/mine")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()["logs"]), 1)
        self.assertEqual(r.json()["logs"][0]["username"], "tester")
        # 管理员 list 可看全部
        r = client.get("/api/loginlog/list")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.json()["logs"]), 2)
        # 按 username 过滤
        r = client.get("/api/loginlog/list", params={"username": "other"})
        self.assertEqual(len(r.json()["logs"]), 1)

    def test_api_clear_and_config(self):
        app = _make_app()
        client = TestClient(app)
        loginlog.record_login("tester", "1.2.3.4", "UA", "success")
        # 配置开关
        r = client.put("/api/loginlog/config", json={"alert_enabled": False})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(loginlog._get_config()["alert_enabled"])
        # 清空
        r = client.post("/api/loginlog/clear")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(loginlog._load_logs(), [])

    def test_api_test_alert(self):
        app = _make_app()
        client = TestClient(app)
        with mock.patch("app.routers.notify.push_all", return_value=(2, 0)):
            r = client.post("/api/loginlog/test-alert")
            self.assertEqual(r.status_code, 200)
            body = r.json()
            self.assertTrue(body["ok"])
            self.assertEqual(body["sent"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
