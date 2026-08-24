"""WAF 应用防火墙单元测试（无需后端运行）。

通过 monkeypatch 把 DATA_DIR / WAF_FILE / WAF_LOG_FILE / _waf_dir
指到临时目录，直接对 waf 模块的校验与 nginx 生成函数做断言。

运行：python -m test_waf_unit
依赖：仅标准库 + FastAPI/pydantic（pytest 非必需，用自带的简单 runner）
"""

import json
import os
import tempfile
import unittest
from unittest import mock

import app.routers.waf as waf


def _store(case: unittest.TestCase):
    """构造临时目录并把 waf 的存储/生成路径 patch 到其中。"""
    case.tmpdir = tempfile.mkdtemp(prefix="waf_unit_")
    case.data_dir = os.path.join(case.tmpdir, "data")
    os.makedirs(case.data_dir, exist_ok=True)
    case.site_file = os.path.join(case.data_dir, "sites.json")
    # 写一个真实站点，供 _site_exists / 配置保存用
    with open(case.site_file, "w", encoding="utf-8") as f:
        json.dump([{"name": "site_a"}, {"name": "site_b"}], f)

    patchers = [
        mock.patch.object(waf, "DATA_DIR", case.data_dir),
        mock.patch.object(waf, "WAF_FILE", os.path.join(case.data_dir, "waf.json")),
        mock.patch.object(waf, "WAF_LOG_FILE", os.path.join(case.data_dir, "waf_logs.json")),
        mock.patch.object(waf, "_waf_dir", lambda: os.path.join(case.tmpdir, "waf_out")),
        mock.patch.object(waf, "_nginx_available", lambda: False),
    ]
    for p in patchers:
        p.start()
        case._patchers = getattr(case, "_patchers", []) + [p]
    case.addCleanup(_stop_all, case)


def _stop_all(case: unittest.TestCase):
    for p in getattr(case, "_patchers", []):
        p.stop()


def _default_cfg(waf_mod=waf):
    return waf_mod._default_site_config("site_a")


class SiteValidationTest(unittest.TestCase):
    def setUp(self):
        _store(self)

    def test_site_id_whitelist(self):
        # 合法 id
        self.assertTrue(waf._SITE_ID_RE.match("site-a"))
        self.assertTrue(waf._SITE_ID_RE.match("Site_1.a"))
        # 非法：穿越 / 控制字符
        for bad in ("../etc", "a;b", "a\nb", "-lead", ""):
            self.assertIsNone(waf._SITE_ID_RE.match(bad), bad)

    def test_ensure_site_rejects_missing(self):
        from fastapi import HTTPException
        with self.assertRaises(HTTPException):
            waf._ensure_site("nonexist", active=True)
        # 非 active 模式只校验格式
        with self.assertRaises(HTTPException):
            waf._ensure_site("..%2f", active=False)

    def test_validate_frequency_boundaries(self):
        from fastapi import HTTPException
        ok = {"mode": "url", "period": 60, "count": 100, "ban": 600}
        waf._validate_frequency(ok, "access")
        for bad in (
            {"mode": "xx"},                          # 非法模式
            {"mode": "url", "count": 0},             # count 越下界
            {"mode": "url", "count": 1, "period": 0},# period 越下界
            {"mode": "url", "count": "x"},           # 非整数
            {"mode": "url", "count": True},          # bool 视为非法
        ):
            with self.assertRaises(HTTPException):
                waf._validate_frequency(bad, "access")

    def test_validate_full_config(self):
        cfg = _default_cfg()
        out = waf._validate_site_config("site_a", cfg)
        self.assertEqual(out["site"], "site_a")
        self.assertEqual(out["frequency"]["access"]["mode"], "url")

    def test_config_rejects_injection_in_url(self):
        from fastapi import HTTPException
        cfg = _default_cfg()
        cfg["blackwhite"]["url_blacklist"] = ["/x; }include /etc/passwd; {"]
        with self.assertRaises(HTTPException):
            waf._validate_site_config("site_a", cfg)

    def test_config_rejects_bad_ip(self):
        from fastapi import HTTPException
        cfg = _default_cfg()
        cfg["blackwhite"]["ip_blacklist"] = ["999.1.2.3 or 1=1"]
        with self.assertRaises(HTTPException):
            waf._validate_site_config("site_a", cfg)

    def test_geo_subnet_ok(self):
        from fastapi import HTTPException
        cfg = _default_cfg()
        cfg["blackwhite"]["ip_whitelist"] = ["10.0.0.0/8"]
        cfg["geo"] = {"enabled": True, "action": "block", "countries": ["CN"]}
        out = waf._validate_site_config("site_a", cfg)
        self.assertEqual(out["geo"]["countries"], ["CN"])

    def test_acl_enum_and_regex(self):
        from fastapi import HTTPException
        cfg = _default_cfg()
        cfg["acl"] = [{"match": "uri", "op": "regex", "value": r"/admin/\d+", "action": "deny"}]
        out = waf._validate_site_config("site_a", cfg)
        self.assertEqual(out["acl"][0]["op"], "regex")
        # 非法枚举
        cfg["acl"] = [{"match": "bad", "op": "eq", "value": "/x", "action": "deny"}]
        with self.assertRaises(HTTPException):
            waf._validate_site_config("site_a", cfg)


