"""Web 服务器引擎（NGINX / OpenResty）模式模块单元测试（无需后端运行）。

覆盖：
  - 模式持久化（缺省 nginx / set_mode 校验与落盘）
  - 二进制名 / 路径解析（nginx 与 openresty 差异）
  - available / reload / nginx_like_available（mock 宿主机探测与命令）
  - webmode 路由：非法模式 400、合法模式切换成功

运行：python -m test_webmode_unit
依赖：仅标准库 + FastAPI/pydantic（pytest 非必需，用自带的简单 runner）
"""

import asyncio
import os
import tempfile
import unittest
from unittest import mock

import app.webserver as webserver
from app.routers import webmode


def _isolate(case: unittest.TestCase):
    """把 webserver 的存储路径重定向到临时目录，避免污染真实配置。"""
    case.tmpdir = tempfile.mkdtemp(prefix="webmode_")
    case.data_dir = os.path.join(case.tmpdir, "data")
    os.makedirs(case.data_dir, exist_ok=True)
    patchers = [
        mock.patch.object(webserver, "DATA_DIR", case.data_dir),
        mock.patch.object(
            webserver, "CONFIG_FILE", os.path.join(case.data_dir, "webserver.json")
        ),
    ]
    for p in patchers:
        p.start()
        case._patchers = getattr(case, "_patchers", []) + [p]
    case.addCleanup(_stop_all, case)

    # 保证从缺省 nginx 开始，避免继承真实环境残留配置
    if os.path.exists(webserver.CONFIG_FILE):
        os.remove(webserver.CONFIG_FILE)


def _stop_all(case: unittest.TestCase):
    for p in getattr(case, "_patchers", []):
        p.stop()


class ModePresistTest(unittest.TestCase):
    def setUp(self):
        _isolate(self)

    def test_default_mode_is_nginx(self):
        # 无配置时默认 nginx（安全缺省）
        self.assertEqual(webserver.get_mode(), "nginx")
        self.assertFalse(webserver.is_openresty())
        self.assertEqual(webserver.binary(), "nginx")

    def test_set_mode_openresty_roundtrip(self):
        # 合法模式可写可读
        webserver.set_mode("openresty")
        self.assertEqual(webserver.get_mode(), "openresty")
        self.assertTrue(webserver.is_openresty())
        self.assertEqual(webserver.binary(), "openresty")
        # 配置文件确实落盘
        self.assertTrue(os.path.exists(webserver.CONFIG_FILE))

    def test_set_mode_invalid_raises(self):
        # 非法模式拒绝，且不污染已有配置
        with self.assertRaises(ValueError):
            webserver.set_mode("apache")
        with self.assertRaises(ValueError):
            webserver.set_mode("nginx; reload")
        self.assertEqual(webserver.get_mode(), "nginx")

    def test_mode_case_insensitive_trim(self):
        # 大小写/空白容忍
        webserver.set_mode("  OpenResty ")
        self.assertEqual(webserver.get_mode(), "openresty")


class PathResolutionTest(unittest.TestCase):
    def setUp(self):
        _isolate(self)

    def test_nginx_paths(self):
        # nginx 模式：/etc/nginx 系列路径
        webserver.set_mode("nginx")
        self.assertEqual(webserver.base_dir(), "/etc/nginx")
        self.assertEqual(webserver.available_dir(), "/etc/nginx/sites-available")
        self.assertEqual(webserver.enabled_dir(), "/etc/nginx/sites-enabled")
        self.assertEqual(webserver.conf_path(), "/etc/nginx/nginx.conf")
        self.assertEqual(webserver.stream_dir(), "/etc/nginx/stream-enabled")
        self.assertEqual(
            webserver.stream_include(),
            "include /etc/nginx/stream-enabled/*.conf;",
        )
        self.assertEqual(webserver.waf_dir(), "/etc/nginx/waf")

    def test_openresty_paths(self):
        # OpenResty 模式：/usr/local/openresty 前缀
        webserver.set_mode("openresty")
        self.assertEqual(
            webserver.available_dir(),
            "/usr/local/openresty/nginx/conf/sites-available",
        )
        self.assertEqual(
            webserver.stream_include(),
            "include /usr/local/openresty/nginx/conf/stream-enabled/*.conf;",
        )
        self.assertEqual(
            webserver.waf_dir(), "/usr/local/openresty/nginx/conf/waf"
        )


class AvailabilityTest(unittest.TestCase):
    def setUp(self):
        _isolate(self)

    def test_available_via_which(self):
        # host_which 命中即视为可用
        with mock.patch.object(webserver, "host_which", return_value="/usr/bin/nginx"):
            webserver.set_mode("nginx")
            self.assertTrue(webserver.available())

    def test_available_via_version_probe(self):
        # which 未命中时回退 -v 探测（成功 returncode=0）
        with mock.patch.object(webserver, "host_which", return_value=None):
            with mock.patch.object(
                webserver, "host_cmd", return_value=mock.Mock(returncode=0)
            ) as m:
                self.assertTrue(webserver.available())
                # 确认探测的是当前引擎二进制
                self.assertEqual(m.call_args[0][0], ["nginx", "-v"])

    def test_available_not_found(self):
        # 二进制缺失 -> False，不抛异常
        with mock.patch.object(webserver, "host_which", return_value=None):
            with mock.patch.object(
                webserver, "host_cmd", return_value=mock.Mock(returncode=127)
            ):
                self.assertFalse(webserver.available())

    def test_reload_uses_current_binary(self):
        # reload 按当前引擎调用；成功返回 True
        webserver.set_mode("openresty")
        with mock.patch.object(
            webserver, "host_cmd", return_value=mock.Mock(returncode=0)
        ) as m:
            self.assertTrue(webserver.reload())
            self.assertEqual(m.call_args[0][0], ["openresty", "-s", "reload"])

    def test_reload_failure_returns_false(self):
        # reload 失败不抛异常，返回 False
        webserver.set_mode("nginx")
        with mock.patch.object(
            webserver, "host_cmd", return_value=mock.Mock(returncode=1)
        ):
            self.assertFalse(webserver.reload())

    def test_nginx_like_available_either(self):
        # 任一引擎可用即视为 nginx 系（openresty 也可）
        with mock.patch.object(
            webserver, "available", side_effect=lambda eng: eng == "openresty"
        ):
            self.assertTrue(webserver.nginx_like_available())


class WebModeRouterTest(unittest.TestCase):
    def setUp(self):
        _isolate(self)

    def _run(self, coro):
        return asyncio.new_event_loop().run_until_complete(coro)

    def test_status_reflects_mode(self):
        # 状态接口返回模式、二进制与目录
        webserver.set_mode("openresty")
        s = self._run(webmode.webmode_status())
        self.assertEqual(s["mode"], "openresty")
        self.assertEqual(s["binary"], "openresty")
        self.assertIn("conf_base", s)
        self.assertIn("nginx_available", s)
        self.assertIn("openresty_available", s)

    def test_set_mode_invalid_returns_400(self):
        # 非法模式 -> HTTP 400
        from fastapi import HTTPException

        with self.assertRaises(HTTPException) as cm:
            self._run(webmode.webmode_set_mode(webmode.ModeBody(mode="iis")))
        self.assertEqual(cm.exception.status_code, 400)

    def test_set_mode_valid(self):
        # 合法切换返回新模式
        res = self._run(webmode.webmode_set_mode(webmode.ModeBody(mode="openresty")))
        self.assertEqual(res["mode"], "openresty")


if __name__ == "__main__":
    unittest.main(verbosity=2)