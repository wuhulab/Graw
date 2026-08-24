# -*- coding: utf-8 -*-
"""
test_netstorage_unit.py - 网络储存路由单元测试

不依赖后端服务：将配置路径 patch 到临时目录，覆盖：
- 连接校验白名单（名称/主机/基础路径/端口/控制字符/注入）
- 连接 CRUD 持久化与密码脱敏
- 云端逻辑路径校验（穿越 / 控制字符 / 前缀）
- FTP 时间解析、路径拼接、父目录计算
- WebDAV PROPFIND XML 解析
- SMB / S3 路径构建（_full / _prefix / _obj_key）
- 管理员守卫（非管理员 403）

运行：backend\.venv\Scripts\python.exe test_netstorage_unit.py
"""
import os
import re
import shutil
import stat
import tempfile
import unittest
from io import BytesIO

# 让被测模块指向临时配置目录，避免触碰真实 data/netstorage.json
import app.routers.netstorage as ns

NS = "DAV:"
ns.CONF_FILE = os.path.join(tempfile.gettempdir(), "test_netstorage_%s.json" % os.getpid())


def _cleanup():
    if os.path.exists(ns.CONF_FILE):
        os.remove(ns.CONF_FILE)


def _conn(**kw):
    base = dict(name="测试存储", type="ftp", host="ftp.example.com", port=21,
                username="u", password="p", base=None, params={})
    base.update(kw)
    return base


class TestValidation(unittest.TestCase):
    def setUp(self):
        _cleanup()

    def test_valid_connection(self):
        c = ns.ConnectionIn(name="上海服务器FTP", type="ftp", host="1.2.3.4",
                            username="admin", password="x")
        c.validate_rules()
        self.assertEqual(c.port, 21)  # 默认端口

    def test_default_ports_per_type(self):
        for t, port in [("ftp", 21), ("ftps", 990), ("smb", 445), ("webdav", 443), ("s3", 9000)]:
            c = ns.ConnectionIn(name="a", type=t, host="h", port=None)
            c.validate_rules()
            self.assertEqual(c.port, port, t)

    def test_bad_type(self):
        c = ns.ConnectionIn(name="a", type="nfs", host="h")
        with self.assertRaises(Exception):
            c.validate_rules()

    def test_control_char_in_name(self):
        c = ns.ConnectionIn(name="a\nb", type="ftp", host="h")
        with self.assertRaises(Exception):
            c.validate_rules()

    def test_bad_host(self):
        c = ns.ConnectionIn(name="a", type="ftp", host="host with space ;")
        with self.assertRaises(Exception):
            c.validate_rules()

    def test_port_bounds(self):
        with self.assertRaises(Exception):
            ns.ConnectionIn(name="a", type="ftp", host="h", port=0).validate_rules()
        with self.assertRaises(Exception):
            ns.ConnectionIn(name="a", type="ftp", host="h", port=70000).validate_rules()

    def test_smb_share_injection(self):
        # 共享名拒绝跨目录 / 控制字符
        c = ns.ConnectionIn(name="a", type="smb", host="h", base="x\ny")
        with self.assertRaises(Exception):
            c.validate_rules()
        c2 = ns.ConnectionIn(name="a", type="smb", host="h", base="..")
        with self.assertRaises(Exception):
            c2.validate_rules()

    def test_webdav_url_scheme(self):
        # WebDAV 根地址仅允许 http/https
        c = ns.ConnectionIn(name="a", type="webdav", host="h", base="file:///etc/x")
        with self.assertRaises(Exception):
            c.validate_rules()
        # 无 scheme 时按 http 处理（合法）
        c2 = ns.ConnectionIn(name="a", type="webdav", host="h", base="dav.example.com")
        c2.validate_rules()

    def test_s3_bucket_charset(self):
        c = ns.ConnectionIn(name="a", type="s3", host="h", base="good-bucket_1.x")
        c.validate_rules()
        c2 = ns.ConnectionIn(name="a", type="s3", host="h", base="bad/bucket")
        with self.assertRaises(Exception):
            c2.validate_rules()


class TestPathValidation(unittest.TestCase):
    def test_normalize(self):
        self.assertEqual(ns._validate_lpath(""), "/")
        self.assertEqual(ns._validate_lpath("//a//b/"), "/a/b")

    def test_reject_traversal(self):
        for p in ["/../x", "/a/../../x", "/a/../x"]:
            with self.assertRaises(Exception):
                ns._validate_lpath(p)

    def test_reject_control(self):
        with self.assertRaises(Exception):
            ns._validate_lpath("/a\x00b")

    def test_require_leading_slash(self):
        with self.assertRaises(Exception):
            ns._validate_lpath("a/b")

    def test_join_and_parent(self):
        self.assertEqual(ns._join_l("/", "f"), "/f")
        self.assertEqual(ns._join_l("/a", "f"), "/a/f")
        self.assertIsNone(ns._lp_parent("/"))
        self.assertEqual(ns._lp_parent("/a/b"), "/a")


