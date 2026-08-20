# -*- coding: utf-8 -*-
"""
虚拟 FTP 用户管理测试

包含两部分：
  1. 单元测试（unittest + FastAPI TestClient，不依赖运行中的后端）：
     - 用户 CRUD 全流程
     - 用户名/目录校验（非法字符、非绝对路径、重名）
     - 密码 bcrypt 哈希落盘：任何接口不回传密码字段，存储哈希可校验
     - 更新仅影响传入字段；用户名重名校验排除自身
  2. 集成测试（E2E，需后端运行在 8000 端口，用法同 test_uptime_e2e.py）：
     - 注入临时管理员/普通用户
     - 管理员可对 /api/ftpusers 增删改查
     - 普通用户访问被拒（403）

用法：
  python test_ftpusers.py           # 仅单元测试
  python test_ftpusers.py e2e       # 单元 + 集成测试（默认 http://localhost:8000）
  python test_ftpusers.py e2e 8011  # 指定端口
"""
import json
import os
import re
import shutil
import sys
import tempfile
import unittest

# 脚本位于项目根目录，后端代码在 backend/ 下，需同时加入两个路径
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_BACKEND_DIR = os.path.join(_SCRIPT_DIR, "backend")
sys.path.insert(0, _BACKEND_DIR)
sys.path.insert(0, _SCRIPT_DIR)

import bcrypt  # noqa: E402
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.routers import ftpusers  # noqa: E402


def _valid_payload(**kw):
    payload = {
        "username": "webuser",
        "password": "FtpPass#123",
        "directory": "/srv/ftp/webuser",
        "enabled": True,
        "description": "官网文件上传",
    }
    payload.update(kw)
    return payload


