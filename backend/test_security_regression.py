"""
安全修复回归测试脚本

策略：
  - 脚本执行前备份 users.json，注入临时测试账号（secadmin/secuser），
    跑完自动恢复原文件，不影响真实账号。
  - 测试覆盖：安全响应头 / 路由权限分级 / 上传路径穿越 / appstore SSRF /
    登录限流 / 默认密码禁止 / 管理接口。

用法：
  python test_security_regression.py
"""
import requests
import json
import sys
import os
import shutil
import bcrypt

BASE = "http://localhost:8000"
USERS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "users.json")
BACKUP_FILE = USERS_FILE + ".bak_regression"

TEST_ADMIN = "secadmin"
TEST_USER = "secuser"
TEST_PASS = "SecPass#123"

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
    cfg_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "shunx.json")
    entry = None
    try:
        with open(cfg_file, "r", encoding="utf-8") as f:
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
    """备份并注入临时测试账号。"""
    shutil.copy2(USERS_FILE, BACKUP_FILE)
    with open(USERS_FILE, "r", encoding="utf-8") as f:
        users = json.load(f)
    pw = bcrypt.hashpw(TEST_PASS.encode(), bcrypt.gensalt()).decode()
    users[TEST_ADMIN] = {"username": TEST_ADMIN, "password": pw, "role": "admin",
                         "must_change_password": False, "created_at": 0}
    users[TEST_USER] = {"username": TEST_USER, "password": pw, "role": "user",
                        "must_change_password": False, "created_at": 0}
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)


def teardown():
    """恢复原始用户文件。"""
    if os.path.exists(BACKUP_FILE):
        shutil.move(BACKUP_FILE, USERS_FILE)


def main():
    global PASS, FAIL
    print("=" * 60)
    print("Graw 安全修复回归验证")
    print("=" * 60)
    setup()
    try:
        _run()
    finally:
        teardown()

    print("\n" + "=" * 60)
    total = PASS + FAIL
    print(f"结果: {PASS}/{total} 通过, {FAIL}/{total} 失败")
    if FAIL == 0:
        print("所有安全修复回归验证通过！")
    else:
        print(f"有 {FAIL} 项失败，请检查。")
    sys.exit(1 if FAIL > 0 else 0)


