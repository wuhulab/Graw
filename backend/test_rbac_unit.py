"""RBAC 模块级权限（受限管理员）单元测试（不依赖运行中的后端服务）。

覆盖：
  - user_perms 的兼容语义：字段缺失 / 显式 null = 全量；非管理员无权限；
    非法类型取最严（空集合）；未知模块 key 被忽略；
  - has_perm：全量放行 / 白名单命中 / 多模块任一 / 未授权拒绝；
  - require_perm：依赖工厂的 403 行为与 __perm_modules__ 元数据（供 Agent 代理映射）；
  - require_perm_ws：命名与工厂可创建（WebSocket 交互由端到端测试覆盖）；
  - routers/auth._normalize_perms：入参校验（未知模块 / 类型错误 → 400）；
  - routers/auth 的超级管理员兜底：任何变更后必须保留至少一个「完整权限超级管理员」
    （受限管理员不计入），阻止把最后一个兜底账号降级 / 收窄 / 删除；
  - _public_user：perms 随用户对象下发（前端 hasPerm 依赖它）；
  - main 的代理前置判定：_proxy_perm_for_path / _proxy_readonly_allow / 启动自检。

用法：
  python test_rbac_unit.py          # 直接运行
  pytest test_rbac_unit.py -q       # 或经 pytest（与其他测试同进程时需注意全局状态隔离）
"""
import asyncio
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import HTTPException  # noqa: E402

from app.auth import (  # noqa: E402
    MODULES,
    user_perms,
    has_perm,
    require_perm,
    require_perm_ws,
    _public_user,
)
from app.routers.auth import _normalize_perms, UpdateUserRequest  # noqa: E402

# 不传 perms 表示字段缺失（升级前的存量管理员）
_UNSET = object()


def _admin(perms=_UNSET):
    """构造管理员用户对象；perms=_UNSET 时省略该字段。"""
    user = {"username": "ops", "role": "admin"}
    if perms is not _UNSET:
        user["perms"] = perms
    return user


class TestUserPerms(unittest.TestCase):
    """user_perms：白名单解析与兼容语义。"""

    def test_non_admin_has_no_module_perms(self):
        self.assertEqual(user_perms({"username": "u", "role": "user"}), set())
        self.assertEqual(user_perms({"username": "u"}), set())          # 无 role 视为非管理员
        self.assertEqual(user_perms(None), set())                       # 异常入参容错

    def test_missing_field_means_full(self):
        """存量账号（无 perms 字段）升级后行为不变：全量权限。"""
        self.assertIsNone(user_perms(_admin()))

    def test_explicit_null_means_full(self):
        self.assertIsNone(user_perms(_admin(None)))

    def test_whitelist_filters_unknown_and_non_str(self):
        self.assertEqual(user_perms(_admin(["docker", "nope", 1])), {"docker"})

    def test_empty_list_means_nothing(self):
        self.assertEqual(user_perms(_admin([])), set())

    def test_invalid_type_takes_strictest(self):
        """类型非法（配置被改坏）→ 空集合，避免意外放大权限。"""
        self.assertEqual(user_perms(_admin("docker")), set())
        self.assertEqual(user_perms(_admin({"docker": True})), set())

    def test_all_declared_modules_accepted(self):
        self.assertEqual(user_perms(_admin(list(MODULES))), set(MODULES))


class TestHasPerm(unittest.TestCase):
    """has_perm：单模块 / 多模块任一判定。"""

    def test_full_admin_passes_any_module(self):
        self.assertTrue(has_perm(_admin(), "docker"))
        self.assertTrue(has_perm(_admin(None), "docker"))

    def test_restricted_admin_only_listed_modules(self):
        u = _admin(["logs", "sites"])
        self.assertTrue(has_perm(u, "logs"))
        self.assertTrue(has_perm(u, "sites"))
        self.assertFalse(has_perm(u, "docker"))

    def test_multiple_modules_any_hit(self):
        u = _admin(["sites"])
        self.assertTrue(has_perm(u, "firewall", "sites"))    # 聚合窗口：任一命中即放行
        self.assertFalse(has_perm(u, "firewall", "backup"))

    def test_non_admin_always_false(self):
        self.assertFalse(has_perm({"username": "u", "role": "user"}, "logs"))