class FtpUsersUnitTest(unittest.TestCase):
    """通过 TestClient 验证虚拟 FTP 用户 CRUD 与校验逻辑。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        ftpusers.FTPUSERS_FILE = os.path.join(self._tmp, "ftp_users.json")
        app = FastAPI()
        # 与 main.py 一致：挂载前缀 /api/ftpusers
        app.include_router(ftpusers.router, prefix="/api/ftpusers")
        self.client = TestClient(app)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _create(self, **kw):
        return self.client.post("/api/ftpusers", json=_valid_payload(**kw))

    def test_crud_and_validation(self):
        # 创建
        r = self._create()
        self.assertEqual(r.status_code, 200, r.text)
        user = r.json()
        uid = user["id"]
        self.assertEqual(user["username"], "webuser")
        # 响应中绝不包含密码哈希
        self.assertNotIn("password", user)

        # 列表不含密码字段
        lst = self.client.get("/api/ftpusers").json()["users"]
        self.assertEqual(len(lst), 1)
        self.assertNotIn("password", lst[0])

        # 非法用户名 / 非绝对路径拒绝
        self.assertEqual(self._create(username="bad user").status_code, 400)
        self.assertEqual(self._create(username="").status_code, 400)
        self.assertEqual(self._create(directory="relative/path").status_code, 400)
        self.assertEqual(self._create(directory="").status_code, 400)
        # 密码过短拒绝
        self.assertEqual(self._create(password="12345").status_code, 400)
        # 用户名重名拒绝
        self.assertEqual(self._create(username="webuser").status_code, 400)

        # 更新：仅更新传入字段
        r = self.client.put(f"/api/ftpusers/{uid}", json={"description": "改用途", "enabled": False})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["description"], "改用途")
        self.assertFalse(r.json()["enabled"])
        self.assertEqual(r.json()["username"], "webuser")  # 未改的字段保持

        # 重名校验：改成已存在（另一个）用户名 → 拒绝
        self._create(username="second")
        r = self.client.put(f"/api/ftpusers/{uid}", json={"username": "second"})
        self.assertEqual(r.status_code, 400)
        # 更新为自身当前用户名 → 允许（排除自身）
        r = self.client.put(f"/api/ftpusers/{uid}", json={"username": "webuser"})
        self.assertEqual(r.status_code, 200)

        # 删除
        self.assertEqual(self.client.delete(f"/api/ftpusers/{uid}").status_code, 200)
        self.assertEqual(self.client.delete(f"/api/ftpusers/{uid}").status_code, 404)
        self.assertEqual(self.client.delete("/api/ftpusers/nope").status_code, 404)
        self.assertEqual(self.client.put("/api/ftpusers/nope", json={}).status_code, 404)

    def test_password_hashed_and_never_returned(self):
        """密码以 bcrypt 哈希落盘，接口不回传，且哈希可校验明文。"""
        r = self._create(password="Secret@2024")
        self.assertEqual(r.status_code, 200)
        uid = r.json()["id"]

        # 磁盘上存储的是 bcrypt 哈希（$2b$...），且能校验明文
        with open(ftpusers.FTPUSERS_FILE, "r", encoding="utf-8") as f:
            stored = json.load(f)["users"][0]
        self.assertTrue(stored["password"].startswith("$2b$"))
        self.assertTrue(bcrypt.checkpw(b"Secret@2024", stored["password"].encode("utf-8")))
        self.assertFalse(bcrypt.checkpw(b"wrong-password", stored["password"].encode("utf-8")))

        # 列表 / 详情响应不含 password
        for resp in (self.client.get("/api/ftpusers"),):
            body = json.dumps(resp.json())
            self.assertNotIn("password", body)

        # 更新密码后哈希变化且可校验新明文
        self.client.put(f"/api/ftpusers/{uid}", json={"password": "NewPass@999"})
        with open(ftpusers.FTPUSERS_FILE, "r", encoding="utf-8") as f:
            updated = json.load(f)["users"][0]
        self.assertTrue(bcrypt.checkpw(b"NewPass@999", updated["password"].encode("utf-8")))

    def test_windows_directory_accepted(self):
        """Windows 风格绝对路径（盘符/UNC）也应被接受。"""
        for i, d in enumerate((r"C:\ftp\webuser", r"C:/ftp/webuser", r"\\server\share\ftp")):
            r = self._create(username=f"winuser{i}", directory=d)
            self.assertEqual(r.status_code, 200, r.text)


# ---------------------------------------------------------------------------
# E2E 集成测试（需后端运行）
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # 先跑单元测试
    suite = unittest.TestLoader().loadTestsFromTestCase(FtpUsersUnitTest)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    unit_failed = not result.wasSuccessful()

    # 参数：python test_ftpusers.py [e2e] [port]
    if len(sys.argv) < 2 or sys.argv[1].lower() != "e2e":
        sys.exit(1 if unit_failed else 0)

    import threading
    import http.server
    import requests

    PORT = sys.argv[2] if len(sys.argv) > 2 else "8000"
    BASE = f"http://localhost:{PORT}"
    HERE = _BACKEND_DIR
    USERS_FILE = os.path.join(HERE, "data", "users.json")
    BACKUP_FILE = USERS_FILE + ".bak_ftpe2e"

    ADMIN = "ftpadmin"
    USER = "ftpuser"
    PASS = "FtpPass#123"

    PASS_N = 0
    FAIL_N = 0

    def ok(name, detail=""):
        global PASS_N
        PASS_N += 1
        print(f"  PASS  {name}" + (f"  ({detail})" if detail else ""))

    def fail(name, detail):
        global FAIL_N
        FAIL_N += 1
        print(f"  FAIL  {name}: {detail}")

    def check(name, cond, detail=""):
        if cond:
            ok(name, detail)
        else:
            fail(name, detail)

    def _entry_headers():
        cfg = os.path.join(HERE, "data", "shunx.json")
        entry = None
        try:
            with open(cfg, "r", encoding="utf-8") as f:
                entry = json.load(f).get("entry_path")
        except Exception:
            pass
        return {"X-ShunX-Entry": entry} if entry else {}

    def login(username, password):
        return requests.post(f"{BASE}/api/auth/login",
                             json={"username": username, "password": password},
                             headers=_entry_headers())

    def hdr(token):
        return {"Authorization": f"Bearer {token}"}

    def setup():
        shutil.copy2(USERS_FILE, BACKUP_FILE)
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            users = json.load(f)
        pw = bcrypt.hashpw(PASS.encode(), bcrypt.gensalt()).decode()
        users[ADMIN] = {"username": ADMIN, "password": pw, "role": "admin",
                        "must_change_password": False, "created_at": 0}
        users[USER] = {"username": USER, "password": pw, "role": "user",
                       "must_change_password": False, "created_at": 0}
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(users, f, ensure_ascii=False, indent=2)

    def teardown():
        if os.path.exists(BACKUP_FILE):
            shutil.move(BACKUP_FILE, USERS_FILE)

    def run():
        r = login(ADMIN, PASS)
        check("管理员登录", r.status_code == 200, f"status={r.status_code}")
        if r.status_code != 200:
            return
        token = r.json()["token"]
        H = hdr(token)

        ru = login(USER, PASS)
        HU = hdr(ru.json()["token"]) if ru.status_code == 200 else None

        # 权限分级：普通用户访问 FTP 用户接口被拒
        rp = requests.get(f"{BASE}/api/ftpusers", headers=HU)
        check("普通用户访问被拒", rp.status_code == 403, f"status={rp.status_code}")

        # 清理可能残留的 FTP 用户
        for u in requests.get(f"{BASE}/api/ftpusers", headers=H).json().get("users", []):
            requests.delete(f"{BASE}/api/ftpusers/{u['id']}", headers=H)

        # 1. 创建
        r = requests.post(f"{BASE}/api/ftpusers", headers=H, json=_valid_payload())
        check("创建 FTP 用户", r.status_code == 200, f"status={r.status_code} detail={r.text[:120]}")
        if r.status_code != 200:
            return
        uid = r.json()["id"]
        # 响应不含密码
        check("创建响应不含密码字段", "password" not in r.json(), str(sorted(r.json().keys())))

        # 2. 非法输入拒绝
        r = requests.post(f"{BASE}/api/ftpusers", headers=H, json=_valid_payload(username="bad user"))
        check("非法用户名被拒", r.status_code == 400, f"status={r.status_code}")
        r = requests.post(f"{BASE}/api/ftpusers", headers=H, json=_valid_payload(directory="rel/path"))
        check("非绝对路径被拒", r.status_code == 400, f"status={r.status_code}")
        r = requests.post(f"{BASE}/api/ftpusers", headers=H, json=_valid_payload(username="webuser"))
        check("重名被拒", r.status_code == 400, f"status={r.status_code}")

        # 3. 列表
        lst = requests.get(f"{BASE}/api/ftpusers", headers=H).json().get("users", [])
        check("列表包含新用户", any(u["id"] == uid for u in lst), str(len(lst)))
        check("列表不含密码字段", all("password" not in u for u in lst))

        # 4. 更新
        r = requests.put(f"{BASE}/api/ftpusers/{uid}", headers=H,
                         json={"enabled": False, "description": "E2E 改用途"})
        check("更新 FTP 用户", r.status_code == 200 and r.json()["enabled"] is False,
              f"status={r.status_code} body={r.text[:120]}")

        # 5. 删除
        check("删除 FTP 用户", requests.delete(f"{BASE}/api/ftpusers/{uid}", headers=H).status_code == 200)
        check("重复删除 404", requests.delete(f"{BASE}/api/ftpusers/{uid}", headers=H).status_code == 404)

    setup()
    try:
        run()
    finally:
        teardown()

    print(f"\nE2E 结果：PASS {PASS_N} 项，FAIL {FAIL_N} 项")
    sys.exit(1 if (unit_failed or FAIL_N) else 0)
