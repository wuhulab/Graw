# -*- coding: utf-8 -*-
"""
两步验证（2FA）E2E 回归测试（需后端运行在 8000 端口）

策略：
  - 脚本执行前备份 users.json，注入临时测试账号（otpadm），跑完自动恢复。
  - 走真实 API 验证完整链路：登录 → 生成密钥 → 验证码启用 → 登录需验证码 →
    错误码拒绝 → 正确码放行 → 验证码关闭 → 恢复直接登录。

用法：
  python test_2fa_e2e.py          # 默认 http://localhost:8000
  python test_2fa_e2e.py 8011     # 指定端口
"""
import json
import os
import shutil
import sys
import time
import bcrypt
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from app.auth import _totp_at  # noqa: E402

PORT = sys.argv[1] if len(sys.argv) > 1 else "8000"
BASE = f"http://localhost:{PORT}"
HERE = os.path.dirname(os.path.abspath(__file__))
USERS_FILE = os.path.join(HERE, "data", "users.json")
BACKUP_FILE = USERS_FILE + ".bak_2fae2e"

USERNAME = "otpadm"
PASS = "OtP#Pass123"

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


def login(username, password, otp_code=None):
    body = {"username": username, "password": password}
    if otp_code is not None:
        body["otp_code"] = otp_code
    return requests.post(f"{BASE}/api/auth/login", json=body, headers=_entry_headers())


def hdr(token):
    return {"Authorization": f"Bearer {token}"}


def setup():
    shutil.copy2(USERS_FILE, BACKUP_FILE)
    with open(USERS_FILE, "r", encoding="utf-8") as f:
        users = json.load(f)
    pw = bcrypt.hashpw(PASS.encode(), bcrypt.gensalt()).decode()
    users[USERNAME] = {"username": USERNAME, "password": pw, "role": "admin",
                       "must_change_password": False, "token_version": 0, "created_at": 0}
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
    # 1. 无 2FA 直接登录
    r = login(USERNAME, PASS)
    check("无 2FA 直接登录", r.status_code == 200, f"status={r.status_code}")
    if r.status_code != 200:
        return
    token = r.json()["token"]
    H = hdr(token)

    # 2. 初始 2FA 状态
    st = requests.get(f"{BASE}/api/auth/2fa/status", headers=H).json()
    check("初始未启用 2FA", st["otp_enabled"] is False)

    # 3. 生成密钥
    r = requests.post(f"{BASE}/api/auth/2fa/setup", headers=H)
    check("生成 2FA 密钥", r.status_code == 200, f"status={r.status_code}")
    secret = r.json()["secret"]
    check("返回 otpauth URI", "otpauth://totp/" in r.json()["otpauth_uri"])

    # 4. 错误验证码启用失败
    r = requests.post(f"{BASE}/api/auth/2fa/enable", headers=H, json={"code": "000000"})
    check("错误验证码启用被拒", r.status_code == 400, f"status={r.status_code}")

    # 5. 正确验证码启用成功
    code = _totp_at(secret, int(time.time()) // 30)
    r = requests.post(f"{BASE}/api/auth/2fa/enable", headers=H, json={"code": code})
    check("正确验证码启用", r.status_code == 200, f"status={r.status_code}")

    # 6. 启用后：无验证码登录 → otp_required 且无 token
    r = login(USERNAME, PASS)
    check("启用后需二次验证", r.status_code == 200 and r.json().get("otp_required"),
          f"status={r.status_code} body={r.text[:100]}")
    check("未签发 token", r.json().get("token", "") == "")

    # 7. 错误验证码 → 401
    r = login(USERNAME, PASS, otp_code="000000")
    check("错误验证码登录被拒", r.status_code == 401, f"status={r.status_code}")

    # 8. 正确验证码 → 200 签发 token
    code = _totp_at(secret, int(time.time()) // 30)
    r = login(USERNAME, PASS, otp_code=code)
    check("正确验证码登录成功", r.status_code == 200 and r.json().get("token"),
          f"status={r.status_code}")
    check("用户信息含 otp_enabled", r.json().get("user", {}).get("otp_enabled") is True)

    # 9. 用新 token 关闭 2FA（错误码拒绝 / 正确码通过）
    H2 = hdr(r.json()["token"])
    r = requests.post(f"{BASE}/api/auth/2fa/disable", headers=H2, json={"code": "000000"})
    check("错误验证码关闭被拒", r.status_code == 400, f"status={r.status_code}")
    code = _totp_at(secret, int(time.time()) // 30)
    r = requests.post(f"{BASE}/api/auth/2fa/disable", headers=H2, json={"code": code})
    check("正确验证码关闭 2FA", r.status_code == 200, f"status={r.status_code}")

    # 10. 关闭后直接登录
    r = login(USERNAME, PASS)
    check("关闭后恢复直接登录", r.status_code == 200 and r.json().get("token"),
          f"status={r.status_code}")

    print(f"\n结果：PASS {PASS_N} 项，FAIL {FAIL_N} 项")
    sys.exit(1 if FAIL_N else 0)


if __name__ == "__main__":
    main()
