# -*- coding: utf-8 -*-
"""
test_sites_discovery.py - 网站「真实站点发现」单元测试
覆盖：
  - server_name / root 从 nginx 配置的解析（含 location 块剔除）
  - 外部站点 id 生成（避免与自建冲突）
  - _existing_site_dirs 含 1Panel 目录
  - _discover_existing_sites 在 mock 目录下识别 .conf 站点
"""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from app.routers import sites  # noqa: E402

# 1Panel 风格真实配置：含 location 块内 root + server 块 root
SAMPLE_CONF = """server {
    listen 80 ;
    server_name graw-test-1p.shunx.top;
    root /www/sites/graw-test-1p.shunx.top/index;
    location ^~ /.well-known/acme-challenge {
        allow all;
        root /usr/share/nginx/html;
    }
    include /www/sites/graw-test-1p.shunx.top/proxy/*.conf;
}
"""

# 纯反向代理配置：根路径 location / 转发到后端
PROXY_CONF = """server {
    listen 15874 ;
    server_name graw-proxy.shunx.top;
    location / {
        proxy_pass http://127.0.0.1:15874;
        proxy_set_header Host $host;
    }
}
"""

# 1Panel：主配置含 root，经 include 的方式挂载反代片段（location ^~ /）
MOD_CONF = """server {
    listen 15874 ;
    server_name graw-test-1p.shunx.top;
    root /www/sites/graw-test-1p.shunx.top/index;
    include /www/sites/graw-test-1p.shunx.top/proxy/*.conf;
}
"""
MOD_PROXY_CONF = """location ^~ / {
    proxy_pass http://127.0.0.1:15874;
    proxy_set_header Host $host;
}
"""

# 静态站点只在子路径 /api 反代：不应整站判为反向代理
SUB_LOC_PROXY_CONF = """server {
    listen 8080;
    server_name mixed.shunx.top;
    root /srv/mixed;
    location /api {
        proxy_pass http://127.0.0.1:9999;
    }
}
"""


class NginxParseTest(unittest.TestCase):
    def test_server_name(self):
        self.assertEqual(sites._parse_server_name(SAMPLE_CONF), "graw-test-1p.shunx.top")

    def test_server_name_wildcard(self):
        self.assertEqual(sites._parse_server_name("server { server_name _; root /x; }"), "_")

    def test_root_excludes_location(self):
        # 应取 server 块 root，剔除 location 里的 /usr/share/nginx/html
        self.assertEqual(sites._parse_root_dir(SAMPLE_CONF), "/www/sites/graw-test-1p.shunx.top/index")

    def test_root_empty_on_no_root(self):
        self.assertEqual(sites._parse_root_dir("server { listen 80; }"), "")

    def test_listen_plain(self):
        self.assertEqual(sites._parse_listen("server { listen 80; }"), 80)

    def test_listen_host_port(self):
        self.assertEqual(sites._parse_listen("server { listen 127.0.0.1:8080; }"), 8080)

    def test_listen_ssl(self):
        self.assertEqual(sites._parse_listen("server { listen 443 ssl; }"), 443)

    def test_proxy_pass_root_location(self):
        self.assertEqual(sites._parse_proxy_pass(PROXY_CONF), "http://127.0.0.1:15874")

    def test_proxy_pass_modifier_root_location(self):
        # location ^~ / 带修饰符也应识别为根路径反代
        self.assertEqual(sites._parse_proxy_pass(MOD_PROXY_CONF), "http://127.0.0.1:15874")

    def test_resolve_site_conf_inlines_proxy(self):
        # 主配置 + include 的反代片段拼合后应识别为反向代理
        with tempfile.TemporaryDirectory() as d:
            site_dir = os.path.join(d, "graw-test-1p.shunx.top")
            os.makedirs(os.path.join(site_dir, "proxy"))
            with open(os.path.join(d, "run.conf"), "w", encoding="utf-8") as f:
                f.write(MOD_CONF.replace(
                    "/www/sites/graw-test-1p.shunx.top",
                    os.path.join(d, "graw-test-1p.shunx.top").replace("\\", "/"),
                ).replace(
                    "/www/sites/", d.replace("\\", "/")
                ))
            with open(os.path.join(site_dir, "proxy", "root.conf"), "w", encoding="utf-8") as f:
                f.write(MOD_PROXY_CONF)
            conf = sites._resolve_site_conf(os.path.join(d, "run.conf"))
        self.assertEqual(sites._parse_proxy_pass(conf), "http://127.0.0.1:15874")
        self.assertEqual(sites._parse_server_name(conf), "graw-test-1p.shunx.top")

    def test_proxy_pass_ignores_sub_location(self):
        # 仅子路径 /api 反代 → 不应判为整站反向代理
        self.assertEqual(sites._parse_proxy_pass(SUB_LOC_PROXY_CONF), "")

    def test_proxy_pass_missing(self):
        self.assertEqual(sites._parse_proxy_pass(SAMPLE_CONF), "")

    def test_ext_id(self):
        self.assertTrue(sites._ext_site_id("graw-test-1p.shunx.top", 0).startswith("ext-graw-test-1p"))

    def test_ext_id_sanitizes(self):
        sid = sites._ext_site_id("../BAD name", 1)
        self.assertNotIn("../", sid)
        self.assertNotIn(" ", sid)


class ExistingDirsTest(unittest.TestCase):
    def test_includes_1panel(self):
        dirs = sites._existing_site_dirs()
        self.assertIn("/opt/1panel/www/conf.d", [x["path"] for x in dirs])
        self.assertIn("1panel", [x.get("source") for x in dirs])

    def test_dirs_have_source(self):
        for d in sites._existing_site_dirs():
            self.assertIn(d.get("source"), ("nginx", "1panel"))


