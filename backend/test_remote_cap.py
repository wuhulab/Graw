# -*- coding: utf-8 -*-
"""
test_remote_cap.py - 远端能力门控单元测试
覆盖 remote_cap 的 local 类路径判定，以及 main 中间件在远端节点下拦截 local 接口、
放行 host 类接口的逻辑（通过 FastAPI TestClient）。
"""
import os
import sys
import unittest
import unittest.mock as mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from app import remote_cap  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


class LocalPathTest(unittest.TestCase):
    def test_local_class_paths(self):
        """sites / databases / cron / ssl 等本地面板管理项应命中 local。"""
        for p in (
            "/api/sites",
            "/api/sites/list",
            "/api/databases/connections",
            "/api/cron/list",
            "/api/ssl/list",
            "/api/backup/tasks",
            "/api/appstore/index",
            "/api/waf/status",
            "/api/notify/channels",
            "/api/uptime/items",
            "/api/webstats/logs",
        ):
            self.assertTrue(remote_cap.is_local_path(p), p)

    def test_host_class_paths_not_local(self):
        """进程 / 文件 / Docker / 磁盘 / 日志 / 终端 / 系统监控等 host 类不应命中 local。"""
        for p in (
            "/api/process/list",
            "/api/files/list",
            "/api/docker/status",
            "/api/disks/list",
            "/api/logs/list",
            "/api/terminal",
            "/api/system/overview",
            "/api/svcmonitor/items",
            "/api/firewall/status",
            "/api/healthcheck/run",
        ):
            self.assertFalse(remote_cap.is_local_path(p), p)

    def test_non_api_and_prefix_safety(self):
        """非 local 前缀与「同前缀但不同模块」不应误伤。"""
        # nodes / system / auth 永远放行
        self.assertFalse(remote_cap.is_local_path("/api/nodes"))
        self.assertFalse(remote_cap.is_local_path("/api/system/ws"))
        self.assertFalse(remote_cap.is_local_path("/api/auth/me"))
        # 精确前缀：加斜杠才命中，避免 /api/sites2 误判
        self.assertFalse(remote_cap.is_local_path("/api/sites2/list"))


class MainMiddlewareTest(unittest.TestCase):
    def setUp(self):
        from app.main import app
        self.client = TestClient(app)
        # 覆盖后端的 /api/health 等公开接口依赖登录，这里直接用未登录态测试中间件拦截：
        # 中间件在鉴权前就返回，故无需登录 token。

    def test_local_blocked_when_remote(self):
        """远端节点下访问 sites 应被 403 拦截。"""
        with mock.patch.object(remote_cap.node_manager, "is_remote", return_value=True):
            r = self.client.get("/api/sites/list")
        self.assertEqual(r.status_code, 403)
        self.assertIn("仅本机", r.json().get("detail", ""))

    def test_host_allowed_when_remote(self):
        """远端节点下访问 host 类接口应放行（后续由路由自身鉴权处理）。"""
        with mock.patch.object(remote_cap.node_manager, "is_remote", return_value=True):
            # 不命中 local 中间件，进入路由鉴权 => 未登录 401（而非 403）
            r = self.client.get("/api/process/list")
        self.assertEqual(r.status_code, 401)

    def test_local_allowed_when_not_remote(self):
        """本机节点下访问 local 类应按原逻辑处理（进入鉴权 => 401，不被拦截）。"""
        with mock.patch.object(remote_cap.node_manager, "is_remote", return_value=False):
            r = self.client.get("/api/sites/list")
        self.assertEqual(r.status_code, 401)


if __name__ == "__main__":
    unittest.main()