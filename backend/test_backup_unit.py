# -*- coding: utf-8 -*-
"""
备份中心核心逻辑单元测试（不依赖运行中的后端服务）

覆盖：
  - 路径安全校验（相对路径 / data 目录 / - 开头 / \\?\\ 设备命名空间 / 根目录）
  - 备份文件命名与 cron 命令生成（Linux shlex 转义 / Windows PowerShell 转义）
  - 手动备份（tarfile 打包、记录识别）
  - 轮转清理（保留份数 / 保留天数）
  - 恢复（正常恢复 + Tar Slip 路径穿越拒绝）
  - 记录扫描与删除
  - API CRUD（创建/手动备份/删除，cron 计划任务以替身隔离）

用法：
  python test_backup_unit.py
"""
import os
import shutil
import sys
import tarfile
import tempfile
import time
import unittest

# 确保可导入 app 包
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.routers import backup  # noqa: E402


def _mk_tree(base: str, files: dict) -> str:
    """在 base 下创建目录/文件树，返回 base 路径。"""
    for rel, content in files.items():
        p = os.path.join(base, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(content)
    return base


class BackupUnitTest(unittest.TestCase):
    """纯函数逻辑：校验 / 命名 / cron 命令 / 轮转 / 扫描 / 备份 / 恢复。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        backup.BACKUP_FILE = os.path.join(self._tmp, "backup.json")
        backup.DEFAULT_BACKUP_DIR = os.path.join(self._tmp, "backups")
        # 本机模式：宿主路径即容器内路径
        backup.host_path = lambda p: p
        backup.unhost_path = lambda p: p
        backup.IS_WINDOWS = False

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    # ---------- 路径安全校验 ----------
    def test_validate_source_rejects_relative(self):
        with self.assertRaises(HTTPException):
            backup._validate_source_path("relative/path")

    def test_validate_source_rejects_data_dir(self):
        with self.assertRaises(HTTPException):
            backup._validate_source_path(backup.DATA_DIR)

    def test_validate_source_rejects_dash_leading(self):
        with self.assertRaises(HTTPException):
            backup._validate_source_path("/tmp/-evil")

    def test_validate_source_rejects_device_namespace(self):
        with self.assertRaises(HTTPException):
            backup._validate_source_path("\\\\?\\C:\\secret")

    def test_validate_source_ok(self):
        p = os.path.join(self._tmp, "mydir")
        self.assertEqual(backup._validate_source_path(p), os.path.normpath(p))

    def test_validate_target_rejects_root_and_data(self):
        with self.assertRaises(HTTPException):
            backup._validate_target_dir("/")
        with self.assertRaises(HTTPException):
            backup._validate_target_dir(backup.DATA_DIR)
        with self.assertRaises(HTTPException):
            backup._validate_target_dir("relative")

    # ---------- 命名与 cron 命令 ----------
    def test_sanitize_name(self):
        self.assertEqual(backup._sanitize_name("/var/www/html"), "html")
        self.assertEqual(backup._sanitize_name("我的站点目录"), "______")
        self.assertEqual(backup._sanitize_name("my_site-1.v2"), "my_site-1.v2")

    def test_cron_command_linux_escape(self):
        cmd = backup._build_cron_command(
            "/var/www/my site;rm -rf /", "/data/backup dir", "my_site"
        )
        # 路径中特殊字符必须被单引号包裹（shlex.quote），不出现未转义的裸命令
        self.assertIn("'my site;rm -rf '", cmd)
        self.assertIn("'/data/backup dir'", cmd)
        self.assertIn("-- ", cmd)  # tar 选项解析终结符
        self.assertIn("my_site_$(date +\\%Y\\%m\\%d_\\%H\\%M\\%S).tar.gz", cmd)

    def test_cron_command_windows_escape(self):
        backup.IS_WINDOWS = True
        try:
            cmd = backup._build_cron_command("C:\\site;evil", "C:\\back up", "site")
            # PowerShell 单引号字面量包裹：内部单引号双写，特殊字符按字面量
            self.assertIn("'site;evil'", cmd)
            self.assertIn("'C:\\back up'", cmd)
            self.assertIn("site_$d.tar.gz", cmd)
        finally:
            backup.IS_WINDOWS = False

    # ---------- 手动备份 / 轮转 / 记录 ----------
    def test_backup_and_records(self):
        src = _mk_tree(os.path.join(self._tmp, "web"), {"index.html": "hello", "css/a.css": "a"})
        data = {"backup_dir": backup.DEFAULT_BACKUP_DIR, "tasks": []}
        task = {
            "id": "bk_test", "name": "web", "source": src, "target": "",
            "safe": backup._sanitize_name(src), "keep_count": 2, "keep_days": 0,
        }
        res = backup._do_backup_sync(data, task)
        self.assertTrue(res["ok"])
        self.assertTrue(res["file"].endswith(".tar.gz"))
        self.assertTrue(os.path.exists(backup.host_path(res["file"])))

        # 记录扫描能识别该备份并归属任务
        task["id"] = "bk_test"
        records = backup._scan_records_sync(data, [task])
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["task_id"], "bk_test")
        self.assertEqual(records[0]["name"], os.path.basename(res["file"]))

        # 备份内容可解压还原
        with tarfile.open(backup.host_path(res["file"]), "r:gz") as tf:
            names = tf.getnames()
        self.assertTrue(any(n.endswith("index.html") for n in names))

    def test_rotate_by_count(self):
        target = os.path.join(self._tmp, "backups")
        os.makedirs(target, exist_ok=True)
        # 生成 4 个旧备份（mtime 递增，越新越大）
        for i in range(4):
            p = os.path.join(target, f"web_20260820_00000{i}.tar.gz")
            with open(p, "wb") as f:
                f.write(b"x" * (i + 1))
            os.utime(p, (time.time() - (4 - i) * 100, time.time() - (4 - i) * 100))
        data = {"backup_dir": backup.DEFAULT_BACKUP_DIR, "tasks": []}
        task = {"id": "t", "source": self._tmp, "safe": "web", "keep_count": 2, "keep_days": 0}
        removed = backup._rotate_sync(data, task)
        self.assertEqual(len(removed), 2)
        remain = [f for f in os.listdir(target) if f.endswith(".tar.gz")]
        self.assertEqual(len(remain), 2)
        # 保留的是最新两份
        self.assertIn("web_20260820_000003.tar.gz", remain)
        self.assertIn("web_20260820_000002.tar.gz", remain)

    def test_rotate_by_days_keeps_latest(self):
        target = os.path.join(self._tmp, "backups")
        os.makedirs(target, exist_ok=True)
        old = os.path.join(target, "web_20260101_000000.tar.gz")
        new = os.path.join(target, "web_20260820_000000.tar.gz")
        for p in (old, new):
            with open(p, "wb") as f:
                f.write(b"x")
        os.utime(old, (time.time() - 40 * 86400, time.time() - 40 * 86400))
        data = {"backup_dir": backup.DEFAULT_BACKUP_DIR, "tasks": []}
        task = {"id": "t", "source": self._tmp, "safe": "web", "keep_count": 0, "keep_days": 30}
        removed = backup._rotate_sync(data, task)
        # 过期文件被删，但最新一份始终保留
        self.assertEqual(removed, ["web_20260101_000000.tar.gz"])
        self.assertTrue(os.path.exists(new))

    # ---------- 恢复（含防穿越） ----------
    def test_restore_ok(self):
        src = _mk_tree(os.path.join(self._tmp, "web"), {"index.html": "v1", "sub/f.txt": "f"})
        data = {"backup_dir": backup.DEFAULT_BACKUP_DIR, "tasks": []}
        task = {"id": "t", "name": "web", "source": src, "target": "",
                "safe": backup._sanitize_name(src), "keep_count": 0, "keep_days": 0}
        res = backup._do_backup_sync(data, task)
        fname = os.path.basename(res["file"])

        dest = os.path.join(self._tmp, "restored")
        r = backup._do_restore_sync(task, fname, dest)
        self.assertTrue(r["ok"])
        restored_index = os.path.join(dest, os.path.basename(src), "index.html")
        with open(restored_index, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "v1")

    def test_restore_rejects_tar_slip(self):
        """构造含 ../ 越界成员的恶意 tar，恢复必须拒绝。"""
        evil_tar = os.path.join(self._tmp, "backups", "web_20260820_000000.tar.gz")
        os.makedirs(os.path.dirname(evil_tar), exist_ok=True)
        with tarfile.open(evil_tar, "w:gz") as tf:
            info = tarfile.TarInfo("../evil.txt")
            data = b"pwned"
            info.size = len(data)
            tf.addfile(info, __import__("io").BytesIO(data))
        task = {"id": "t", "name": "web", "source": "/x", "target": "",
                "safe": "web", "keep_count": 0, "keep_days": 0}
        with self.assertRaises(HTTPException):
            backup._do_restore_sync(task, "web_20260820_000000.tar.gz", os.path.join(self._tmp, "dest"))
        self.assertFalse(os.path.exists(os.path.join(self._tmp, "evil.txt")))

    def test_restore_rejects_bad_filename(self):
        task = {"id": "t", "source": "/x", "target": "", "safe": "web"}
        with self.assertRaises(HTTPException):
            backup._do_restore_sync(task, "../../etc/passwd", "/tmp")

    # ---------- 远程备份（WebDAV） ----------
    def test_webdav_url_validates_scheme(self):
        """WebDAV URL 拼接：拒绝非 http/https，路径正确拼接。"""
        with self.assertRaises(HTTPException):
            backup._webdav_url({"base": "file:///etc/passwd"}, "x")
        with self.assertRaises(HTTPException):
            backup._webdav_url({"base": "ftp://host/x"}, "y")
        url = backup._webdav_url({"base": "https://dav.example.com/dav/"}, "web/x.tar.gz")
        self.assertEqual(url, "https://dav.example.com/dav/web/x.tar.gz")

    def test_remote_upload_success(self):
        """远程上传：MKCOL 建目录 + PUT 上传，2xx 视为成功。"""
        import unittest.mock as mock
        from types import SimpleNamespace

        remote = {"base": "https://dav.example.com/dav", "username": "u", "password": "p"}
        local = os.path.join(self._tmp, "web_20260820.tar.gz")
        with open(local, "wb") as f:
            f.write(b"backup-data")
        responses = iter([SimpleNamespace(status_code=201), SimpleNamespace(status_code=201)])
        with mock.patch("requests.request", side_effect=lambda *a, **k: next(responses)) as m:
            backup._remote_upload(remote, local, "web", "web_20260820.tar.gz")
        # MKCOL + PUT 各一次
        self.assertEqual(m.call_count, 2)
        methods = [c.args[0] for c in m.call_args_list]
        self.assertIn("MKCOL", methods)
        self.assertIn("PUT", methods)

    def test_remote_upload_auth_fail(self):
        """远程上传 401：抛 HTTPException 401，不当作成功。"""
        import unittest.mock as mock
        from types import SimpleNamespace

        remote = {"base": "https://dav.example.com/dav", "username": "u", "password": "p"}
        local = os.path.join(self._tmp, "web_20260820.tar.gz")
        with open(local, "wb") as f:
            f.write(b"x")
        with mock.patch("requests.request", return_value=SimpleNamespace(status_code=401)):
            with self.assertRaises(HTTPException) as cm:
                backup._remote_upload(remote, local, "web", "web_20260820.tar.gz")
        self.assertEqual(cm.exception.status_code, 401)

    def test_remote_upload_http_error(self):
        """远程上传 500：抛 HTTPException 502。"""
        import unittest.mock as mock
        from types import SimpleNamespace

        remote = {"base": "https://dav.example.com/dav"}
        local = os.path.join(self._tmp, "web_20260820.tar.gz")
        with open(local, "wb") as f:
            f.write(b"x")
        with mock.patch("requests.request", return_value=SimpleNamespace(status_code=500)):
            with self.assertRaises(HTTPException) as cm:
                backup._remote_upload(remote, local, "web", "web_20260820.tar.gz")
        self.assertEqual(cm.exception.status_code, 502)

    def test_test_remote_ok_and_fail(self):
        """连接测试：PROPFIND 2xx 成功，>=400 抛 502。"""
        import unittest.mock as mock
        from types import SimpleNamespace

        remote = {"base": "https://dav.example.com/dav", "username": "u", "password": "p"}
        with mock.patch("requests.request", return_value=SimpleNamespace(status_code=207)):
            backup._test_remote(remote)  # 不抛异常
        with mock.patch("requests.request", return_value=SimpleNamespace(status_code=401)):
            with self.assertRaises(HTTPException) as cm:
                backup._test_remote(remote)
        self.assertEqual(cm.exception.status_code, 401)
        with mock.patch("requests.request", return_value=SimpleNamespace(status_code=500)):
            with self.assertRaises(HTTPException) as cm:
                backup._test_remote(remote)
        self.assertEqual(cm.exception.status_code, 502)


class BackupApiTest(unittest.TestCase):
    """通过 TestClient 验证任务 CRUD / 手动备份 / 记录删除。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        backup.BACKUP_FILE = os.path.join(self._tmp, "backup.json")
        backup.DEFAULT_BACKUP_DIR = os.path.join(self._tmp, "backups")
        backup.host_path = lambda p: p
        backup.unhost_path = lambda p: p
        backup.IS_WINDOWS = False
        self._src = _mk_tree(os.path.join(self._tmp, "web"), {"index.html": "hello"})
        # 隔离 cron：创建/更新/删除计划任务的替身（async）
        import app.routers.cron as cron

        async def _fake_create(req):
            return {"id": "task_fake", "name": req.name, "schedule": req.schedule}

        async def _fake_update(tid, req):
            return {"id": tid}

        async def _fake_delete(tid):
            return {"ok": True}

        cron.create_task = _fake_create
        cron.update_task = _fake_update
        cron.delete_task = _fake_delete

        app = FastAPI()
        app.include_router(backup.router)
        self.client = TestClient(app)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _create(self, **kw):
        payload = {
            "name": "网站备份",
            "type": "dir",
            "source": self._src,
            "target": "",
            "schedule": "30 2 * * *",
            "keep_count": 5,
            "keep_days": 0,
            "enabled": True,
        }
        payload.update(kw)
        return self.client.post("/tasks", json=payload)

    def test_crud(self):
        r = self._create()
        self.assertEqual(r.status_code, 200)
        tid = r.json()["id"]
        self.assertEqual(r.json()["cron_task_id"], "task_fake")
        self.assertEqual(len(self.client.get("/tasks").json()["tasks"]), 1)

        # 更新
        r = self.client.put(f"/tasks/{tid}", json={"keep_count": 3, "schedule": ""})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["keep_count"], 3)
        # 删除计划任务后 cron_task_id 清空
        self.assertEqual(r.json()["cron_task_id"], "")

        # 非法源路径拒绝
        self.assertEqual(self._create(source="relative").status_code, 400)
        self.assertEqual(self._create(source=backup.DATA_DIR).status_code, 403)

        # 删除
        self.assertEqual(self.client.delete(f"/tasks/{tid}").status_code, 200)
        self.assertEqual(self.client.delete("/tasks/notexist").status_code, 404)

    def test_manual_run_and_records(self):
        r = self._create(schedule="")
        tid = r.json()["id"]
        run = self.client.post(f"/tasks/{tid}/run")
        self.assertEqual(run.status_code, 200)
        self.assertTrue(run.json()["ok"])

        records = self.client.get("/records").json()["records"]
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["task_id"], tid)

        # 删除记录（删除备份文件）
        fname = records[0]["name"]
        r = self.client.delete("/records", params={"file": fname})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.client.get("/records").json()["records"], [])

    def test_delete_record_rejects_bad_name(self):
        r = self.client.delete("/records", params={"file": "../evil.tar.gz"})
        self.assertEqual(r.status_code, 400)

    def test_status(self):
        st = self.client.get("/status").json()
        self.assertEqual(st["task_count"], 0)
        self.assertIn("backup_dir", st)

    # ---------- 远程备份目标 CRUD ----------
    def _add_remote(self, base="https://dav.example.com/dav/", **kw):
        payload = {"name": "坚果云", "type": "webdav", "base": base, "username": "u", "password": "p"}
        payload.update(kw)
        return self.client.post("/remotes", json=payload)

    def test_remotes_crud(self):
        r = self._add_remote()
        self.assertEqual(r.status_code, 200)
        rid = r.json()["id"]
        self.assertTrue(r.json()["has_password"])
        # 密码绝不回显
        self.assertNotIn("password", r.json())

        # 非法 base 拒绝
        self.assertEqual(self._add_remote(base="file:///etc/passwd").status_code, 400)
        self.assertEqual(self._add_remote(base="ftp://x/y").status_code, 400)

        # 更新（密码留空 = 保持）
        r = self.client.put(f"/remotes/{rid}", json={
            "name": "坚果云2", "type": "webdav", "base": "https://dav.example.com/dav2/",
            "username": "u2", "password": "",
        })
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["name"], "坚果云2")
        self.assertTrue(r.json()["has_password"])  # 原密码保留

        # 列表脱敏
        remotes = self.client.get("/remotes").json()["remotes"]
        self.assertEqual(len(remotes), 1)
        self.assertNotIn("password", remotes[0])

        # 删除
        self.assertEqual(self.client.delete(f"/remotes/{rid}").status_code, 200)
        self.assertEqual(self.client.delete("/remotes/notexist").status_code, 404)

    def test_task_bind_remote_and_upload(self):
        """任务绑定远程后，手动备份会上传到远程（mock 上传成功）。"""
        import unittest.mock as mock

        rid = self._add_remote().json()["id"]
        r = self._create(schedule="", remote_id=rid)
        self.assertEqual(r.status_code, 200)
        tid = r.json()["id"]
        self.assertEqual(r.json()["remote_id"], rid)

        # 绑定不存在的远程被拒
        self.assertEqual(self._create(schedule="", remote_id="rmt_nope").status_code, 400)

        with mock.patch("app.routers.backup._remote_upload", return_value=None):
            run = self.client.post(f"/tasks/{tid}/run")
        self.assertEqual(run.status_code, 200)
        body = run.json()
        self.assertTrue(body["remote"]["uploaded"])
        self.assertEqual(body["remote"]["remote_id"], rid)

    def test_task_unbind_remote_on_delete(self):
        """删除远程目标后，绑定该远程的任务被解除绑定。"""
        rid = self._add_remote().json()["id"]
        tid = self._create(schedule="", remote_id=rid).json()["id"]
        self.assertEqual(self.client.delete(f"/remotes/{rid}").status_code, 200)
        tasks = self.client.get("/tasks").json()["tasks"]
        t = next(x for x in tasks if x["id"] == tid)
        self.assertEqual(t["remote_id"], "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
