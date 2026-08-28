# -*- coding: utf-8 -*-
"""
证书到期提醒核心逻辑单元测试（不依赖运行中的后端服务）

覆盖：
  - 证书到期解析与状态判定（ok / warn / expired / unknown）
  - 到期检查：临期推送、过期推送、去重（同档不重复提醒）
  - 配置更新端点

用法：
  python test_certcheck_unit.py
"""
import datetime
import json
import os
import sys
import tempfile
import shutil
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.routers import certcheck  # noqa: E402


def _make_cert(path: str, days_valid: int):
    """生成一张 N 天后到期的自签证书（用 cryptography）。"""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    # 安全：RSA 至少 2048 位（1024 位已认为可被破解，code-scanning 告警）
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "example.com")])
    now = datetime.datetime.now(datetime.timezone.utc)
    # 生效时间固定为 30 天前，保证 days_valid 为负（已过期证书）时
    # not_valid_after 仍在 not_valid_before 之后（cryptography 的约束）
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=30))
        .not_valid_after(now + datetime.timedelta(days=days_valid))
        .sign(key, hashes.SHA256())
    )
    with open(path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))
    return path


class CertStatusTest(unittest.TestCase):
    """证书到期解析与状态判定。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        certcheck.CERTCHECK_FILE = os.path.join(self._tmp, "certcheck.json")
        certcheck.SSL_FILE = os.path.join(self._tmp, "ssl.json")
        # 本机模式：路径直接可读
        certcheck.host_path = lambda p: p
        certcheck._save(dict(certcheck.DEFAULT_CONFIG))

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _cert(self, days_valid):
        path = os.path.join(self._tmp, f"cert_{days_valid}.crt")
        _make_cert(path, days_valid)
        return {"id": f"c{days_valid}", "name": "test", "domains": ["example.com"], "cert_path": path}

    def test_status_ok_warn_expired(self):
        # 90 天后到期 → ok
        st = certcheck._cert_status(self._cert(90))
        self.assertEqual(st["status"], "ok")
        self.assertGreater(st["days_left"], 80)
        # 15 天后到期 → warn
        st = certcheck._cert_status(self._cert(15))
        self.assertEqual(st["status"], "warn")
        # 已过期 → expired
        st = certcheck._cert_status(self._cert(-5))
        self.assertEqual(st["status"], "expired")
        self.assertLess(st["days_left"], 0)

    def test_status_unknown(self):
        # 证书文件不存在 / 空路径 → unknown
        st = certcheck._cert_status({"id": "x", "name": "x", "domains": [], "cert_path": ""})
        self.assertEqual(st["status"], "unknown")
        st = certcheck._cert_status({"id": "x", "name": "x", "domains": [],
                                     "cert_path": os.path.join(self._tmp, "nope.crt")})
        self.assertEqual(st["status"], "unknown")


class CertCheckOnceTest(unittest.TestCase):
    """到期检查与去重。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        certcheck.CERTCHECK_FILE = os.path.join(self._tmp, "certcheck.json")
        certcheck.SSL_FILE = os.path.join(self._tmp, "ssl.json")
        certcheck.host_path = lambda p: p
        certcheck._save(dict(certcheck.DEFAULT_CONFIG))

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _write_ssl(self, certs):
        with open(certcheck.SSL_FILE, "w", encoding="utf-8") as f:
            json.dump({"certs": certs}, f)

    def test_check_reminds_and_dedup(self):
        import json

        from app.routers import notify

        # 15 天后到期的证书：命中 30 天档提醒一次
        path = os.path.join(self._tmp, "c.crt")
        _make_cert(path, 15)
        self._write_ssl([{"id": "c1", "name": "t", "domains": ["a.com"], "cert_path": path}])

        with mock.patch.object(notify, "push_all", return_value=(1, 0)) as push:
            n = certcheck._check_once()
        self.assertEqual(n, 1)
        push.assert_called_once()
        self.assertIn("证书提醒", push.call_args[0][0])
        self.assertIn("30 天", push.call_args[0][0])  # 命中最大档 30

        # 再次检查：同档已提醒 → 不再推送
        with mock.patch.object(notify, "push_all", return_value=(1, 0)) as push2:
            n2 = certcheck._check_once()
        self.assertEqual(n2, 0)
        push2.assert_not_called()

    def test_check_expired(self):
        from app.routers import notify

        path = os.path.join(self._tmp, "e.crt")
        _make_cert(path, -1)
        self._write_ssl([{"id": "e1", "name": "t", "domains": ["b.com"], "cert_path": path}])
        with mock.patch.object(notify, "push_all", return_value=(1, 0)) as push:
            n = certcheck._check_once()
        self.assertEqual(n, 1)
        self.assertIn("证书告警", push.call_args[0][0])

    def test_check_disabled(self):
        from app.routers import notify

        cfg = certcheck._load()
        cfg["enabled"] = False
        certcheck._save(cfg)
        path = os.path.join(self._tmp, "c.crt")
        _make_cert(path, 10)
        self._write_ssl([{"id": "c1", "name": "t", "domains": ["a.com"], "cert_path": path}])
        with mock.patch.object(notify, "push_all", return_value=(1, 0)) as push:
            n = certcheck._check_once()
        self.assertEqual(n, 0)
        push.assert_not_called()


class CertApiTest(unittest.TestCase):
    """端点：状态 / 证书列表 / 测试 / 配置。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        certcheck.CERTCHECK_FILE = os.path.join(self._tmp, "certcheck.json")
        certcheck.SSL_FILE = os.path.join(self._tmp, "ssl.json")
        certcheck.host_path = lambda p: p
        certcheck._save(dict(certcheck.DEFAULT_CONFIG))
        app = FastAPI()
        app.include_router(certcheck.router)
        self.client = TestClient(app)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_status_and_config(self):
        st = self.client.get("/status").json()
        self.assertIn("cert_count", st)
        self.assertEqual(st["remind_days"], [30, 7])

        r = self.client.put("/config", json={"enabled": False, "remind_days": [14]})
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["enabled"])
        self.assertEqual(r.json()["remind_days"], [14])

    def test_certs_and_test(self):
        path = os.path.join(self._tmp, "c.crt")
        _make_cert(path, 60)
        with open(certcheck.SSL_FILE, "w", encoding="utf-8") as f:
            json.dump({"certs": [{"id": "c1", "name": "t", "domains": ["a.com"], "cert_path": path}]}, f)
        r = self.client.get("/certs")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["certs"][0]["status"], "ok")
        r = self.client.post("/test")
        self.assertEqual(r.status_code, 200)
        self.assertIn("triggered", r.json())


if __name__ == "__main__":
    unittest.main(verbosity=2)
