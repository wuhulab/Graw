# -*- coding: utf-8 -*-
"""Graw 主面板 VIP 模块单元测试（无需后端运行）。

覆盖：
  - 配置：后端常量默认地址 / 环境变量可选覆盖 / 前端不可改动（无 config 接口）
  - 状态：未开通 / 生效 / 过期判断、按共享截止 + 时长增量无限叠加
  - 激活：成功落库 / 授权码为空 / 授权码服务不可达（mock HTTP）
  - 共享：面板级 VIP（任意账号激活 → 所有账号生效、跨账号叠加不吞时长）

运行：python -m test_vip_unit
"""
import os
import tempfile
import unittest
import unittest.mock as mock
from datetime import datetime, timedelta, timezone

import app.vip as vip_mod
from app.routers import vip as vip_router


def _isolate(case: unittest.TestCase):
    """把 vip 模块的状态存储路径重定向到临时目录（地址为后端常量/环境变量）。"""
    case.tmpdir = tempfile.mkdtemp(prefix="vip_unit_")
    case.data_dir = os.path.join(case.tmpdir, "data")
    os.makedirs(case.data_dir, exist_ok=True)
    case.state_file = os.path.join(case.data_dir, "vip.json")
    patchers = [
        mock.patch.object(vip_mod, "DATA_DIR", case.data_dir),
        mock.patch.object(vip_mod, "VIP_STATE_FILE", case.state_file),
        mock.patch.object(vip_mod, "_state_cache", None),
    ]
    for p in patchers:
        p.start()
        case._patchers = patchers
    case.addCleanup(_stop, case)


def _stop(case: unittest.TestCase):
    for p in getattr(case, "_patchers", []):
        p.stop()


def _until_plus(days: int) -> str:
    """返回 days 天后的 ISO UTC 时间（作为 VIP 截止）。"""
    return (datetime.now(timezone.utc) + timedelta(days=days)).isoformat()


class VipConfigTest(unittest.TestCase):
    def setUp(self):
        _isolate(self)

    def test_default_server_url(self):
        # 无环境变量 → 使用后端常量（尾部斜杠被规范化）
        self.assertEqual(vip_mod.get_server_url(), "https://graw-vip.shunx.top")

    def test_env_server_url_override(self):
        # 部署时可用环境变量覆盖（仍不受前端控制）
        with mock.patch.dict(os.environ, {"GRAW_VIP_SERVER": "https://graw-vip.shunx.top/"}):
            self.assertEqual(vip_mod.get_server_url(), "https://graw-vip.shunx.top")

    def test_no_public_config_endpoint(self):
        # 前端不可修改授权地址：路由不再暴露 /config
        paths = {getattr(r, "path", "") for r in vip_router.router.routes}
        self.assertNotIn("/config", paths)
        self.assertFalse(hasattr(vip_mod, "set_server_url"))


class VipStatusTest(unittest.TestCase):
    def setUp(self):
        _isolate(self)

    def test_not_active_when_empty(self):
        s = vip_mod.get_vip("alice")
        self.assertFalse(s["vip"])
        self.assertFalse(s["is_vip"])
        self.assertEqual(s["plan"], "")

    def test_active_and_expired(self):
        # 直接落库一个增量为 10 天的有效 VIP
        with mock.patch.object(vip_mod, "_save_state"):
            vip_mod._set_user_vip("bob", "month", timedelta(days=10))
        s1 = vip_mod.get_vip("bob")
        self.assertTrue(s1["vip"])
        self.assertEqual(s1["plan"], "month")
        # 过期判断（直接注入过去的截止时间）
        with mock.patch.object(vip_mod, "_save_state"):
            vip_mod._state_cache["bob"]["vip_until"] = _until_plus(-1)
        self.assertFalse(vip_mod.get_vip("bob")["vip"])


