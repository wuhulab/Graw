#!/usr/bin/env python3
"""PoC-8: 攻击链 B —— 加固实例 32 项未授权绕过矩阵（结果：全部失败）。

前置条件：127.0.0.1:8000 为「强口令 + 随机 ShunX 入口」的加固实例。
用途：回归测试——任何一项由 FAIL 变 HIT 即代表新引入了未授权绕过。
"""
import json
import time
import urllib.request
import urllib.error
import base64
import ssl

BASE = "http://127.0.0.1:8000"

def req(method, path, body=None, headers=None, timeout=8):
    h = dict(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h.setdefault("Content-Type", "application/json")
    r = urllib.request.Request(BASE + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, resp.read()[:400]
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:400]
    except Exception as e:
        return -1, str(e).encode()[:200]

results = []
def log(name, status, body, verdict):
    results.append((name, status, verdict))
    print(f"[{verdict:^4}] {name:50} -> {status} {body[:120]!r}")

# ---------------------------------------------------------------
# 1. JWT 伪造：alg=none / 算法混淆 / 空签名 / 弱密钥
# ---------------------------------------------------------------
def b64url(d):
    return base64.urlsafe_b64encode(d).rstrip(b"=")

def forge_jwt(header, payload):
    signing = b64url(json.dumps(header).encode()) + b"." + b64url(json.dumps(payload).encode())
    return signing.decode() + "."

cases = {
    "alg=none": ({"alg": "none", "typ": "JWT"}, {"sub": "admin", "tv": 0, "iat": int(time.time()), "exp": int(time.time()) + 86400}),
    "alg=none+agent": ({"alg": "none", "typ": "JWT"}, {"sub": "agent", "tv": 0, "iat": int(time.time()), "exp": int(time.time()) + 86400}),
    "HS256-empty-key": ({"alg": "HS256", "typ": "JWT"}, {"sub": "admin", "tv": 0, "iat": int(time.time()), "exp": int(time.time()) + 86400}),
}
for name, (hdr, pl) in cases.items():
    tok = forge_jwt(hdr, pl)
    if name == "HS256-empty-key":
        import hmac, hashlib
        signing = tok.encode()
        sig = hmac.new(b"", signing, hashlib.sha256).digest()
        tok = signing.decode() + "." + b64url(sig).decode()
    st, body = req("GET", "/api/auth/me", headers={"Authorization": "Bearer " + tok})
    log(f"JWT伪造 {name}", st, body, "FAIL" if st == 401 else "HIT!")

# ---------------------------------------------------------------
# 2. ShunX 安全入口绕过：不猜入口路径，尝试各类畸形值
# ---------------------------------------------------------------
entry_bypass = [
    ("空值", ""),
    ("斜杠", "/"),
    ("点", "."),
    ("通配符", "*"),
    ("null字节", "\x00"),
    ("大小写ADMIN", "ADMIN"),
    ("超长", "A" * 5000),
    ("JSON", '{"entry": true}'),
]
for name, val in entry_bypass:
    st, body = req("POST", "/api/auth/login",
                   {"username": "admin", "password": "test1234"},
                   {"X-ShunX-Entry": val})
    # 期望全部 403「请通过安全入口」或 401（限流抹平）；若 401 密码错误说明入口被绕过→需人工判断
    detail = ""
    try:
        detail = json.loads(body).get("detail", "")
    except Exception:
        pass
    bypassed = "安全入口" not in detail
    log(f"ShunX绕过[{name}]", st, body, "HIT!" if bypassed else "FAIL")

# ---------------------------------------------------------------
# 3. WebSocket 端点无 token / 畸形 token
# ---------------------------------------------------------------
import socket

def ws_probe(path, extra_headers=""):
    """原始 WebSocket 握手探测。"""
    try:
        s = socket.create_connection(("127.0.0.1", 8000), timeout=5)
        raw = (f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:8000\r\n"
               "Upgrade: websocket\r\nConnection: Upgrade\r\n"
               "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
               "Sec-WebSocket-Version: 13\r\n" + extra_headers + "\r\n")
        s.sendall(raw.encode())
        data = s.recv(1024)
        s.close()
        return data.decode(errors="replace").split("\r\n")[0]
    except Exception as e:
        return f"ERR {e}"

for path in ["/api/system/ws", "/api/system/ws?token=", "/api/system/ws?token=garbage",
             "/api/system/ws?token=null", "/api/system/ws?token=undefined",
             "/api/terminal/ws", "/api/terminal/ws?token=",
             "/api/docker/ws", "/api/tamper/ws"]:
    line = ws_probe(path)
    ok = "101" in line
    log(f"WS探测 {path}", line, "-" , "HIT!" if ok else "FAIL")

# ---------------------------------------------------------------
# 4. 限流绕过：伪造 X-Forwarded-For
# ---------------------------------------------------------------
xff_results = []
for i in range(8):
    st, body = req("POST", "/api/auth/login",
                   {"username": "admin", "password": "wrong" + str(i)},
                   {"X-Forwarded-For": f"1.2.3.{i}", "X-ShunX-Entry": "x"})
    xff_results.append(st)
    if st == 403 and ("频繁" in body.decode(errors="replace") or "稍后再试" in body.decode(errors="replace")):
        break
    time.sleep(0.2)
log("XFF伪造绕过限流(8连发)", ",".join(map(str, xff_results)), b"", "FAIL" if 403 in xff_results else "CHECK")

# ---------------------------------------------------------------
# 5. 登录类型混乱 / 畸形 JSON
# ---------------------------------------------------------------
weird = [
    ("username数组", {"username": ["admin"], "password": "x"}),
    ("password数组", {"username": "admin", "password": ["x"]}),
    ("username数字", {"username": 1, "password": "x"}),
    ("额外role字段", {"username": "admin", "password": "x", "role": "admin"}),
    ("额外token_version", {"username": "admin", "password": "x", "token_version": 99}),
]
for name, body in weird:
    st, body = req("POST", "/api/auth/login", body, {"X-ShunX-Entry": "x"})
    log(f"类型混乱[{name}]", st, body, "FAIL" if st in (401, 403, 422) else "HIT!")

# ---------------------------------------------------------------
# 6. 公开端点路径穿越 / 信息泄露
# ---------------------------------------------------------------
trav = [
    "/api/appstore/icons/..%2f..%2f..%2f..%2fbackend%2fdata%2fsecret.key",
    "/api/appstore/icons/%2e%2e%2f%2e%2e%2f",
    "/api/ui/public/../../data/users.json",
    "/assets/../../../../backend/data/secret.key",
    "/../../../etc/passwd",
    "/api/health?debug=1",
]
for p in trav:
    st, body = req("GET", p)
    sensitive = b"secret" in body or b"password" in body.lower() or b"root:" in body
    log(f"穿越[{p[:50]}]", st, body[:80], "HIT!" if sensitive else "FAIL")

print("\n========== 汇总 ==========")
hits = [r for r in results if r[2] != "FAIL"]
print(f"总计 {len(results)} 项，疑似命中 {len(hits)} 项")
for name, st, v in hits:
    print(f"  - {name}: {v}")