class TestRequirePerm(unittest.TestCase):
    """require_perm：依赖工厂行为（直接调用依赖函数，不经 HTTP 栈）。"""

    def test_full_admin_allowed(self):
        dep = require_perm("docker")
        got = asyncio.run(dep(user=_admin(None)))
        self.assertEqual(got["username"], "ops")

    def test_whitelisted_module_allowed(self):
        dep = require_perm("docker", "files")
        got = asyncio.run(dep(user=_admin(["files"])))
        self.assertEqual(got["username"], "ops")

    def test_unauthorized_module_403(self):
        dep = require_perm("docker")
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(dep(user=_admin(["logs"])))
        self.assertEqual(ctx.exception.status_code, 403)

    def test_non_admin_403(self):
        dep = require_perm("logs")
        with self.assertRaises(HTTPException) as ctx:
            asyncio.run(dep(user={"username": "u", "role": "user"}))
        self.assertEqual(ctx.exception.status_code, 403)

    def test_dep_carries_module_meta(self):
        """__perm_modules__ 供 main 的启动自检与 Agent 代理映射读取。"""
        dep = require_perm("docker", "files")
        self.assertEqual(dep.__perm_modules__, ("docker", "files"))
        self.assertEqual(dep.__name__, "require_perm_docker_files")

    def test_ws_factory_naming(self):
        dep = require_perm_ws("terminal")
        self.assertEqual(dep.__name__, "require_perm_ws_terminal")
        self.assertTrue(callable(dep))


class TestNormalizePerms(unittest.TestCase):
    """_normalize_perms：用户管理接口的入参校验与规整。"""

    def test_none_returns_none(self):
        self.assertIsNone(_normalize_perms(None))

    def test_order_follows_modules_declaration(self):
        got = _normalize_perms(["logs", "docker", "logs"])
        self.assertEqual(got, [m for m in MODULES if m in {"docker", "logs"}])

    def test_empty_list_kept(self):
        self.assertEqual(_normalize_perms([]), [])

    def test_unknown_key_400(self):
        with self.assertRaises(HTTPException) as ctx:
            _normalize_perms(["docker", "nope"])
        self.assertEqual(ctx.exception.status_code, 400)

    def test_non_list_400(self):
        with self.assertRaises(HTTPException) as ctx:
            _normalize_perms("docker")
        self.assertEqual(ctx.exception.status_code, 400)


class TestPublicUser(unittest.TestCase):
    """_public_user：perms 随用户对象下发（前端 store/auth.hasPerm 依赖）。"""

    def test_perms_exposed(self):
        pu = _public_user(_admin(["logs"]))
        self.assertEqual(pu["perms"], ["logs"])
        self.assertNotIn("password", pu)          # 仍然脱敏

    def test_missing_perms_becomes_none(self):
        """缺字段的存量账号对外表现为 null（前端据此走全量兼容分支）。"""
        self.assertIsNone(_public_user(_admin())["perms"])


class TestProxyGuard(unittest.TestCase):
    """main 的 Agent 代理前置判定（导入 app.main 会构建完整应用与权限映射）。"""

    @classmethod
    def setUpClass(cls):
        from app import main as main_mod
        cls.main = main_mod

    def test_perm_map_covers_module_routes(self):
        m = self.main._proxy_perm_map()
        self.assertEqual(m.get("/api/docker"), ("docker",))
        self.assertEqual(m.get("/api/backup"), ("backup",))
        self.assertEqual(m.get("/api/terminal"), ("terminal",))
        self.assertGreaterEqual(len(m), 40)       # 覆盖全部模块授权前缀

    def test_perm_lookup_by_path(self):
        self.assertEqual(self.main._proxy_perm_for_path("/api/docker/containers/x"), ("docker",))
        self.assertEqual(self.main._proxy_perm_for_path("/api/health"), ())   # 非模块路由

    def test_readonly_allowlist(self):
        self.assertTrue(self.main._proxy_readonly_allow("/api/system/info", "GET"))
        self.assertFalse(self.main._proxy_readonly_allow("/api/system/info", "POST"))
        self.assertFalse(self.main._proxy_readonly_allow("/api/notes", "POST"))
        self.assertFalse(self.main._proxy_readonly_allow("/api/docker/containers", "GET"))

    def test_full_admin_prefixes_are_panel_boundary(self):
        """面板自身安全边界前缀（用户管理/节点/插件/更新等）必须登记在豁免清单中。"""
        for prefix in ("/api/auth", "/api/nodes", "/api/plugins", "/api/update", "/api/panelbackup"):
            self.assertIn(prefix, self.main._FULL_ADMIN_PREFIXES)

    def test_audit_route_perms_runs(self):
        """自检不得抛异常（有漏网的 require_admin 路由时只打 warning）。"""
        self.main._audit_route_perms()


