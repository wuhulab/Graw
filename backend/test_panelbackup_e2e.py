# -*- coding: utf-8 -*-
"""
面板自身备份 E2E 回归测试（需后端运行在 8000 端口）

策略：
  - 注入临时管理员/普通用户，验证导出、列表、下载、删除与权限分级。
  - 破坏性「导入恢复」已在单元测试覆盖（临时目录隔离）；此处仅验证
    非法归档被拒绝（不触碰真实 data/）。

用法：
  python test_panelbackup_e2e.py          # 默认 http://localhost:8000
  python test_panelbackup_e2e.py 8011     # 指定端口
"""
import io
import json
import os
import shutil
import sys
import tarfile
import bcrypt
import requests

PORT = sys.argv[1] if len(sys.argv) > 1 else "8000"
BASE = f"http://localhost:{PORT}"
HERE = os.path.dirname(os.path.abspath(__file__))
USERS_FILE = os.path.join(HERE, "data", "users.json")
BACKUP_FILE = USERS_FILE + ".bak_pbe2e"

ADMIN = "pbadmin"
USER = "pbuser"
PASS = "PbPass#123"

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


def main():
    setup()
    try:
        run()
    finally:
        teardown()


def run():
    r = login(ADMIN, PASS)
    check("管理员登录", r.status_code == 200, f"status={r.status_code}")
    if r.status_code != 200:
        return
    token = r.json()["token"]
    H = hdr(token)

    ru = login(USER, PASS)
    HU = hdr(ru.json()["token"]) if ru.status_code == 200 else None

    # 权限分级
    rp = requests.get(f"{BASE}/api/panelbackup/list", headers=HU)
    check("普通用户访问面板备份被拒", rp.status_code == 403, f"status={rp.status_code}")

    # 1. 导出
    r = requests.post(f"{BASE}/api/panelbackup/export", headers=H)
    check("导出配置归档", r.status_code == 200, f"status={r.status_code} detail={r.text[:120]}")
    name = r.json().get("name", "") if r.status_code == 200 else ""
    check("归档文件名合法", bool(name) and name.endswith(".tar.gz"), name)

    # 2. 列表
    archives = requests.get(f"{BASE}/api/panelbackup/list", headers=H).json()["archives"]
    check("列表含新归档", any(a["name"] == name for a in archives), f"count={len(archives)}")

    # 3. 下载
    d = requests.get(f"{BASE}/api/panelbackup/download/{name}", headers=H)
    check("下载归档", d.status_code == 200, f"status={d.status_code}")
    check("下载为 gzip", d.content[:2] == b"\x1f\x8b")
    # 非法名下载拒绝
    check("非法名下载被拒", requests.get(f"{BASE}/api/panelbackup/download/evil.tar.gz", headers=H).status_code == 400)

    # 4. 非法导入拒绝（非 tar.gz）
    r = requests.post(f"{BASE}/api/panelbackup/import", headers=H,
                      files={"file": ("x.txt", b"hello", "text/plain")})
    check("非 tar.gz 导入被拒", r.status_code == 400, f"status={r.status_code}")

    # 5. 含越界路径的 tar.gz 导入被拒（不触碰 data/）
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tf:
        info = tarfile.TarInfo("../evil.txt")
        data = b"pwned"
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    r = requests.post(f"{BASE}/api/panelbackup/import", headers=H,
                      files={"file": ("evil.tar.gz", buf.getvalue(), "application/gzip")})
    check("越界归档导入被拒", r.status_code == 400, f"status={r.status_code}")

    # 6. 删除归档
    r = requests.delete(f"{BASE}/api/panelbackup/{name}", headers=H)
    check("删除归档", r.status_code == 200, f"status={r.status_code}")
    archives = requests.get(f"{BASE}/api/panelbackup/list", headers=H).json()["archives"]
    check("删除后列表为空", all(a["name"] != name for a in archives))

    print(f"\n结果：PASS {PASS_N} 项，FAIL {FAIL_N} 项")
    sys.exit(1 if FAIL_N else 0)


if __name__ == "__main__":
    main()
