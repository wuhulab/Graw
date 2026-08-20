# -*- coding: utf-8 -*-
"""
站点可用性检测 E2E 回归测试（需后端运行在 8000 端口）

策略：
  - 脚本执行前备份 users.json，注入临时测试账号（upadmin/upuser），跑完自动恢复。
  - 本地 mock 站点服务器：/site 可切换返回 200 / 500，模拟网站正常与宕机；
    /push 记录通知中心推送，验证「宕机→推送」与「恢复→推送」全链路。

用法：
  python test_uptime_e2e.py          # 默认 http://localhost:8000
  python test_uptime_e2e.py 8011     # 指定端口
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
BACKUP_FILE = USERS_FILE + ".bak_uptimee2e"

ADMIN = "upadmin"
USER = "upuser"
PASS = "UpPass#123"

PASS_N = 0
FAIL_N = 0


class SiteMock(http.server.BaseHTTPRequestHandler):
    """既是「被监控站点」（/site 可切换状态码），也是「推送接收器」（/push）。"""
    site_code = 200
    pushes = []

    def log_message(self, *a):
        pass

    def _send(self, code, body=b"ok"):
        self.send_response(code)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self):
        if self.path == "/site":
            self._send(SiteMock.site_code, b"")
        else:
            self._send(404, b"")

    def do_GET(self):
        if self.path == "/site":
            self._send(SiteMock.site_code)
        else:
            self._send(404, b"not found")

    def do_POST(self):
        if self.path == "/push":
            length = int(self.headers.get("Content-Length", 0) or 0)
            body = self.rfile.read(length) if length else b""
            SiteMock.pushes.append(body.decode("utf-8", "replace"))
            self._send(200, b'{"ok":true}')
        else:
            self._send(404, b"")


def start_mock():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), SiteMock)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address
    SiteMock.pushes.clear()
    return server, f"http://{host}:{port}"


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
    rp = requests.get(f"{BASE}/api/uptime/status", headers=HU)
    check("普通用户访问站点监控被拒", rp.status_code == 403, f"status={rp.status_code}")

    mock, mock_base = start_mock()
    try:
        # 清理可能残留的监控项/渠道
        for it in requests.get(f"{BASE}/api/uptime/items", headers=H).json().get("items", []):
            requests.delete(f"{BASE}/api/uptime/items/{it['id']}", headers=H)
        for c in requests.get(f"{BASE}/api/notify/channels", headers=H).json().get("channels", []):
            requests.delete(f"{BASE}/api/notify/channels/{c['id']}", headers=H)

        # 1. 创建监控项（指向本地 mock /site，预期 200）
        SiteMock.site_code = 200
        r = requests.post(f"{BASE}/api/uptime/items", headers=H, json={
            "name": "e2e-site", "url": f"{mock_base}/site", "expect_status": 200,
            "timeout_seconds": 5, "interval_seconds": 60, "enabled": True,
        })
        check("创建监控项", r.status_code == 200, f"status={r.status_code} detail={r.text[:120]}")
        iid = r.json()["id"]

        # 非法 URL 拒绝
        r = requests.post(f"{BASE}/api/uptime/items", headers=H, json={
            "name": "bad", "url": "ftp://x", "expect_status": 200, "interval_seconds": 60,
        })
        check("非法监控 URL 被拒", r.status_code == 400, f"status={r.status_code}")

        # 2. 手动探测（mock 200）→ 正常
        r = requests.post(f"{BASE}/api/uptime/items/{iid}/test", headers=H)
        check("手动探测（正常）", r.status_code == 200 and r.json()["status"] == "ok",
              f"status={r.status_code} body={r.text[:100]}")

        # 3. 配置通知渠道（webhook 指向本地 /push）
        r = requests.post(f"{BASE}/api/notify/channels", headers=H, json={
            "name": "e2e-hook", "type": "webhook", "config": {"url": f"{mock_base}/push"}, "enabled": True,
        })
        check("创建通知渠道", r.status_code == 200, f"status={r.status_code}")
        cid = r.json()["id"]

        # 4. 站点宕机（mock 500）→ 手动探测 → 推送「站点告警」
        SiteMock.site_code = 500
        SiteMock.pushes.clear()
        r = requests.post(f"{BASE}/api/uptime/items/{iid}/test", headers=H)
        check("宕机探测", r.status_code == 200 and r.json()["status"] == "down",
              f"status={r.status_code} body={r.text[:100]}")
        check("宕机推送已发送", len(SiteMock.pushes) >= 1, SiteMock.pushes[0][:80] if SiteMock.pushes else "empty")
        if SiteMock.pushes:
            text = json.loads(SiteMock.pushes[0]).get("text", "")
            check("推送含告警标识", "站点告警" in text, text[:60])

        # 5. 站点恢复（mock 200）→ 推送「站点恢复」
        SiteMock.site_code = 200
        SiteMock.pushes.clear()
        r = requests.post(f"{BASE}/api/uptime/items/{iid}/test", headers=H)
        check("恢复探测", r.status_code == 200 and r.json()["status"] == "ok",
              f"status={r.status_code} body={r.text[:100]}")
        check("恢复推送已发送", len(SiteMock.pushes) >= 1, SiteMock.pushes[0][:80] if SiteMock.pushes else "empty")
        if SiteMock.pushes:
            text = json.loads(SiteMock.pushes[0]).get("text", "")
            check("推送含恢复标识", "站点恢复" in text, text[:60])

        # 6. 状态接口汇总
        st = requests.get(f"{BASE}/api/uptime/status", headers=H).json()
        check("状态汇总接口", st["item_count"] >= 1 and st["up_count"] >= 1, str(st))

        # 7. 删除监控项与渠道
        check("删除监控项", requests.delete(f"{BASE}/api/uptime/items/{iid}", headers=H).status_code == 200)
        check("删除通知渠道", requests.delete(f"{BASE}/api/notify/channels/{cid}", headers=H).status_code == 200)
    finally:
        mock.shutdown()

    print(f"\n结果：PASS {PASS_N} 项，FAIL {FAIL_N} 项")
    sys.exit(1 if FAIL_N else 0)


if __name__ == "__main__":
    main()
