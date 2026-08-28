# -*- coding: utf-8 -*-
"""
ShunX 网页防篡改 - API 端到端冒烟测试（需后端运行在 8000 端口）

流程：
  1. 备份 users.json，注入临时管理员（tamtester），结束后自动恢复；
  2. 在系统临时目录创建站点根目录与受保护文件；
  3. 调用 /api/tamper/* 验证：创建防护 → 篡改 → 扫描自动回滚 →
     临时关闭 10 分钟 → 重新启用 → 完全关闭 → 重新启用；
  4. 清理临时目录与防护配置。

用法：
  python test_tamper_e2e.py
"""
import json
import os
import shutil
import sys
import tempfile
import time

import requests
import bcrypt

BASE = "http://localhost:8000"
DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
USERS_FILE = os.path.join(DATA_DIR, "users.json")
BACKUP_FILE = USERS_FILE + ".bak_tamper_e2e"

TEST_ADMIN = "tamtester"
TEST_PASS = "Tamper#123456"

PASS = 0
FAIL = 0


def ok(name, detail=""):
    global PASS
    PASS += 1
    msg = f"  PASS  {name}"
    if detail:
        msg += f"  ({detail})"
    print(msg)


def fail(name, detail):
    global FAIL
    FAIL += 1
    print(f"  FAIL  {name}: {detail}")


def check(name, cond, detail=""):
    if cond:
        ok(name, detail)
    else:
        fail(name, detail)


def _entry_headers():
    """ShunX 安全入口：登录需携带 X-ShunX-Entry 头（读配置，避免硬编码）。"""
    cfg_file = os.path.join(DATA_DIR, "shunx.json")
    entry = None
    try:
        with open(cfg_file, "r", encoding="utf-8") as f:
            entry = json.load(f).get("entry_path")
    # CodeQL [py/empty-except] 读取可选 shunx.json：未配置/损坏时静默忽略
    except Exception:
        pass
    return {"X-ShunX-Entry": entry} if entry else {}


def hdr(token):
    return {"Authorization": f"Bearer {token}"}


def setup():
    """备份并注入临时管理员账号。"""
    shutil.copy2(USERS_FILE, BACKUP_FILE)
    with open(USERS_FILE, "r", encoding="utf-8") as f:
        users = json.load(f)
    users[TEST_ADMIN] = {
        "username": TEST_ADMIN,
        "password": bcrypt.hashpw(TEST_PASS.encode("utf-8"), bcrypt.gensalt()).decode("utf-8"),
        "role": "admin",
        "must_change_password": False,
        "created_at": time.time(),
    }
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)


def teardown():
    """恢复原用户文件。"""
    if os.path.exists(BACKUP_FILE):
        shutil.copy2(BACKUP_FILE, USERS_FILE)
        os.remove(BACKUP_FILE)