class TestPersistence(unittest.TestCase):
    def setUp(self):
        _cleanup()

    def _save_conns(self):
        ns._save([_conn(id="abc", password="secret")])
        return ns._load()

    def test_save_load_roundtrip(self):
        data = self._save_conns()
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["password"], "secret")

    def test_get_conn_404(self):
        self._save_conns()
        with self.assertRaises(Exception):
            ns._get_conn("nope")

    def test_mask_password(self):
        c = _conn(id="abc", password="secret")
        m = ns._mask(c)
        self.assertTrue(m["has_password"])
        self.assertEqual(m["password"], "")


class TestFTPHelpers(unittest.TestCase):
    def test_ts_parse(self):
        self.assertGreater(ns._ftp_ts("20260819120000"), 0)
        self.assertEqual(ns._ftp_ts("garbage"), 0)

    def test_make_adapter_ftp(self):
        a = ns._make_adapter(_conn(type="ftp"))
        self.assertIsInstance(a, ns.FTPAdapter)
        self.assertEqual(a._full("/a"), "/a")
        self.assertEqual(a._full(""), "/")
        # base 前缀
        a2 = ns._make_adapter(_conn(type="ftp", base="pub"))
        self.assertEqual(a2._full("x"), "/pub/x")


class TestSmbPaths(unittest.TestCase):
    def test_full_build(self):
        a = ns.SMBAdapter(_conn(type="smb", host="192.168.1.10", base="share"))
        self.assertEqual(a._root, "//192.168.1.10/share")
        self.assertEqual(a._full("/sub/file.txt"), "//192.168.1.10/share/sub/file.txt")
        self.assertEqual(a._full("/"), "//192.168.1.10/share")


class TestWebDavParse(unittest.TestCase):
    def test_dav_parse(self):
        xml = (
            '<?xml version="1.0"?>'
            '<d:multistatus xmlns:d="DAV:">'
            '<d:response><d:href>/dav/user/</d:href><d:propstat><d:prop>'
            '<d:resourcetype><d:collection/></d:resourcetype>'
            '</d:prop></d:propstat></d:response>'
            '<d:response><d:href>/dav/user/a.txt</d:href><d:propstat><d:prop>'
            '<d:getcontentlength>123</d:getcontentlength>'
            '<d:getlastmodified>Thu, 19 Aug 2026 08:00:00 GMT</d:getlastmodified>'
            '</d:prop></d:propstat></d:response>'
            '</d:multistatus>'
        )
        # 当前目录 = /dav/user/（资源自身的 href 被过滤）
        entries = ns._dav_parse(xml.encode(), "http://h/dav/user/")
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["name"], "a.txt")
        self.assertFalse(entries[0]["is_dir"])
        self.assertEqual(entries[0]["size"], 123)
        self.assertGreater(ns._dav_ts("Thu, 19 Aug 2026 08:00:00 GMT"), 1700000000)


class TestS3Paths(unittest.TestCase):
    def test_keys(self):
        a = ns.S3Adapter(_conn(type="s3", host="minio.example.com", username="ak", password="sk",
                               base="mybucket"))
        self.assertEqual(a._prefix("/"), "")
        self.assertEqual(a._prefix("/a"), "a/")
        self.assertEqual(a._obj_key("/a/b.txt"), "a/b.txt")


class TestAdminGuard(unittest.TestCase):
    def test_admin_ok(self):
        ns._require_admin_user({"role": "admin"})
        ns._require_admin_user({"is_admin": True})

    def test_user_forbidden(self):
        with self.assertRaises(Exception):
            ns._require_admin_user({"role": "user"})


class TestFtpAdapterSmoke(unittest.TestCase):
    """FTP 适配器：用一个假的 ftplib 均衡验证 list/read/write 流程，不真连网络。"""

    def test_list_with_mlsd(self):
        class FakeFTP:
            def __init__(self): self.calls = []
            def connect(self, *a, **k): return None
            def login(self, *a, **k): return None
            def set_pasv(self, *a, **k): return None
            def quit(self): return None
            def mlsd(self, path, facts):  # noqa
                yield "file1.txt", {"type": "file", "size": "10", "modify": "20260819080000"}
                yield "sub/", {"type": "dir", "size": "0", "modify": ""}
            def nlst(self, path): raise Exception("should not call")

        adapter = ns.FTPAdapter(_conn(type="ftp", base=""))
        adapter._connect = FakeFTP
        res = adapter.list("/")
        names = {i["name"]: i for i in res["items"]}
        self.assertTrue(names["file1.txt"]["is_dir"] is False)
        self.assertEqual(names["file1.txt"]["size"], 10)
        self.assertTrue(names["sub/"]["is_dir"])


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        _cleanup()
        shutil.rmtree(os.path.join(tempfile.gettempdir(), "graw_test_ns_tmp"),
                      ignore_errors=True)