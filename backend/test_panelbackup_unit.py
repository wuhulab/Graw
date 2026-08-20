# -*- coding: utf-8 -*-
"""
面板自身备份核心逻辑单元测试（不依赖运行中的后端服务）

覆盖：
  - 导出：打包 data/ 下所有配置（排除 panelbackups 自身与 *.tmp）
  - 归档列表 / 删除（文件名白名单防穿越）
  - 导入：解压校验、Zip Slip 拒绝、导入前自动备份当前配置、覆盖恢复

用法：
  python test_panelbackup_unit.py
"""
import io
import json
import os
import sys
import tarfile
import tempfile
import shutil
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.routers import panelbackup  # noqa: E402


class PanelBackupUnitTest(unittest.TestCase):
    """导出 / 列表 / 删除 / 导入 纯函数逻辑。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        panelbackup.DATA_DIR = self._tmp
        panelbackup.BACKUP_DIR = os.path.join(self._tmp, "panelbackups")
        # 预置一些"配置"文件
        with open(os.path.join(self._tmp, "users.json"), "w", encoding="utf-8") as f:
            json.dump({"admin": {}}, f)
        with open(os.path.join(self._tmp, "secret.key"), "w", encoding="utf-8") as f:
            f.write("secret")
        os.makedirs(os.path.join(self._tmp, "sub"), exist_ok=True)
        with open(os.path.join(self._tmp, "sub", "cron.json"), "w", encoding="utf-8") as f:
            json.dump([], f)
        # 临时文件应被排除
        with open(os.path.join(self._tmp, "users.json.tmp"), "w", encoding="utf-8") as f:
            f.write("tmp")

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_export(self):
        r = panelbackup._export_sync()
        self.assertIn("name", r)
        self.assertEqual(r["file_count"], 3)  # users.json / secret.key / sub/cron.json（排除 .tmp 与 panelbackups）
        # 归档存在且内容正确
        with tarfile.open(os.path.join(panelbackup.BACKUP_DIR, r["name"]), "r:gz") as tf:
            names = tf.getnames()
        self.assertIn("users.json", names)
        self.assertIn("secret.key", names)
        self.assertIn("sub/cron.json", names)
        self.assertNotIn("users.json.tmp", names)

    def test_list_and_delete(self):
        r = panelbackup._export_sync()
        archives = panelbackup._list_archives_sync()
        self.assertEqual(len(archives), 1)
        self.assertEqual(archives[0]["name"], r["name"])
        # 非法文件名拒绝
        with self.assertRaises(HTTPException):
            panelbackup._delete_archive_sync("../evil.tar.gz")
        panelbackup._delete_archive_sync(r["name"])
        self.assertEqual(panelbackup._list_archives_sync(), [])

    def _make_archive(self, members: dict) -> bytes:
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            for arc, data in members.items():
                info = tarfile.TarInfo(arc)
                data_b = data if isinstance(data, bytes) else data.encode("utf-8")
                info.size = len(data_b)
                tf.addfile(info, io.BytesIO(data_b))
        return buf.getvalue()

    def test_import_restores(self):
        # 构造归档：users.json（新）+ 一个子目录文件
        content = self._make_archive({
            "users.json": json.dumps({"newadmin": {}}),
            "sites/extra.json": json.dumps([1, 2]),
        })
        r = panelbackup._import_sync(content)
        self.assertTrue(r["ok"])
        self.assertEqual(r["restored_files"], 2)
        # 恢复后 data/ 应只有新内容 + panelbackups
        with open(os.path.join(self._tmp, "users.json"), encoding="utf-8") as f:
            self.assertIn("newadmin", f.read())
        self.assertTrue(os.path.isfile(os.path.join(self._tmp, "sites", "extra.json")))
        # 旧的 secret.key 应被覆盖移除（归档里没有它）
        self.assertFalse(os.path.isfile(os.path.join(self._tmp, "secret.key")))
        # 导入前自动备份已生成
        archives = panelbackup._list_archives_sync()
        self.assertTrue(any(a["is_pre_import"] for a in archives))

    def test_import_rejects_tar_slip(self):
        # 构造含 ../ 越界成员的恶意归档
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            info = tarfile.TarInfo("../evil.txt")
            data = b"pwned"
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        with self.assertRaises(HTTPException):
            panelbackup._import_sync(buf.getvalue())
        # 越界文件未落盘
        self.assertFalse(os.path.exists(os.path.join(self._tmp, "..", "evil.txt")))

    def test_import_rejects_absolute_and_special(self):
        content = self._make_archive({"/etc/passwd": "x"})
        with self.assertRaises(HTTPException):
            panelbackup._import_sync(content)


class PanelBackupApiTest(unittest.TestCase):
    """端点：导出 / 列表 / 下载 / 删除 / 导入。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        panelbackup.DATA_DIR = self._tmp
        panelbackup.BACKUP_DIR = os.path.join(self._tmp, "panelbackups")
        with open(os.path.join(self._tmp, "users.json"), "w", encoding="utf-8") as f:
            json.dump({"admin": {}}, f)
        app = FastAPI()
        app.include_router(panelbackup.router)
        self.client = TestClient(app)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_export_download_delete(self):
        r = self.client.post("/export")
        self.assertEqual(r.status_code, 200)
        name = r.json()["name"]
        # 列表
        archives = self.client.get("/list").json()["archives"]
        self.assertEqual(len(archives), 1)
        # 下载
        d = self.client.get(f"/download/{name}")
        self.assertEqual(d.status_code, 200)
        self.assertTrue(d.content.startswith(b"\x1f\x8b"))  # gzip 魔数
        # 非法名下载（不匹配白名单）→ 400
        self.assertEqual(self.client.get("/download/evil.tar.gz").status_code, 400)
        # 删除
        self.assertEqual(self.client.delete(f"/{name}").status_code, 200)
        self.assertEqual(self.client.delete(f"/{name}").status_code, 404)

    def test_import_endpoint(self):
        # 构造归档并上传
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tf:
            info = tarfile.TarInfo("new.json")
            data = b'{"k":"v"}'
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        r = self.client.post("/import", files={"file": ("migrate.tar.gz", buf.getvalue(), "application/gzip")})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])
        self.assertTrue(os.path.isfile(os.path.join(self._tmp, "new.json")))
        # 非 tar.gz 拒绝
        r = self.client.post("/import", files={"file": ("x.txt", b"hello", "text/plain")})
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main(verbosity=2)