class DiscoverTest(unittest.TestCase):
    def _d(self, path):
        return [{"path": path, "source": "nginx"}]

    def test_discovers_conf_site(self):
        with tempfile.TemporaryDirectory() as d:
            conf = os.path.join(d, "graw-test.conf")
            with open(conf, "w", encoding="utf-8") as f:
                f.write(SAMPLE_CONF)
            with mock.patch.object(sites, "_existing_site_dirs", return_value=self._d(d)):
                found = sites._discover_existing_sites()
        self.assertEqual(len(found), 1)
        item = found[0]
        self.assertTrue(item["external"])
        self.assertEqual(item["name"], "graw-test-1p.shunx.top")
        self.assertEqual(item["domains"], ["graw-test-1p.shunx.top"])
        self.assertTrue(item["root"].endswith("index"))
        self.assertEqual(item["config_file"], conf)
        self.assertEqual(item["source"], "nginx")

    def test_discovers_1panel_source(self):
        # 来自 1Panel 目录的站点应标记 source=1panel
        with tempfile.TemporaryDirectory() as d:
            conf = os.path.join(d, "graw-test.conf")
            with open(conf, "w", encoding="utf-8") as f:
                f.write(SAMPLE_CONF)
            with mock.patch.object(
                sites, "_existing_site_dirs",
                return_value=[{"path": d, "source": "1panel"}],
            ):
                found = sites._discover_existing_sites()
        self.assertEqual(found[0]["source"], "1panel")

    def test_skips_non_conf(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "readme.txt"), "w").close()
            with mock.patch.object(sites, "_existing_site_dirs", return_value=self._d(d)):
                found = sites._discover_existing_sites()
        self.assertEqual(found, [])

    def test_discovers_proxy_site(self):
        with tempfile.TemporaryDirectory() as d:
            conf = os.path.join(d, "proxy.conf")
            with open(conf, "w", encoding="utf-8") as f:
                f.write(PROXY_CONF)
            with mock.patch.object(sites, "_existing_site_dirs", return_value=self._d(d)):
                found = sites._discover_existing_sites()
        self.assertEqual(len(found), 1)
        item = found[0]
        self.assertEqual(item["type"], "proxy")
        self.assertEqual(item["reverse_proxy"], "http://127.0.0.1:15874")
        self.assertEqual(item["port"], 15874)
        self.assertEqual(item["domains"], ["graw-proxy.shunx.top"])


class EditExternalTest(unittest.TestCase):
    def test_find_external_site_by_id(self):
        with tempfile.TemporaryDirectory() as d:
            conf = os.path.join(d, "proxy.conf")
            with open(conf, "w", encoding="utf-8") as f:
                f.write(PROXY_CONF)
            with mock.patch.object(
                sites, "_existing_site_dirs",
                return_value=[{"path": d, "source": "nginx"}],
            ):
                ext = sites._discover_existing_sites()[0]
                found = sites._find_external_site(ext["id"])
        self.assertIsNotNone(found)
        self.assertEqual(found["id"], ext["id"])
        self.assertEqual(found["reverse_proxy"], "http://127.0.0.1:15874")

    def test_nginx_config_external_proxy(self):
        # 外部反代站点经 _nginx_site_config 生成的配置应含 proxy_pass
        site = {
            "id": "ext-x", "type": "proxy", "domains": ["graw-proxy.shunx.top"],
            "port": 80, "reverse_proxy": "http://127.0.0.1:15874",
            "ssl": {}, "root": "", "locations": [],
        }
        cfg = sites._nginx_site_config(site)
        self.assertIn("proxy_pass http://127.0.0.1:15874", cfg)

    def test_nginx_config_external_static(self):
        site = {
            "id": "ext-s", "type": "static", "domains": ["a.example.com"],
            "port": 80, "reverse_proxy": "", "ssl": {},
            "root": "/www/sites/a.example.com/index", "locations": [],
        }
        cfg = sites._nginx_site_config(site)
        self.assertIn("root /www/sites/a.example.com/index", cfg)


class ProxySslOptionsTest(unittest.TestCase):
    """反向代理上游为 https 时的 SSL 指令生成（修复宝塔等面板 502）。"""

    def test_http_upstream_no_extra_ssl_opts(self):
        # 明文 http 上游：不额外生成 SSL 指令，保持原有行为
        self.assertEqual(sites._proxy_ssl_options("http://127.0.0.1:8080"), [])

    def test_empty_proxy_no_opts(self):
        self.assertEqual(sites._proxy_ssl_options(""), [])

    def test_https_upstream_adds_verify_off(self):
        opts = sites._proxy_ssl_options("https://127.0.0.1:12840")
        self.assertIn("        proxy_ssl_verify off;", opts)
        self.assertIn("        proxy_ssl_server_name off;", opts)

    def test_https_uppercase_scheme_handled(self):
        opts = sites._proxy_ssl_options("HTTPS://pan.local")
        self.assertIn("        proxy_ssl_verify off;", opts)

    def test_nginx_proxy_config_ends_with_https_opts(self):
        # https 上游的整站反代配置应包含关闭证书校验指令，避免 502
        site = {
            "id": "ext-https", "type": "proxy", "domains": ["pan.example.com"],
            "port": 80, "reverse_proxy": "https://127.0.0.1:12840",
            "ssl": {}, "root": "", "locations": [],
        }
        cfg = sites._nginx_site_config(site)
        self.assertIn("proxy_pass https://127.0.0.1:12840;", cfg)
        self.assertIn("proxy_ssl_verify off;", cfg)


if __name__ == "__main__":
    unittest.main()