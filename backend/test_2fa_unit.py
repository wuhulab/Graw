# -*- coding: utf-8 -*-
"""
两步验证（2FA/TOTP）单元测试（不依赖运行中的后端服务）

覆盖：
  - TOTP 纯函数：生成密钥 / 校验正确码 / 拒绝错误码 / otpauth URI
  - 2FA 端点：setup（生成密钥）→ enable（验证码启用）→ disable（验证码关闭）
  - 登录流程：开启 2FA 后无验证码登录返回 otp_required；错误验证码 401；
    正确验证码签发令牌

用法：
  python test_2fa_unit.py
"""
import os
import sys
import tempfile
import shutil
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import auth as core  # noqa: E402
from app.routers import auth as auth_router  # noqa: E402


def _make_test_app():
    app = FastAPI()
    app.include_router(auth_router.router)
    client = TestClient(app)
    return client


class TotpUnitTest(unittest.TestCase):
    """TOTP 纯函数逻辑。"""

    def test_generate_and_verify(self):
        secret = core.generate_otp_secret()
        self.assertIsInstance(secret, str)
        self.assertGreaterEqual(len(secret), 16)
        # 从同一密钥推算当前码并校验
        counter = int(time.time()) // 30
        code = core._totp_at(secret, counter)
        self.assertEqual(len(code), 6)
        self.assertTrue(code.isdigit())
        self.assertTrue(core.verify_totp(secret, code))
        # 错误码拒绝
        self.assertFalse(core.verify_totp(secret, "000000"))
        # 非 6 位数字拒绝
        self.assertFalse(core.verify_totp(secret, "abc123"))
        self.assertFalse(core.verify_totp(secret, "123"))
        # 空 secret / 空码拒绝
        self.assertFalse(core.verify_totp("", "123456"))
        self.assertFalse(core.verify_totp(secret, ""))

    def test_otpauth_uri(self):
        uri = core.otpauth_uri("ABCDEFGHIJKLMNOP", "admin", "Graw")
        self.assertTrue(uri.startswith("otpauth://totp/"))
        self.assertIn("secret=ABCDEFGHIJKLMNOP", uri)
        self.assertIn("issuer=Graw", uri)
        self.assertIn("Graw:admin", uri)


class TwoFactorApiTest(unittest.TestCase):
    """通过 TestClient 验证 2FA 端点与登录流程。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        core.USERS_FILE = os.path.join(self._tmp, "users.json")
        core.SECRET_FILE = os.path.join(self._tmp, "secret.key")
        core._save_users({})
        # 注入测试用户（无 2FA）
        users = core._load_users() or {}
        users["otptester"] = {
            "username": "otptester",
            "password": core.hash_password("OtP#Pass123"),
            "role": "user",
            "must_change_password": False,
            "token_version": 0,
            "created_at": time.time(),
        }
        core._save_users(users)
        # 屏蔽 ShunX 入口校验（单测不涉及）
        auth_router.verify_entry = mock.MagicMock()
        self.client = _make_test_app()

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _login(self, otp_code=None, password="OtP#Pass123"):
        body = {"username": "otptester", "password": password}
        if otp_code is not None:
            body["otp_code"] = otp_code
        return self.client.post("/login", json=body)

    def test_login_without_2fa(self):
        r = self._login()
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["token"])

    def test_full_2fa_flow(self):
        # 1. 初始状态未启用
        token = self._login().json()["token"]
        H = {"Authorization": f"Bearer {token}"}
        st = self.client.get("/2fa/status", headers=H).json()
        self.assertFalse(st["otp_enabled"])

        # 2. setup 生成密钥
        r = self.client.post("/2fa/setup", headers=H)
        self.assertEqual(r.status_code, 200)
        secret = r.json()["secret"]
        self.assertIn("otpauth://totp/", r.json()["otpauth_uri"])
        st = self.client.get("/2fa/status", headers=H).json()
        self.assertTrue(st["has_secret"])
        self.assertFalse(st["otp_enabled"])

        # 3. 错误验证码 enable 失败
        r = self.client.post("/2fa/enable", headers=H, json={"code": "000000"})
        self.assertEqual(r.status_code, 400)

        # 4. 正确验证码 enable 成功
        code = core._totp_at(secret, int(time.time()) // 30)
        r = self.client.post("/2fa/enable", headers=H, json={"code": code})
        self.assertEqual(r.status_code, 200)
        st = self.client.get("/2fa/status", headers=H).json()
        self.assertTrue(st["otp_enabled"])

        # 5. 开启后：无验证码登录 → otp_required（不签发 token）
        r = self._login()
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["otp_required"])
        self.assertEqual(body["token"], "")
        self.assertIsNone(body["user"])

        # 6. 错误验证码 → 401
        r = self._login(otp_code="000000")
        self.assertEqual(r.status_code, 401)

        # 7. 正确验证码 → 200 签发 token
        code = core._totp_at(secret, int(time.time()) // 30)
        r = self._login(otp_code=code)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["token"])
        # 返回的用户信息带 otp_enabled
        self.assertTrue(r.json()["user"]["otp_enabled"])

        # 8. disable：错误验证码拒绝
        H2 = {"Authorization": f"Bearer {r.json()['token']}"}
        r = self.client.post("/2fa/disable", headers=H2, json={"code": "000000"})
        self.assertEqual(r.status_code, 400)
        # 正确验证码关闭
        code = core._totp_at(secret, int(time.time()) // 30)
        r = self.client.post("/2fa/disable", headers=H2, json={"code": code})
        self.assertEqual(r.status_code, 200)
        st = self.client.get("/2fa/status", headers=H2).json()
        self.assertFalse(st["otp_enabled"])

        # 9. 关闭后无验证码可直接登录
        r = self._login()
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["token"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