def _run():
    # ------------------------------------------------------------------
    # 1. 安全响应头
    # ------------------------------------------------------------------
    print("\n[1] 安全响应头")
    try:
        r = requests.get(f"{BASE}/api/health")
        body = r.json()
        check("health 返回 status=ok", body.get("status") == "ok",
              f"status={body.get('status')}")
        check("health 返回面板版本号",
              isinstance(body.get("version"), str) and bool(body.get("version")),
              f"version={body.get('version')}")
        check("health 响应无敏感信息", "detail" not in body and "password" not in body)
        csp = r.headers.get("Content-Security-Policy", "")
        check("CSP header 存在", "Content-Security-Policy" in r.headers)
        check("CSP 包含 default-src 'self'", "default-src 'self'" in csp)
        check("CSP 放行 Vue 需要的 unsafe-inline/unsafe-eval",
              "'unsafe-inline'" in csp and "'unsafe-eval'" in csp)
        check("X-Content-Type-Options: nosniff",
              r.headers.get("X-Content-Type-Options") == "nosniff")
        check("X-Frame-Options: DENY", r.headers.get("X-Frame-Options") == "DENY")
        check("Referrer-Policy: same-origin", r.headers.get("Referrer-Policy") == "same-origin")
    except Exception as e:
        fail("安全响应头", str(e))

    # ------------------------------------------------------------------
    # 2. 路由权限分级
    # ------------------------------------------------------------------
    print("\n[2] 路由权限分级")
    rtok = login(TEST_ADMIN, TEST_PASS).json()["token"]
    utok = login(TEST_USER, TEST_PASS).json()["token"]

    admin_routes = [
        ("GET", "/api/docker/containers"),
        ("GET", "/api/process/list"),
        ("GET", "/api/cron/list"),
        ("GET", "/api/firewall/rules"),
        ("GET", "/api/ssl/list"),
        ("GET", "/api/sites/list"),
        ("GET", "/api/logs/list"),
        ("GET", "/api/appstore/config"),
        ("GET", "/api/files/roots"),
    ]
    for method, path in admin_routes:
        try:
            r = requests.get(f"{BASE}{path}", headers=hdr(utok))
            check(f"ADMIN 路由 {path} 拒绝普通用户 (403)", r.status_code == 403,
                  f"status={r.status_code}")
        except Exception as e:
            fail(f"ADMIN 路由 {path}", str(e))

    protected_routes = [
        "/api/system/overview",
        "/api/system/network",
        "/api/system/info",
        "/api/notes/",
    ]
    for path in protected_routes:
        try:
            r = requests.get(f"{BASE}{path}", headers=hdr(utok))
            check(f"PROTECTED 路由 {path} 允许普通用户 (2xx)", 200 <= r.status_code < 300,
                  f"status={r.status_code}")
        except Exception as e:
            fail(f"PROTECTED 路由 {path}", str(e))

    for path in ["/api/system/overview", "/api/files/roots"]:
        try:
            r = requests.get(f"{BASE}{path}", headers=hdr(rtok))
            check(f"管理员访问 {path} (2xx)", 200 <= r.status_code < 300,
                  f"status={r.status_code}")
        except Exception as e:
            fail(f"管理员访问 {path}", str(e))

    # 未认证访问受保护路由
    r = requests.get(f"{BASE}/api/system/overview")
    check("未认证访问受保护路由 (401)", r.status_code == 401, f"status={r.status_code}")

    # ------------------------------------------------------------------
    # 3. 文件上传路径穿越
    # ------------------------------------------------------------------
    print("\n[3] 文件上传路径穿越")
    try:
        files = {"file": ("../../evil.txt", "malicious", "text/plain")}
        r = requests.post(f"{BASE}/api/files/upload",
                          data={"path": "/tmp"},
                          files=files, headers=hdr(rtok))
        # 无论 200（被消毒到 /tmp/evil.txt）还是 400/403，都不能写出到 /tmp 之外
        check("上传路径穿越文件名被处理 (200/400/403)",
              r.status_code in (200, 400, 403), f"status={r.status_code}")
        evil = os.path.join("/tmp", "evil.txt")
        check("恶意文件未写出到 /tmp 之外", not os.path.exists(
            os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "..", "..", "evil.txt"))))
    except Exception as e:
        fail("文件上传路径穿越", str(e))

    # ------------------------------------------------------------------
    # 4. appstore URL scheme 白名单（防 SSRF）
    # ------------------------------------------------------------------
    print("\n[4] appstore URL scheme 白名单")
    try:
        for bad_url in ["file:///etc/passwd", "ftp://example.com/x", "gopher://x"]:
            r = requests.put(f"{BASE}/api/appstore/config",
                             json={"index_url": bad_url}, headers=hdr(rtok))
            check(f"appstore 拒绝 {bad_url.split(':')[0]}://", r.status_code in (400, 403, 422),
                  f"status={r.status_code}")
    except Exception as e:
        fail("appstore URL scheme", str(e))
    # 恢复配置
    requests.put(f"{BASE}/api/appstore/config", json={"index_url": ""}, headers=hdr(rtok))

    # ------------------------------------------------------------------
    # 5. 登录限流
    # ------------------------------------------------------------------
    print("\n[5] 登录限流")
    try:
        for _ in range(5):
            login("nonexistent_user_xyz", "wrongpass")
        r = login("nonexistent_user_xyz", "wrongpass")
        check("登录失败 5 次后第 6 次被锁定 (403)", r.status_code == 403,
              f"status={r.status_code}")
    except Exception as e:
        fail("登录限流", str(e))

    # ------------------------------------------------------------------
    # 6. 默认密码禁止使用
    # ------------------------------------------------------------------
    print("\n[6] 默认密码检测")
    try:
        r = requests.post(f"{BASE}/api/auth/users",
                          json={"username": "test_default_pwd", "password": "admin123", "role": "user"},
                          headers=hdr(rtok))
        check("创建用户拒绝默认密码 (400)", r.status_code == 400, f"status={r.status_code}")
        # 重置密码为默认密码也应拒绝
        r = requests.put(f"{BASE}/api/auth/users/{TEST_USER}",
                         json={"password": "admin123"}, headers=hdr(rtok))
        check("重置密码拒绝默认密码 (400)", r.status_code == 400, f"status={r.status_code}")
    except Exception as e:
        fail("默认密码检测", str(e))

    # ------------------------------------------------------------------
    # 7. 管理接口可用性
    # ------------------------------------------------------------------
    print("\n[7] 管理接口可用性")
    r = requests.get(f"{BASE}/api/auth/users", headers=hdr(rtok))
    check("管理员可获取用户列表 (200)", r.status_code == 200, f"status={r.status_code}")


if __name__ == "__main__":
    main()