class VipActivateTest(unittest.TestCase):
    def setUp(self):
        _isolate(self)

    def test_empty_code_rejected(self):
        with self.assertRaises(ValueError):
            vip_mod.activate_vip("alice", "  ")

    def test_activate_success(self):
        # 服务端返回权威时长增量 duration_seconds → 落库截止 ≈ now + 30 天
        with mock.patch.object(vip_mod, "_call_license", return_value={
            "ok": True, "type": "month", "duration_seconds": 30 * 24 * 3600,
        }):
            s = vip_mod.activate_vip("alice", "GRAW-MONTH-ABC")
        self.assertTrue(s["vip"])
        self.assertEqual(s["plan"], "month")
        end = datetime.fromisoformat(s["vip_until"])
        self.assertAlmostEqual((end - datetime.now(timezone.utc)).total_seconds(),
                               30 * 24 * 3600, delta=60)

    def test_activate_license_rejection(self):
        # 服务端拒绝（已使用/无效）→ ValueError
        with mock.patch.object(vip_mod, "_call_license", return_value={"ok": False, "detail": "该授权码已被使用"}):
            with self.assertRaises(ValueError) as cm:
                vip_mod.activate_vip("alice", "GRAW-MONTH-XYZ")
            self.assertIn("已被使用", str(cm.exception))

    def test_activate_network_error(self):
        # 服务不可达 → ValueError，且不落库
        with mock.patch.object(vip_mod, "_call_license", side_effect=ValueError("无法连接授权码服务")):
            with self.assertRaises(ValueError):
                vip_mod.activate_vip("alice", "GRAW-MONTH-XYZ")
        self.assertFalse(vip_mod.get_vip("alice")["vip"])


class VipRouterTest(unittest.TestCase):
    def setUp(self):
        _isolate(self)

    def test_status_uses_current_user(self):
        user = {"username": "alice"}
        with mock.patch.object(vip_mod, "get_vip", return_value={"vip": True, "plan": "month", "vip_until": "", "is_vip": True, "activated_at": ""}):
            res = vip_router.vip_status(user)
        self.assertTrue(res["vip"])


class VipSharedTest(unittest.TestCase):
    """面板级 VIP 共享：任意账号激活 → 所有账号同时生效。"""

    def setUp(self):
        _isolate(self)

    def test_shared_across_users(self):
        # alice 激活后，未激活过的 bob 同样生效（面板级共享）
        with mock.patch.object(vip_mod, "_call_license", return_value={
            "ok": True, "type": "month", "duration_seconds": 30 * 24 * 3600,
        }):
            vip_mod.activate_vip("alice", "CODE-1")
        s_bob = vip_mod.get_vip("bob")
        self.assertTrue(s_bob["vip"])
        self.assertTrue(s_bob["is_vip"])
        end = datetime.fromisoformat(s_bob["vip_until"])
        self.assertAlmostEqual((end - datetime.now(timezone.utc)).total_seconds(),
                               30 * 24 * 3600, delta=60)

    def test_shared_all_expired_inactive(self):
        # 每个账号的 VIP 都已过期 → 全员未解锁（无一生效则共享失效）
        with mock.patch.object(vip_mod, "_save_state"):
            vip_mod._set_user_vip("alice", "month", timedelta(days=-1))
            vip_mod._set_user_vip("bob", "month", timedelta(days=-1))
        self.assertFalse(vip_mod.get_vip("alice")["vip"])
        self.assertFalse(vip_mod.get_vip("bob")["vip"])

    def test_stack_accumulates_unbounded(self):
        # 无限叠加：连续激活 3 张月卡，共享截止应逐次累加 ≈ now + 90 天（不封顶）
        with mock.patch.object(vip_mod, "_call_license", return_value={
            "ok": True, "type": "month", "duration_seconds": 30 * 24 * 3600,
        }):
            s = None
            for code in ("CODE-1", "CODE-2", "CODE-3"):
                s = vip_mod.activate_vip("alice", code)
        end = datetime.fromisoformat(s["vip_until"])
        self.assertAlmostEqual((end - datetime.now(timezone.utc)).total_seconds(),
                               90 * 24 * 3600, delta=120)
        # 换账号激活同样累加在共享截止之上，不吞已生效时长（≈ now + 120 天）
        with mock.patch.object(vip_mod, "_call_license", return_value={
            "ok": True, "type": "month", "duration_seconds": 30 * 24 * 3600,
        }):
            s2 = vip_mod.activate_vip("bob", "CODE-4")
        end2 = datetime.fromisoformat(s2["vip_until"])
        self.assertAlmostEqual((end2 - datetime.now(timezone.utc)).total_seconds(),
                               120 * 24 * 3600, delta=120)


if __name__ == "__main__":
    unittest.main(verbosity=2)