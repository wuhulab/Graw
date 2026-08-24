# -*- coding: utf-8 -*-
"""
第六轮安全审查攻击模拟脚本。

聚焦本轮静态审查发现的新攻击面：
  A. Windows 设备命名空间前缀（\\?\\ 与 \\\\.\\）对 commonpath 数据目录
     防护的 fail-open 绕过（files / logs）；
  B. 路径穿越经典变体回归（大小写 / URL 编码 / 尾随点 / ADS 流）；
  C. 普通用户越权访问 ADMIN 路由回归；
  D. WebSocket 未授权连接回归；
  E. ShunX 安全入口头伪造回归。

运行前置：后端已运行在 127.0.0.1:8000。
脚本会临时备份 users.json 并注入测试账号（secadmin6/secuser6），跑完恢复。
"""
import json
import os
import shutil
import sys

import bcrypt
import requests

BASE = "http://127.0.0.1:8000"
HERE = os.path.dirname(os.path.abspath(__file__))
USERS_FILE = os.path.join(HERE, "data", "users.json")
BACKUP_FILE = USERS_FILE + ".round6.bak"
TEST_ADMIN = "secadmin6"
TEST_USER = "secuser6"
TEST_PASS = "R6-test-Pass123"

PASS = 0
FAIL = 0


def ok(name, detail=""):
    global PASS
    PASS += 1
    print(f"  PASS  {name}" + (f"  [{detail}]" if detail else ""))


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
    """读取 ShunX 入口配置，登录请求需携带对应头。"""
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
                         headers=_entry_headers(), timeout=15)


def hdr(token):
    return {"Authorization": f"Bearer {token}"}


