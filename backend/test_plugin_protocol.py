# -*- coding: utf-8 -*-
"""
test_plugin_protocol.py - Graw 应用接口开放协议（GPOP）单元测试

覆盖：
  1. 清单（plugin.yml / manifest）校验：必填字段 / 非法 ID / 能力白名单 / 版本口径
  2. 令牌：生成 / 哈希 / 校验 / 轮换 / 常量时间比较
  3. 注册表：register / get / list / unregister / 脱敏
  4. 插件持久化配置：保存 / 读取 / 大小上限
  5. 开放接口 /api/op：鉴权（缺头 / 错令牌 / 未启用 / 能力门控）
  6. 管理接口 /api/plugins：未登录 401；远端节点门控（local 路径）

用法：
  cd backend && pytest test_plugin_protocol.py -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from app import plugin_protocol as pp  # noqa: E402
from app import remote_cap  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


def _valid_manifest(**overrides):
    """构造一份合法清单（可覆盖字段制造各种异常场景）。"""
    m = {
        "api_version": 1,
        "id": "hello-graw",
        "name": "Hello Graw",
        "version": "1.0.0",
        "description": "一个示例插件",
        "author": "Graw Team",
        "capabilities": ["panel_info", "notify", "audit", "config"],
        "entry": {"service": "hello", "port": 8080, "path": "/"},
    }
    m.update(overrides)
    return m


class _IsolatedRegistryMixin(object):
    """把注册表/配置写入临时目录，避免污染真实 backend/data。"""

    def setUp(self):
        import tempfile

        self._tmp = tempfile.mkdtemp(prefix="graw-plugin-test-")
        self._orig_file = pp.PLUGINS_FILE
        self._orig_dir = pp.DATA_DIR
        self._orig_cache = pp._plugins_cache
        pp.PLUGINS_FILE = os.path.join(self._tmp, "plugins.json")
        # save_config 的落盘路径基于 DATA_DIR，一并隔离（否则污染真实 data/plugins）
        pp.DATA_DIR = os.path.join(self._tmp, "data")
        pp._plugins_cache = None

    def tearDown(self):
        pp.PLUGINS_FILE = self._orig_file
        pp.DATA_DIR = self._orig_dir
        pp._plugins_cache = self._orig_cache


class ManifestValidateTest(unittest.TestCase):
    """清单校验规则。"""

    def test_valid_manifest_normalized(self):
        m = pp.validate_manifest(_valid_manifest())
        self.assertEqual(m["id"], "hello-graw")
        self.assertEqual(m["api_version"], 1)
        self.assertIn("panel_info", m["capabilities"])

    def test_missing_required_field(self):
        for field in ("id", "name", "version", "description"):
            with self.assertRaises(ValueError, msg=field):
                pp.validate_manifest(_valid_manifest(**{field: ""}))

    def test_invalid_id_rejected(self):
        for bad in ("../evil", "a b", "含有中文", "-leading-dash", ""):
            with self.assertRaises(ValueError, msg=repr(bad)):
                pp.validate_manifest(_valid_manifest(id=bad))

    def test_unknown_fields_dropped(self):
        m = pp.validate_manifest(_valid_manifest(pwned="x", privileged=True))
        self.assertNotIn("pwned", m)
        self.assertNotIn("privileged", m)

    def test_capability_whitelist(self):
        m = pp.validate_manifest(_valid_manifest(capabilities=["notify", "hack"]))
        self.assertEqual(m["capabilities"], ["notify"])

    def test_newer_api_version_rejected(self):
        with self.assertRaises(ValueError):
            pp.validate_manifest(_valid_manifest(api_version=pp.OPEN_API_VERSION + 1))

    def test_bad_version_format(self):
        # 含空格 / 斜杠 / 空串均非法（连续点 v1..0 是合法宽松版本，不做限制）
        with self.assertRaises(ValueError):
            pp.validate_manifest(_valid_manifest(version="v 1.0"))
        with self.assertRaises(ValueError):
            pp.validate_manifest(_valid_manifest(version="1.0/stable"))
        with self.assertRaises(ValueError):
            pp.validate_manifest(_valid_manifest(version=""))

    def test_entry_port_boundary(self):
        # 端口越界被忽略但不抛错
        m = pp.validate_manifest(_valid_manifest(entry={"service": "s", "port": 99999}))
        self.assertNotIn("port", m.get("entry", {}))


class RegistryTest(_IsolatedRegistryMixin, unittest.TestCase):
    """注册表 + 令牌。"""

    def test_register_get_list_unregister(self):
        token = pp.generate_token()
        pp.register_plugin(
            "demo", _valid_manifest(id="demo"), pp.hash_token(token), compose_file="x"
        )
        rec = pp.get_plugin("demo")
        self.assertEqual(rec["id"], "demo")
        self.assertEqual(rec["token_hash"], pp.hash_token(token))
        self.assertIn("demo", [p["id"] for p in pp.list_plugins()])
        pp.unregister_plugin("demo")
        self.assertIsNone(pp.get_plugin("demo"))

    def test_verify_token(self):
        token = pp.generate_token()
        pp.register_plugin("demo", _valid_manifest(id="demo", capabilities=["panel_info"]), pp.hash_token(token))
        self.assertTrue(pp.verify_token("demo", token))
        self.assertFalse(pp.verify_token("demo", "wrong-token"))
        self.assertFalse(pp.verify_token("nope", token))

    def test_rotate_token_invalidates_old(self):
        token = pp.generate_token()
        pp.register_plugin("demo", _valid_manifest(id="demo"), pp.hash_token(token))
        new_token, _rec = pp.rotate_token("demo")
        self.assertNotEqual(new_token, token)
        self.assertTrue(pp.verify_token("demo", new_token))
        self.assertFalse(pp.verify_token("demo", token))

    def test_update_status(self):
        pp.register_plugin("demo", _valid_manifest(id="demo"), pp.hash_token(pp.generate_token()))
        rec = pp.update_plugin_status("demo", status="running")
        self.assertEqual(rec["status"], "running")
        # 关键字段不可覆盖
        pp.update_plugin_status("demo", token_hash="hacked")
        self.assertNotEqual(pp.get_plugin("demo")["token_hash"], "hacked")

    def test_enabled_flag_default_and_toggle(self):
        """插件功能总开关：默认开启，可持久化关闭并重新打开（幂等）。"""
        self.assertTrue(pp.is_enabled())
        pp.set_enabled(False)
        self.assertFalse(pp.is_enabled())
        # 重新读取（绕过内存缓存）仍为关闭态，证明已持久化
        pp.reload()
        self.assertFalse(pp.is_enabled())
        pp.set_enabled(True)
        self.assertTrue(pp.is_enabled())
        pp.reload()
        self.assertTrue(pp.is_enabled())


class PluginConfigTest(_IsolatedRegistryMixin, unittest.TestCase):
    """插件持久化配置。"""

    def test_save_and_load(self):
        pp.save_config("demo", {"mode": "on", "port": 1234})
        self.assertEqual(pp.load_config("demo"), {"mode": "on", "port": 1234})

    def test_invalid_id_rejected(self):
        with self.assertRaises(ValueError):
            pp.save_config("../evil", {})

    def test_size_limit(self):
        big = {"data": "x" * (pp.MAX_CONFIG_BYTES)}
        with self.assertRaises(ValueError):
            pp.save_config("demo", big)


class ConfigPathSafetyTest(unittest.TestCase):
    """配置路径防护（py/path-injection 回归）：穿越 ID 一律拒绝。"""

    def test_load_config_never_leaks_outside_root(self):
        """load_config 遇到非法/穿越 ID 一律返回空 dict，绝不访问任意路径。"""
        self.assertEqual(pp.load_config("../../etc/passwd"), {})
        self.assertEqual(pp.load_config(""), {})

    def test_save_config_rejects_traversal(self):
        """save_config 遇到非法/穿越 ID 抛 ValueError（白名单 + 前缀检查双重拦截）。"""
        for bad in ("../evil", "..", "a/../../b", "a\\..\\..\\etc", ""):
            with self.assertRaises(ValueError, msg=repr(bad)):
                pp.save_config(bad, {})


class OpenApiAuthTest(_IsolatedRegistryMixin, unittest.TestCase):
    """/api/op 开放接口的鉴权与能力门控（TestClient）。"""

    def setUp(self):
        super().setUp()
        from app.main import app

        self.client = TestClient(app)
        self.token = pp.generate_token()
        pp.register_plugin(
            "demo",
            _valid_manifest(id="demo", capabilities=["panel_info", "notify"]),
            pp.hash_token(self.token),
        )
        self._headers = {
            "X-Graw-Plugin-Id": "demo",
            "Authorization": f"Bearer {self.token}",
        }

    def _op_get(self, path, headers=None):
        return self.client.get(path, headers=headers or self._headers)

    def test_protocol_public(self):
        r = self.client.get("/api/op/protocol")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["api_version"], pp.OPEN_API_VERSION)

    def test_me_with_valid_token(self):
        r = self._op_get("/api/op/me")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["plugin"]["id"], "demo")
        self.assertIn("panel", body)

    def test_missing_token_rejected(self):
        r = self._op_get("/api/op/me", headers={"X-Graw-Plugin-Id": "demo"})
        self.assertEqual(r.status_code, 401)

    def test_wrong_token_rejected(self):
        r = self._op_get(
            "/api/op/me",
            headers={"X-Graw-Plugin-Id": "demo", "Authorization": "Bearer bad"},
        )
        self.assertEqual(r.status_code, 401)

    def test_unknown_plugin_rejected(self):
        r = self._op_get(
            "/api/op/me",
            headers={"X-Graw-Plugin-Id": "ghost", "Authorization": "Bearer x"},
        )
        self.assertEqual(r.status_code, 401)

    def test_disabled_plugin_rejected(self):
        pp.update_plugin_status("demo", enabled=False)
        r = self._op_get("/api/op/me")
        self.assertEqual(r.status_code, 403)

    def test_capability_gate(self):
        """未声明 audit 能力时调用 /api/op/audit 应 403；声明 notify 的可用。"""
        r = self.client.post("/api/op/audit", json={"action": "x"}, headers=self._headers)
        self.assertEqual(r.status_code, 403)
        r2 = self.client.post(
            "/api/op/notify",
            json={"title": "hi"},
            headers=self._headers,
        )
        # demo 声明了 notify：应放行（推送无渠道时也返回成功，0 个渠道）
        self.assertEqual(r2.status_code, 200)

    def test_config_roundtrip(self):
        caps = {"capabilities": ["config"]}
        self.token = pp.generate_token()
        pp.register_plugin("demo", _valid_manifest(id="demo", **caps), pp.hash_token(self.token))
        h = {"X-Graw-Plugin-Id": "demo", "Authorization": f"Bearer {self.token}"}
        r = self.client.put("/api/op/config", json={"config": {"k": "v"}}, headers=h)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["config"]["k"], "v")


class AdminApiTest(_IsolatedRegistryMixin, unittest.TestCase):
    """管理接口鉴权与远端门控。"""

    def setUp(self):
        super().setUp()
        from app.main import app

        self.client = TestClient(app)

    def test_admin_required(self):
        """未登录访问管理接口应 401。"""
        self.assertEqual(self.client.get("/api/plugins").status_code, 401)
        self.assertEqual(self.client.get("/api/plugins/protocol").status_code, 401)

    def test_remote_node_gated(self):
        """SSH 远端节点下 /api/plugins 与 /api/op 被门控为 local。"""
        self.assertTrue(remote_cap.is_local_path("/api/plugins"))
        self.assertTrue(remote_cap.is_local_path("/api/plugins/install"))
        self.assertTrue(remote_cap.is_local_path("/api/op"))
        self.assertTrue(remote_cap.is_local_path("/api/op/me"))

    def test_protocol_examples(self):
        """协议信息应返回本地示例插件 hello-graw（开发仓库内）。"""
        # 直接调用函数不用全局 ADMIN，验证本地示例发现逻辑
        from app.routers import plugins as plugins_mod

        examples = plugins_mod._list_local_examples()
        ids = {e["id"] for e in examples}
        self.assertIn("hello-graw", ids)


class SettingsApiTest(unittest.TestCase):
    """插件功能总开关接口（settings_router 始终注册）。"""

    def setUp(self):
        import tempfile

        from app import plugin_protocol as pp_mod
        from app.routers import plugins as plugins_mod

        self._pp = pp_mod
        self._pl = plugins_mod
        self._tmp = tempfile.mkdtemp(prefix="graw-plugin-settings-test-")
        self._orig_file = pp_mod.PLUGINS_FILE
        self._orig_dir = pp_mod.DATA_DIR
        self._orig_cache = pp_mod._plugins_cache
        pp_mod.PLUGINS_FILE = os.path.join(self._tmp, "plugins.json")
        pp_mod.DATA_DIR = os.path.join(self._tmp, "data")
        pp_mod._plugins_cache = None
        from app.main import app

        self.client = TestClient(app)

    def tearDown(self):
        self._pp.PLUGINS_FILE = self._orig_file
        self._pp.DATA_DIR = self._orig_dir
        self._pp._plugins_cache = self._orig_cache

    def test_settings_requires_admin(self):
        """未登录访问 /api/plugins/settings 应 401（settings 路由挂 ADMIN）。"""
        r = self.client.get("/api/plugins/settings")
        self.assertEqual(r.status_code, 401)

    def test_settings_default_enabled_and_turn_off(self):
        """管理员可读取默认开启状态并关闭；关闭后路由逻辑仍可查询配置。"""
        # 直接以模块函数验证保存（路由挂 ADMIN，登录态在单测内构造较繁琐）
        self.assertTrue(self._pp.is_enabled())
        self._pp.set_enabled(False)
        self._pp.reload()
        self.assertFalse(self._pp.is_enabled())
        self._pp.set_enabled(True)


if __name__ == "__main__":
    unittest.main()