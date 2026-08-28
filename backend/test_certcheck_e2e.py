# -*- coding: utf-8 -*-
"""
证书到期提醒 E2E 回归测试（需后端运行在 8000 端口）

策略：
  - 备份 users.json / ssl.json / certcheck.json，注入临时管理员 + 一张
    剩余 15 天到期的测试证书到 ssl.json，跑完自动恢复。
  - 本地 mock webhook 接收通知中心推送，验证「临期→推送提醒」「去重」链路。

用法：
  python test_certcheck_e2e.py          # 默认 http://localhost:8000
  python test_certcheck_e2e.py 8011     # 指定端口
"""
import datetime
import json
import os
import shutil
import sys
import tempfile
import threading
import http.server
import bcrypt
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

PORT = sys.argv[1] if len(sys.argv) > 1 else "8000"
BASE = f"http://localhost:{PORT}"
HERE = os.path.dirname(os.path.abspath(__file__))
USERS_FILE = os.path.join(HERE, "data", "users.json")
SSL_FILE = os.path.join(HERE, "data", "ssl.json")
CERTCHECK_FILE = os.path.join(HERE, "data", "certcheck.json")

BACKUP_FILES = [USERS_FILE + ".bak_certe2e", SSL_FILE + ".bak_certe2e", CERTCHECK_FILE + ".bak_certe2e"]

ADMIN = "ckadmin"
USER = "ckuser"
PASS = "CkPass#123"

PASS_N = 0
FAIL_N = 0


class HookMock(http.server.BaseHTTPRequestHandler):
    received = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else b""
        HookMock.received.append(body.decode("utf-8", "replace"))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"ok":true}')


def start_hook():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), HookMock)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address
    HookMock.received.clear()
    return server, f"http://{host}:{port}/push"


def _make_expiring_cert(path, days_valid=15):
    """生成一张 N 天后到期的自签证书。"""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    # 安全：RSA 至少 2048 位（1024 位已认为可被破解，code-scanning 告警）
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "expire.test")])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=30))
        .not_valid_after(now + datetime.timedelta(days=days_valid))
        .sign(key, hashes.SHA256())
    )
    with open(path, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.PEM))


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
    for f in BACKUP_FILES:
        src = f.replace(".bak_certe2e", "")
        if os.path.exists(src):
            shutil.copy2(src, f)
    # 注入临时账号
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
    for f in BACKUP_FILES:
        src = f.replace(".bak_certe2e", "")
        if os.path.exists(f):
            shutil.move(f, src)


def main():
    setup()
    try:
        run()
    finally:
        teardown()


def run():
    # 注入一张剩余 15 天的测试证书到 ssl.json
    tmp = tempfile.mkdtemp(prefix="graw_certe2e_")
    cert_path = os.path.join(tmp, "expire.crt")
    _make_expiring_cert(cert_path, 15)
    ssl_data = {"certs": [{
        "id": "certe2e1", "name": "e2e-cert", "domains": ["expire.test"],
        "cert_path": cert_path,
    }]}
    with open(SSL_FILE, "w", encoding="utf-8") as f:
        json.dump(ssl_data, f, ensure_ascii=False, indent=2)
    # 清空 certcheck 已提醒记录（保证测试可重复）
    with open(CERTCHECK_FILE, "w", encoding="utf-8") as f:
        json.dump({"enabled": True, "interval_seconds": 86400, "remind_days": [30, 7], "reminded": {}}, f)

    r = login(ADMIN, PASS)
    check("管理员登录", r.status_code == 200, f"status={r.status_code}")
    if r.status_code != 200:
        return
    token = r.json()["token"]
    H = hdr(token)

    ru = login(USER, PASS)
    HU = hdr(ru.json()["token"]) if ru.status_code == 200 else None

    # 权限分级
    rp = requests.get(f"{BASE}/api/certcheck/status", headers=HU)
    check("普通用户访问证书提醒被拒", rp.status_code == 403, f"status={rp.status_code}")

    # 证书状态列表：应识别为临期（warn）
    certs = requests.get(f"{BASE}/api/certcheck/certs", headers=H).json()["certs"]
    check("识别临期证书", len(certs) == 1 and certs[0]["status"] == "warn", str(certs))
    check("剩余天数正确", certs[0]["days_left"] is not None and 10 < certs[0]["days_left"] < 20,
          f"days={certs[0]['days_left']}")

    hook, hook_url = start_hook()
    try:
        # 配置 webhook 渠道（本地 mock）
        for c in requests.get(f"{BASE}/api/notify/channels", headers=H).json().get("channels", []):
            requests.delete(f"{BASE}/api/notify/channels/{c['id']}", headers=H)
        r = requests.post(f"{BASE}/api/notify/channels", headers=H, json={
            "name": "e2e-hook", "type": "webhook", "config": {"url": hook_url}, "enabled": True,
        })
        check("创建通知渠道", r.status_code == 200, f"status={r.status_code}")

        # 触发检查 → 推送「证书提醒」
        r = requests.post(f"{BASE}/api/certcheck/test", headers=H)
        check("触发检查", r.status_code == 200, f"status={r.status_code} body={r.text[:100]}")
        check("临期提醒已推送", len(HookMock.received) >= 1, HookMock.received[0][:60] if HookMock.received else "empty")
        if HookMock.received:
            text = json.loads(HookMock.received[0]).get("text", "")
            check("推送含证书提醒", "证书提醒" in text, text[:60])

        # 再次检查 → 去重不推送
        HookMock.received.clear()
        requests.post(f"{BASE}/api/certcheck/test", headers=H)
        check("去重不重复推送", len(HookMock.received) == 0, f"count={len(HookMock.received)}")

        # 清理渠道
        for c in requests.get(f"{BASE}/api/notify/channels", headers=H).json().get("channels", []):
            requests.delete(f"{BASE}/api/notify/channels/{c['id']}", headers=H)
    finally:
        hook.shutdown()
        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n结果：PASS {PASS_N} 项，FAIL {FAIL_N} 项")
    sys.exit(1 if FAIL_N else 0)


if __name__ == "__main__":
    main()