def setup():
    """备份并注入临时测试账号（管理员 / 普通用户各一）。"""
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
    print("=" * 64)
    print("第六轮安全审查 · 攻击模拟")
    print("=" * 64)

    setup()
    try:
        # ---- 登录 ----
        r = login(TEST_ADMIN, TEST_PASS)
        check("管理员登录", r.status_code == 200, str(r.status_code))
        admin_tok = r.json().get("token", "")
        r = login(TEST_USER, TEST_PASS)
        check("普通用户登录", r.status_code == 200, str(r.status_code))
        user_tok = r.json().get("token", "")

        # ============================================================
        # A 组：设备命名空间前缀绕过 data 目录防护（本轮新发现）
        # ============================================================
        print("\n[A] 设备命名空间前缀（\\\\?\\）绕过 data 目录防护")
        # \\?\ 前缀：commonpath 因盘符解析差异抛 ValueError -> fail-open；
        # 修复后入口直接拒绝（400），或包含性判定拦截（403）。
        # 核心断言：绝不返回 200（修复前实测 200 并回传文件内容）。
        evil = r"\\?\S:\Graw\backend\data\secret.key"
        r = requests.get(f"{BASE}/api/files/read",
                         params={"path": evil}, headers=hdr(admin_tok), timeout=15)
        check("files/read \\\\?\\secret.key 被拦截", r.status_code in (400, 403),
              f"status={r.status_code}（若 200 则防护被绕过，secret.key 泄露）")
        if r.status_code == 200:
            fail("secret.key 泄露！", f"响应长度 {len(r.text)}")

        evil_users = r"\\?\S:\Graw\backend\data\users.json"
        r = requests.get(f"{BASE}/api/files/read",
                         params={"path": evil_users}, headers=hdr(admin_tok), timeout=15)
        check("files/read \\\\?\\users.json 被拦截", r.status_code in (400, 403),
              f"status={r.status_code}")

        r = requests.get(f"{BASE}/api/files/download",
                         params={"path": evil_users}, headers=hdr(admin_tok), timeout=15)
        check("files/download \\\\?\\ 被拦截", r.status_code in (400, 403),
              f"status={r.status_code}")

        r = requests.get(f"{BASE}/api/logs/read",
                         params={"path": evil_users}, headers=hdr(admin_tok), timeout=15)
        check("logs/read \\\\?\\ 被拦截", r.status_code in (400, 403),
              f"status={r.status_code}")

        # \\.\ 设备路径变体（COM1 / CON 等设备名）
        evil_dev = r"\\.\CON"
        r = requests.get(f"{BASE}/api/files/read",
                         params={"path": evil_dev}, headers=hdr(admin_tok), timeout=15)
        check("files/read \\\\.\\CON 被拒绝", r.status_code in (400, 403, 404, 500),
              f"status={r.status_code}（不得返回 200 内容）")

        # ============================================================
        # B 组：经典路径穿越变体回归（应全部拦截）
        # ============================================================
        print("\n[B] 经典穿越变体回归")
        variants = [
            ("大小写混合", r"S:\GRaw\BACKEND\DATA\users.json"),
            ("尾随点+分隔", r"S:\Graw\backend\data\.\users.json"),
            ("ADS 流", r"S:\Graw\backend\data\users.json::$DATA"),
            ("父目录回溯", r"S:\Graw\backend\app\..\data\users.json"),
        ]
        for label, p in variants:
            r = requests.get(f"{BASE}/api/files/read",
                             params={"path": p}, headers=hdr(admin_tok), timeout=15)
            check(f"files/read {label} 被拦截", r.status_code == 403,
                  f"status={r.status_code}")

        # ============================================================
        # C 组：普通用户越权（应全部 403）
        # ============================================================
        print("\n[C] 普通用户越权访问 ADMIN 路由")
        admin_endpoints = [
            ("GET", "/api/files/list"),
            ("GET", "/api/docker/status"),
            ("GET", "/api/process/list"),
            ("GET", "/api/sites/list"),
            ("GET", "/api/cron/list"),
            ("GET", "/api/appstore/index"),
            ("GET", "/api/tasks"),
            ("GET", "/api/nodes"),
        ]
        for method, url in admin_endpoints:
            r = requests.request(method, BASE + url, headers=hdr(user_tok), timeout=15)
            check(f"user -> {url} 403", r.status_code == 403, f"status={r.status_code}")

        # 普通用户读取 data 目录（即使穿越成功也应被角色拦截）
        r = requests.get(f"{BASE}/api/files/read",
                         params={"path": evil_users}, headers=hdr(user_tok), timeout=15)
        check("user -> files/read \\\\?\\ 403", r.status_code == 403,
              f"status={r.status_code}")

        # ============================================================
        # D 组：WebSocket 未授权连接（应被关闭，收不到数据帧）
        # ============================================================
        print("\n[D] WebSocket 未授权连接")
        try:
            import websockets

            async def _ws_probe(url):
                """尝试无 token 连接：预期握手失败或立即被服务端关闭。"""
                try:
                    async with websockets.connect(url, open_timeout=5) as ws:
                        try:
                            msg = await asyncio.wait_for(ws.recv(), timeout=3)
                            return "GOT_DATA", msg
                        except asyncio.TimeoutError:
                            return "SILENT", ""
                        except websockets.ConnectionClosed as e:
                            return "CLOSED", str(e.code)
                except Exception as e:
                    return "REJECT", str(e)[:60]

            import asyncio
            for url in ("ws://127.0.0.1:8000/api/system/ws",
                        "ws://127.0.0.1:8000/api/terminal/ws",
                        "ws://127.0.0.1:8000/api/tamper/ws"):
                state, detail = asyncio.run(_ws_probe(url))
                check(f"WS 无 token {url.rsplit('/', 2)[-2]} 拒绝",
                      state in ("REJECT", "CLOSED", "SILENT"), f"{state} {detail}")
        except ImportError:
            print("  SKIP  websockets 未安装，跳过 WS 探测")

        # ============================================================
        # E 组：ShunX 入口头伪造（应 403 且计入限流）
        # ============================================================
        print("\n[E] ShunX 入口头伪造")
        r = requests.post(f"{BASE}/api/auth/login",
                          json={"username": TEST_ADMIN, "password": TEST_PASS},
                          headers={"X-ShunX-Entry": "wrong-entry"}, timeout=15)
        check("伪造入口头登录被拒", r.status_code == 403, f"status={r.status_code}")
    finally:
        teardown()

    print("\n" + "=" * 64)
    print(f"结果：PASS={PASS}  FAIL={FAIL}")
    print("=" * 64)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
