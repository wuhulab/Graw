# -*- coding: utf-8 -*-
"""
登录日志 / 异地登录提示 E2E 回归测试（需后端运行在 8000 端口）

策略：
  - 脚本执行前备份 users.json / login_logs.json / login_known.json，
    注入临时测试账号（lladmin 管理员 / lluser 普通用户），跑完自动恢复。
  - 通过真实登录接口触发登录日志写入，验证：
      成功登录记录 IP/设备/时间、首次登录标记「异常」、重复登录正常、
      新设备触发异常、普通用户 /mine 只见自己、/list 需管理员、
      配置开关与清空、权限隔离（普通用户访问管理接口 403）。

用法：
  python test_loginlog_e2e.py          # 默认 http://localhost:8000
  python test_loginlog_e2e.py 8011     # 指定端口
"""
import json
import os
import shutil
import sys

import bcrypt
import requests

PORT = sys.argv[1] if len(sys.argv) > 1 else "8000"
BASE = f"http://localhost:{PORT}"
HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
USERS_FILE = os.path.join(DATA, "users.json")
LOGS_FILE = os.path.join(DATA, "login_logs.json")
KNOWN_FILE = os.path.join(DATA, "login_known.json")

BACKUP = {
    USERS_FILE: USERS_FILE + ".bak_loginloge2e",
    LOGS_FILE: LOGS_FILE + ".bak_loginloge2e",
    KNOWN_FILE: KNOWN_FILE + ".bak_loginloge2e",
}

ADMIN = "lladmin"
USER = "lluser"
PASS = "LlPass#123"
# 使用可被解析为不同「设备」的真实 UA，验证新设备异常检测
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0 Safari/537.36"  # Chrome · Windows
UA_OTHER = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0) Version/17.0 Mobile Safari/604.1"  # Safari · iPhone

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
    cfg = os.path.join(DATA, "shunx.json")
    entry = None
    try:
        with open(cfg, "r", encoding="utf-8") as f:
            entry = json.load(f).get("entry_path")
    except Exception:
        pass
    return {"X-ShunX-Entry": entry} if entry else {}


def login(username, password, ua):
    """真实登录：返回 (响应, 是否成功)。"""
    r = requests.post(
        f"{BASE}/api/auth/login",
        json={"username": username, "password": password},
        headers={**_entry_headers(), "User-Agent": ua},
        timeout=10,
    )
    return r


def hdr(token):
    return {"Authorization": f"Bearer {token}", "User-Agent": UA}


def setup():
    # 备份三个数据文件
    for src, dst in BACKUP.items():
        if os.path.exists(src):
            shutil.copy2(src, dst)
    # 注入测试账号
    with open(USERS_FILE, "r", encoding="utf-8") as f:
        users = json.load(f)
    pw = bcrypt.hashpw(PASS.encode(), bcrypt.gensalt()).decode()
    users[ADMIN] = {"username": ADMIN, "password": pw, "role": "admin",
                    "must_change_password": False, "created_at": 0}
    users[USER] = {"username": USER, "password": pw, "role": "user",
                   "must_change_password": False, "created_at": 0}
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)
    # 清理本次测试账号可能残留的指纹（保证「首次登录=异常」可复现）
    known = {}
    if os.path.exists(KNOWN_FILE):
        try:
            with open(KNOWN_FILE, "r", encoding="utf-8") as f:
                known = json.load(f)
        except Exception:
            known = {}
    for name in (ADMIN, USER):
        known.pop(name, None)
    with open(KNOWN_FILE, "w", encoding="utf-8") as f:
        json.dump(known, f, ensure_ascii=False, indent=2)


def teardown():
    # 恢复数据文件：原本存在 → 从备份还原；原本不存在 → 删除测试产物
    for src, dst in BACKUP.items():
        if os.path.exists(dst):
            shutil.copy2(dst, src)
            os.remove(dst)
        else:
            if os.path.exists(src):
                os.remove(src)