def main():
    setup()
    site_root = tempfile.mkdtemp(prefix="graw_tamper_e2e_www_")
    site_id = "tamper-e2e"
    try:
        r = requests.post(
            f"{BASE}/api/auth/login",
            json={"username": TEST_ADMIN, "password": TEST_PASS},
            headers=_entry_headers(),
            timeout=15,
        )
        check("登录成功", r.status_code == 200, str(r.status_code))
        if r.status_code != 200:
            print("  无法继续：", r.text)
            return
        token = r.json()["token"]

        # 准备站点文件
        index = os.path.join(site_root, "index.html")
        with open(index, "w", encoding="utf-8") as f:
            f.write("<html>trusted</html>")
        logdir = os.path.join(site_root, "logs")
        os.makedirs(logdir, exist_ok=True)
        with open(os.path.join(logdir, "access.log"), "w", encoding="utf-8") as f:
            f.write("log-line-1")

        # 1) 创建防护
        r = requests.post(
            f"{BASE}/api/tamper/sites",
            headers=hdr(token),
            json={
                "site_id": site_id,
                "site_name": "E2E Site",
                "root": site_root,
                "protected_files": ["index.html", "logs/access.log"],
                "ignore_patterns": ["logs/**"],
                "backup_interval_minutes": 60,
                "scan_interval_seconds": 15,
            },
            timeout=30,
        )
        check("创建防护成功", r.status_code == 200, str(r.status_code) + " " + r.text[:200])
        if r.status_code == 200:
            check("基线已建立", r.json().get("protected_count") == 2, str(r.json().get("protected_count")))

        # 2) 篡改受保护文件 → 扫描自动回滚
        with open(index, "w", encoding="utf-8") as f:
            f.write("<html>EVIL</html>")
        r = requests.post(
            f"{BASE}/api/tamper/sites/{site_id}/scan-now",
            headers=hdr(token),
            timeout=30,
        )
        check("扫描检测到篡改", r.status_code == 200 and r.json().get("tampered_count") == 1,
              str(r.status_code) + " " + r.text[:200])
        with open(index, "r", encoding="utf-8") as f:
            content = f.read()
        check("受保护文件已自动回滚", content == "<html>trusted</html>", content)

        # 3) 篡改被忽略的日志 → 不判为篡改、不改动
        with open(os.path.join(logdir, "access.log"), "w", encoding="utf-8") as f:
            f.write("log-line-EVIL")
        r = requests.post(
            f"{BASE}/api/tamper/sites/{site_id}/scan-now",
            headers=hdr(token),
            timeout=30,
        )
        check("忽略文件不被判为篡改", r.status_code == 200 and r.json().get("tampered_count") == 0,
              r.text[:200])
        with open(os.path.join(logdir, "access.log"), "r", encoding="utf-8") as f:
            content = f.read()
        check("忽略文件内容未被改动", content == "log-line-EVIL", content)

        # 4) 临时关闭 10 分钟
        r = requests.post(f"{BASE}/api/tamper/disable", headers=hdr(token),
                          json={"minutes": 10, "mode": "temporary"}, timeout=15)
        check("临时关闭成功", r.status_code == 200 and r.json().get("temporarily_disabled") is True,
              r.text[:200])
        check("临时关闭后仍为启用态", r.json().get("enabled") is True, r.text[:200])

        # 5) 重新启用
        r = requests.post(f"{BASE}/api/tamper/enable", headers=hdr(token), timeout=15)
        check("重新启用成功", r.status_code == 200 and r.json().get("temporarily_disabled") is False,
              r.text[:200])

        # 6) 完全关闭（需手动开启）
        r = requests.post(f"{BASE}/api/tamper/disable", headers=hdr(token),
                          json={"mode": "manual"}, timeout=15)
        check("完全关闭成功", r.status_code == 200 and r.json().get("enabled") is False, r.text[:200])

        # 7) 再次启用（恢复运行）
        r = requests.post(f"{BASE}/api/tamper/enable", headers=hdr(token), timeout=15)
        check("重新启用（完全关闭后）", r.status_code == 200 and r.json().get("enabled") is True, r.text[:200])

        # 8) 状态与历史
        r = requests.get(f"{BASE}/api/tamper/status", headers=hdr(token), timeout=15)
        check("状态接口可用", r.status_code == 200 and r.json().get("site_count") >= 1, r.text[:200])
        r = requests.get(f"{BASE}/api/tamper/history", headers=hdr(token), timeout=15)
        check("历史接口可用", r.status_code == 200 and len(r.json().get("history", [])) >= 1, r.text[:200])

        # 9) 删除防护
        r = requests.delete(f"{BASE}/api/tamper/sites/{site_id}", headers=hdr(token), timeout=15)
        check("删除防护成功", r.status_code == 200, str(r.status_code) + " " + r.text[:200])

        # 10) 权限：普通写接口拒绝非法 root
        r = requests.post(
            f"{BASE}/api/tamper/sites",
            headers=hdr(token),
            json={"site_id": "bad", "root": "../relative", "protected_files": ["a.txt"]},
            timeout=15,
        )
        check("拒绝相对路径根目录", r.status_code == 400, str(r.status_code))
    finally:
        teardown()
        shutil.rmtree(site_root, ignore_errors=True)

    print(f"\n结果：通过 {PASS} 项，失败 {FAIL} 项")
    if FAIL:
        sys.exit(1)


if __name__ == "__main__":
    main()