class TestSuperAdminGuard(unittest.TestCase):
    """超级管理员兜底约束：任何变更后必须保留至少一个「完整权限超级管理员」。

    受限管理员（perms 为数组）已被模块白名单收窄，无法执行用户管理 / 插件 / 更新等
    面板自身操作，因此不作为兜底账号；把最后一个兜底账号降级 / 收窄 / 删除必须被拒。
    """

    def setUp(self):
        from app.routers import auth as auth_mod
        self.auth = auth_mod

    def _run(self, users, coro_factory, extra_patches=()):
        """在 mock 掉文件读写 / 审计日志的环境下执行接口协程。"""
        patches = [
            mock.patch.object(self.auth, "_load_users", lambda: users),
            mock.patch.object(self.auth, "_save_users", lambda u: None),
            mock.patch.object(self.auth, "get_client_ip", lambda req: "127.0.0.1"),
            mock.patch.object(self.auth.auditlog, "record", lambda *a, **k: None),
        ]
        patches.extend(extra_patches)
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        return asyncio.run(coro_factory())

    def test_count_only_full_admins(self):
        users = {
            "root": {"role": "admin"},                        # 完整管理员（字段缺失）
            "ops": {"role": "admin", "perms": None},          # 完整管理员（显式 null）
            "web": {"role": "admin", "perms": ["sites"]},     # 受限管理员 → 不计入
            "bob": {"role": "user", "perms": None},           # 普通用户 → 不计入
        }
        self.assertEqual(self.auth._super_admin_count(users), 2)

    def test_update_blocks_demoting_last_super_admin(self):
        users = {"root": {"role": "admin", "perms": None},
                 "web": {"role": "admin", "perms": ["sites"]}}
        req = UpdateUserRequest(role="user")
        with self.assertRaises(HTTPException) as ctx:
            self._run(users, lambda: self.auth.update_user("root", req, None, admin={"username": "other"}))
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("超级管理员", ctx.exception.detail)

    def test_update_blocks_narrowing_last_super_admin(self):
        users = {"root": {"role": "admin", "perms": None}}
        req = UpdateUserRequest(perms=["docker"])
        with self.assertRaises(HTTPException) as ctx:
            self._run(users, lambda: self.auth.update_user("root", req, None, admin={"username": "other"}))
        self.assertEqual(ctx.exception.status_code, 400)

    def test_update_allows_demoting_when_another_super_admin_remains(self):
        """存在第二个兜底管理员时，收窄其中一个应放行。"""
        users = {"root": {"role": "admin", "perms": None}, "ops": {"role": "admin"}}
        req = UpdateUserRequest(perms=["docker"])
        self._run(users, lambda: self.auth.update_user("root", req, None, admin={"username": "other"}))
        self.assertEqual(users["root"]["perms"], ["docker"])   # 已生效
        self.assertEqual(self.auth._super_admin_count(users), 1)

    def test_delete_blocks_last_super_admin(self):
        users = {"root": {"role": "admin", "perms": None}}
        with self.assertRaises(HTTPException) as ctx:
            self._run(users, lambda: self.auth.delete_user("root", None, user={"username": "other"}))
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("root", users)                            # 未被删除

    def test_delete_allows_restricted_admin(self):
        """受限管理员不是兜底账号，删除不受影响。"""
        users = {"root": {"role": "admin", "perms": None},
                 "web": {"role": "admin", "perms": ["sites"]}}
        self._run(users, lambda: self.auth.delete_user("web", None, user={"username": "root"}))
        self.assertNotIn("web", users)


if __name__ == "__main__":
    unittest.main(verbosity=2)