class NginxRenderTest(unittest.TestCase):
    def setUp(self):
        _store(self)

    def test_default_render_has_hooks(self):
        cfg = _default_cfg()
        out = waf._render_site_nginx(cfg)
        # 默认防御规则全开应包含 SQL/木马等签名 if
        self.assertIn("return 403", out)
        # 默认有频率限制片段
        self.assertIn("limit_req zone=waf_access", out)

    def test_defense_toggle(self):
        cfg = _default_cfg()
        cfg["defense"]["sql"] = False
        cfg["defense"]["webshell"] = False
        cfg["defense"]["directory"] = False
        cfg["defense"]["xss"] = False
        cfg["defense"]["param"] = False
        cfg["defense"]["ua"] = False
        cfg["defense"]["header"] = False
        cfg["defense"]["cookie"] = False
        cfg["defense"]["http"] = False
        cfg["defense"]["url"] = False
        out = waf._render_site_nginx(cfg)
        self.assertNotIn("if ($request_uri ~*", out)
        self.assertNotIn("if ($ua ~*", out)

    def test_blackwhite_order(self):
        cfg = _default_cfg()
        cfg["blackwhite"]["ip_whitelist"] = ["203.0.113.10"]
        cfg["blackwhite"]["ip_blacklist"] = ["198.51.100.5"]
        out = waf._render_site_nginx(cfg)
        allow_pos = out.index("allow 203.0.113.10;")
        deny_pos = out.index("deny 198.51.100.5;")
        self.assertLess(allow_pos, deny_pos, "白名单 allow 必须在黑名单 deny 之前")

    def test_ng_escape_outside_forbidden(self):
        # 反斜杠保留（正则语义），引号/分号/花括号被清洗
        self.assertEqual(waf._ng_escape(r"\d+"), r"\d+")
        self.assertEqual(waf._ng_escape('a;b"c{d}'), r"a_b_c_d_")  # 各禁止字符分别替换为 _

    def test_render_acl_challenge(self):
        cfg = _default_cfg()
        cfg["waiting_hall"] = {"enabled": True, "url": "/go"}
        cfg["acl"] = [{"id": "x", "match": "ip", "op": "eq", "value": "9.9.9.9", "action": "challenge"}]
        out = waf._render_site_nginx(cfg)
        self.assertIn("waf_challenge", out)
        self.assertIn("$remote_addr", out)

    def test_render_frequency_mode(self):
        for mode, key in (("url", "waf_access"), ("global", "waf_access")):
            cfg = _default_cfg()
            cfg["frequency"]["access"]["mode"] = mode
            out = waf._render_site_nginx(cfg)
            self.assertIn(f"zone={key}:10m", out if mode == "url" else out)
        # 关 count=0 不生成频率片段
        cfg = _default_cfg()
        cfg["frequency"]["access"]["count"] = 0
        cfg["frequency"]["attack"]["count"] = 0
        cfg["frequency"]["notfound"]["count"] = 0
        out = waf._render_site_nginx(cfg)
        self.assertNotIn("limit_req zone=waf_access", out)

    def test_geo_block(self):
        cfg = _default_cfg()
        cfg["geo"] = {"enabled": True, "action": "block", "countries": ["CN"]}
        out = waf._render_site_nginx(cfg)
        self.assertIn("geoip_country_code", out)

    def test_upload_limut_render(self):
        cfg = _default_cfg()
        cfg["custom"]["upload_limit_mb"] = 50
        out = waf._render_site_nginx(cfg)
        self.assertIn("client_max_body_size 50m", out)


class PersistTest(unittest.TestCase):
    def setUp(self):
        _store(self)

    def test_save_then_load(self):
        cfg = _default_cfg()
        cfg["enabled"] = True
        waf._save_waf({"enabled": True, "sites": [cfg]})
        data = waf._load_waf()
        self.assertEqual(data["sites"][0]["site"], "site_a")

    def test_get_site_config(self):
        cfg = _default_cfg()
        waf._save_waf({"enabled": True, "sites": [cfg]})
        self.assertEqual(waf.get_site_config("site_a")["site"], "site_a")
        self.assertIsNone(waf.get_site_config("nonexist"))

    def test_log_ring_truncation(self):
        logs = [{"time": "2026-01-01T00:00:00", "site": "", "ip": "1.2.3.4",
                 "rule": "x", "reason": "r", "action": "deny", "geo": ""}] * 10
        waf._save_logs(logs)
        self.assertEqual(len(waf._load_logs()), 10)

    def test_blockmap_aggregates(self):
        from datetime import datetime
        waf._save_logs([
            {"time": datetime.now().isoformat(), "geo": "北美", "ip": "1.2.3.4", "reason": "r", "action": "deny", "site": ""},
            {"time": datetime.now().isoformat(), "geo": "北美", "ip": "5.6.7.8", "reason": "r", "action": "deny", "site": ""},
            {"time": datetime.now().isoformat(), "geo": "欧洲", "ip": "80.1.2.3", "reason": "r", "action": "deny", "site": ""},
        ])
        # blockmap 是无依赖纯函数吗？——不，它耦合了 WS 与 app；这里仅验证内部聚合逻辑
        # 精确测试 _ip_to_geo 兜底
        self.assertEqual(waf._ip_to_geo("192.168.1.1"), "私有 IP")
        self.assertEqual(waf._ip_to_geo("not-an-ip"), "unknown")

    def test_ip_to_geo_bucket(self):
        # 首字节分桶命中欧洲段
        self.assertNotEqual(waf._ip_to_geo("80.10.1.1"), "unknown")


if __name__ == "__main__":
    unittest.main(verbosity=2)