# -*- coding: utf-8 -*-
"""
第七轮安全审计 - 攻击模拟：ShunX 入口路径对普通用户的泄露验证（修复版断言）

攻击链（修复前）：
  任意普通用户账号（低权限）登录 → GET /api/shunx/config
  → 响应包含完整 entry_path（如 shunianssy）
  → ShunX 安全入口的核心秘密直接暴露给低权限账号；
  低权限账号易被攻破/泄露（弱密码、离职员工等），等价于入口保护失效。

修复语义（app/routers/shunx.py）：
  GET /api/shunx/config 按角色脱敏——管理员返回完整 entry_path
  （设置窗口需要回填展示），普通用户只返回 entry_path=None + enabled 标记。

运行前提：后端运行在 127.0.0.1:8000。
脚本会临时备份 users.json 注入测试账号，跑完自动恢复。
"""
import json
import os
import shutil
import sys
import time

import requests

BASE = "http://127.0.0.1:8000"
USERS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "users.json")
BACKUP_FILE = USERS_FILE + ".bak_shunx_config"

ADMIN_USER = "shunx_cfg_admin"
NORMAL_USER = "shunx_cfg_user"
ADMIN_PW = "Cfg!Admin2026x"
NORMAL_PW = "Cfg!User2026x"


def _inject_users() -> None:
    """备份 users.json 并注入测试管理员/普通用户（复用面板自带的 bcrypt）。"""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from app.auth import hash_password  # noqa: E402

    shutil.copy2(USERS_FILE, BACKUP_FILE)
    with open(USERS_FILE, "r", encoding="utf-8") as f:
        users = json.load(f)
    now = time.time()
    users[ADMIN_USER] = {
        "username": ADMIN_USER, "password": hash_password(ADMIN_PW),
        "role": "admin", "must_change_password": False, "created_at": now,
    }
    users[NORMAL_USER] = {
        "username": NORMAL_USER, "password": hash_password(NORMAL_PW),
        "role": "user", "must_change_password": False, "created_at": now,
    }
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)


def _restore_users() -> None:
    """恢复原始 users.json。"""
    if os.path.exists(BACKUP_FILE):
        shutil.move(BACKUP_FILE, USERS_FILE)


def _login(username: str, password: str) -> str:
    """登录并返回 token（失败抛异常）。"""
    r = requests.post(
        BASE + "/api/auth/login",
        json={"username": username, "password": password},
        headers={"X-ShunX-Entry": "shunianssy"},  # 已知真实入口，绕过入口校验
        timeout=15,
    )
    r.raise_for_status()
    return r.json()["token"]


def main() -> int:
    injected = False
    checks = []
    try:
        _inject_users()
        injected = True
        print("[*] 已注入临时测试账号（跑完自动恢复）")

        # 1. 普通用户登录：入口路径必须脱敏
        user_token = _login(NORMAL_USER, NORMAL_PW)
        r = requests.get(BASE + "/api/shunx/config",
                         headers={"Authorization": f"Bearer {user_token}"}, timeout=15)
        data = r.json()
        print(f"[*] 普通用户 GET /shunx/config -> {r.status_code}: {data}")
        checks.append(("普通用户读取配置返回 200", r.status_code == 200))
        checks.append(("普通用户不回传 entry_path（脱敏为 None）", data.get("entry_path") is None))
        checks.append(("普通用户仍可见 enabled 标记（前端判断用）", data.get("enabled") is True))

        # 2. 管理员登录：完整配置可见（设置窗口功能保留）
        admin_token = _login(ADMIN_USER, ADMIN_PW)
        r = requests.get(BASE + "/api/shunx/config",
                         headers={"Authorization": f"Bearer {admin_token}"}, timeout=15)
        data = r.json()
        print(f"[*] 管理员 GET /shunx/config -> {r.status_code}: {data}")
        checks.append(("管理员可见完整 entry_path（功能保留）", bool(data.get("entry_path"))))
        checks.append(("管理员 enabled 为 true", data.get("enabled") is True))

        # 3. 未认证访问仍然 401
        r = requests.get(BASE + "/api/shunx/config", timeout=15)
        checks.append(("未认证访问 shunx/config 返回 401", r.status_code == 401))
    finally:
        if injected:
            _restore_users()
            print("[*] users.json 已恢复")

    print("\n===== 攻击模拟结论（修复后） =====")
    ok = True
    for name, passed in checks:
        print(f"{'PASS' if passed else 'FAIL'} | {name}")
        ok = ok and passed
    if ok:
        print("\n[修复确认] 普通用户无法再读取安全入口路径，管理员功能不受影响")
    else:
        print("\n[!] 入口路径仍对普通用户泄露")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