def main():
    setup()
    try:
        # 1. 管理员首次登录（新 IP + 新设备）→ 异常
        r = login(ADMIN, PASS, UA)
        check("admin 首次登录成功", r.status_code == 200 and r.json().get("token"))
        admin_token = r.json().get("token", "") if r.status_code == 200 else ""
        check("admin 首次登录返回 token", bool(admin_token))

        r = requests.get(f"{BASE}/api/loginlog/mine", headers=hdr(admin_token), timeout=10)
        check("mine 接口可用", r.status_code == 200, f"status={r.status_code}")
        logs = r.json().get("logs", []) if r.status_code == 200 else []
        mine = [x for x in logs if x.get("username") == ADMIN]
        check("首次登录已记录", len(mine) >= 1, f"count={len(mine)}")
        first = mine[0]
        check("记录包含 IP/设备/时间", first.get("ip") and first.get("device") and first.get("time"))
        check("首次登录标记异常", first.get("abnormal") is True, first.get("abnormal_reason") or "")
        check("设备解析正常", "Chrome" in (first.get("device") or ""), first.get("device") or "")

        # 2. 管理员同 IP 同设备再次登录 → 正常（不再异常）
        r = login(ADMIN, PASS, UA)
        check("admin 重复登录成功", r.status_code == 200)
        r = requests.get(f"{BASE}/api/loginlog/mine", headers=hdr(admin_token), timeout=10)
        logs = r.json().get("logs", [])
        mine = [x for x in logs if x.get("username") == ADMIN]
        check("重复登录已记录", len(mine) >= 2, f"count={len(mine)}")
        check("重复登录不再异常", mine[0].get("abnormal") is False)

        # 3. 管理员换新设备登录 → 异常（新设备）
        r = login(ADMIN, PASS, UA_OTHER)
        check("admin 新设备登录成功", r.status_code == 200)
        r = requests.get(f"{BASE}/api/loginlog/mine", headers=hdr(admin_token), timeout=10)
        logs = r.json().get("logs", [])
        mine = [x for x in logs if x.get("username") == ADMIN]
        check("新设备登录标记异常", mine[0].get("abnormal") is True, mine[0].get("abnormal_reason") or "")

        # 4. 登录失败也被记录
        r = login(ADMIN, "WrongPass#1", UA)
        check("错误密码登录返回 401", r.status_code == 401)
        r = requests.get(f"{BASE}/api/loginlog/list", headers=hdr(admin_token), timeout=10)
        logs = r.json().get("logs", [])
        failed = [x for x in logs if x.get("username") == ADMIN and x.get("status") == "failed"]
        check("失败登录已记录", len(failed) >= 1, f"count={len(failed)}")

        # 5. 普通用户登录 + 权限隔离
        r = login(USER, PASS, UA)
        check("普通用户登录成功", r.status_code == 200 and r.json().get("token"))
        user_token = r.json().get("token", "") if r.status_code == 200 else ""
        r = requests.get(f"{BASE}/api/loginlog/mine", headers=hdr(user_token), timeout=10)
        logs = r.json().get("logs", [])
        check("普通用户 mine 只见自己", all(x.get("username") == USER for x in logs) and len(logs) >= 1, f"count={len(logs)}")
        r = requests.get(f"{BASE}/api/loginlog/list", headers=hdr(user_token), timeout=10)
        check("普通用户访问 list 被拒 403", r.status_code == 403, f"status={r.status_code}")
        r = requests.post(f"{BASE}/api/loginlog/clear", headers=hdr(user_token), timeout=10)
        check("普通用户清空被拒 403", r.status_code == 403, f"status={r.status_code}")
        r = requests.put(f"{BASE}/api/loginlog/config", json={"alert_enabled": False}, headers=hdr(user_token), timeout=10)
        check("普通用户改配置被拒 403", r.status_code == 403, f"status={r.status_code}")

        # 6. 管理员状态 / 列表 / 配置开关 / 清空
        r = requests.get(f"{BASE}/api/loginlog/status", headers=hdr(admin_token), timeout=10)
        check("status 接口可用", r.status_code == 200)
        st = r.json()
        check("status 统计正确", st.get("total", 0) >= 3 and st.get("success", 0) >= 3, json.dumps(st))
        r = requests.put(f"{BASE}/api/loginlog/config", json={"alert_enabled": False}, headers=hdr(admin_token), timeout=10)
        check("管理员可关闭提醒", r.status_code == 200)
        r = requests.get(f"{BASE}/api/loginlog/status", headers=hdr(admin_token), timeout=10)
        check("关闭后 status 回读为 false", r.json().get("alert_enabled") is False)
        r = requests.put(f"{BASE}/api/loginlog/config", json={"alert_enabled": True}, headers=hdr(admin_token), timeout=10)
        check("管理员可重新开启提醒", r.status_code == 200)
        r = requests.post(f"{BASE}/api/loginlog/clear", headers=hdr(admin_token), timeout=10)
        check("管理员清空成功", r.status_code == 200)
        r = requests.get(f"{BASE}/api/loginlog/list", headers=hdr(admin_token), timeout=10)
        check("清空后列表为空", len(r.json().get("logs", [])) == 0)

        # 7. 列表过滤
        login(ADMIN, PASS, UA)  # 再制造一条记录
        r = requests.get(f"{BASE}/api/loginlog/list", params={"username": ADMIN}, headers=hdr(admin_token), timeout=10)
        check("list 按账号过滤", len(r.json().get("logs", [])) >= 1)
        r = requests.get(f"{BASE}/api/loginlog/list", params={"status": "success"}, headers=hdr(admin_token), timeout=10)
        check("list 按状态过滤", all(x.get("status") == "success" for x in r.json().get("logs", [])))

        # 8. 测试推送（无渠道时静默返回 ok）
        r = requests.post(f"{BASE}/api/loginlog/test-alert", headers=hdr(admin_token), timeout=10)
        check("test-alert 接口可用", r.status_code == 200)

    finally:
        teardown()

    print(f"\n结果：PASS {PASS_N} / FAIL {FAIL_N}")
    sys.exit(0 if FAIL_N == 0 else 1)


if __name__ == "__main__":
    main()
