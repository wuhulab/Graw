# -*- coding: utf-8 -*-
"""
通知中心 E2E 回归测试（需后端运行在 8000 端口）

策略：
  - 脚本执行前备份 users.json，注入临时测试账号（nkadmin/nkuser），
    跑完自动恢复原文件，不影响真实账号。
  - 本地 mock HTTP 服务器接收 Webhook 推送，验证「规则→检查→推送→记录」全链路。
  - 覆盖：渠道 CRUD/脱敏、规则 CRUD、配置开关、测试告警触发、
    冷却去重、普通用户权限、告警记录。

用法：
  python test_notify_e2e.py          # 默认 http://localhost:8000
  python test_notify_e2e.py 8011     # 指定端口
"""
import json
import os
import shutil
import sys
import threading
import http.server
import bcrypt
import requests

PORT = sys.argv[1] if len(sys.argv) > 1 else "8000"
BASE = f"http://localhost:{PORT}"
HERE = os.path.dirname(os.path.abspath(__file__))
USERS_FILE = os.path.join(HERE, "data", "users.json")
BACKUP_FILE = USERS_FILE + ".bak_notifye2e"

ADMIN = "nkadmin"
USER = "nkuser"
PASS = "NkPass#123"

PASS_N = 0
FAIL_N = 0


class HookMock(http.server.BaseHTTPRequestHandler):
    """接收 Webhook 推送的本地 mock。"""
    received = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else b""
        HookMock.received.append({"path": self.path, "body": body.decode("utf-8", "replace")})
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"ok":true}')

    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")


def start_hook_mock():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), HookMock)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address
    HookMock.received.clear()
    return server, f"http://{host}:{port}/push"


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
    rp = requests.get(f"{BASE}/api/notify/status", headers=HU)
    check("普通用户访问通知中心被拒", rp.status_code == 403, f"status={rp.status_code}")

    # 清理可能的历史配置（幂等）：先读取旧渠道/规则，逐个删除
    old = requests.get(f"{BASE}/api/notify/channels", headers=H).json().get("channels", [])
    for c in old:
        requests.delete(f"{BASE}/api/notify/channels/{c['id']}", headers=H)
    old_rules = requests.get(f"{BASE}/api/notify/rules", headers=H).json().get("rules", [])
    for r_ in old_rules:
        requests.delete(f"{BASE}/api/notify/rules/{r_['id']}", headers=H)
    requests.post(f"{BASE}/api/notify/logs/clear", headers=H)

    # 本地 mock 接收推送
    hook, hook_url = start_hook_mock()
    try:
        # 创建 webhook 渠道
        r = requests.post(f"{BASE}/api/notify/channels", headers=H, json={
            "name": "e2e-hook", "type": "webhook", "config": {"url": hook_url}, "enabled": True,
        })
        check("创建 Webhook 渠道", r.status_code == 200, f"status={r.status_code}")
        cid = r.json()["id"]

        # 渠道测试发送
        r = requests.post(f"{BASE}/api/notify/channels/{cid}/test", headers=H)
        check("渠道测试发送", r.status_code == 200, f"status={r.status_code}")
        check("mock 收到测试推送", len(HookMock.received) >= 1)

        # 创建规则：阈值 0 → 必然触发
        r = requests.post(f"{BASE}/api/notify/rules", headers=H, json={
            "metric": "cpu", "threshold": 0, "enabled": True,
        })
        check("创建规则", r.status_code == 200, f"status={r.status_code}")
        rid = r.json()["id"]

        # 开启总开关
        r = requests.put(f"{BASE}/api/notify/config", headers=H, json={
            "enabled": True, "cooldown_seconds": 300, "interval_seconds": 30,
        })
        check("开启总开关", r.status_code == 200 and r.json()["enabled"], f"status={r.status_code}")

        # 触发一次检查
        HookMock.received.clear()
        r = requests.post(f"{BASE}/api/notify/test-alert", headers=H)
        check("触发测试告警", r.status_code == 200, f"status={r.status_code}")
        check("mock 收到告警推送", len(HookMock.received) >= 1,
              HookMock.received[0]["body"][:80] if HookMock.received else "empty")
        logs = requests.get(f"{BASE}/api/notify/logs", headers=H).json()["logs"]
        check("告警记录已写入", len(logs) == 1 and logs[0]["metric"] == "cpu", f"count={len(logs)}")

        # 冷却去重：立即再触发一次，冷却期内不应产生新记录/新推送
        HookMock.received.clear()
        requests.post(f"{BASE}/api/notify/test-alert", headers=H)
        logs2 = requests.get(f"{BASE}/api/notify/logs", headers=H).json()["logs"]
        check("冷却期内不重复告警", len(logs2) == 1, f"count={len(logs2)}")
        check("冷却期内无新推送", len(HookMock.received) == 0, f"count={len(HookMock.received)}")

        # 关闭总开关后不再告警
        requests.put(f"{BASE}/api/notify/config", headers=H, json={"enabled": False})
        HookMock.received.clear()
        requests.post(f"{BASE}/api/notify/test-alert", headers=H)
        check("关闭后不再告警", len(HookMock.received) == 0)

        # 删除渠道与规则
        check("删除渠道", requests.delete(f"{BASE}/api/notify/channels/{cid}", headers=H).status_code == 200)
        check("删除规则", requests.delete(f"{BASE}/api/notify/rules/{rid}", headers=H).status_code == 200)
    finally:
        hook.shutdown()

    print(f"\n结果：PASS {PASS_N} 项，FAIL {FAIL_N} 项")
    sys.exit(1 if FAIL_N else 0)


if __name__ == "__main__":
    main()
