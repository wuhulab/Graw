#!/usr/bin/env python3
"""加固实例上的 ShunX 入口枚举测试：/status 预言机 + 限流验证 + 登录差异响应抹平验证。"""
import json
import time
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8000"

def req(method, path, body=None, headers=None):
    h = dict(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h.setdefault("Content-Type", "application/json")
    r = urllib.request.Request(BASE + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, resp.read()[:200]
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:200]
    except Exception as e:
        return -1, str(e).encode()

print("=== 1. /status 预言机：字典词 vs 随机 hex 入口的枚举可行性 ===")
wordlist = ["admin", "login", "panel", "manage", "dashboard", "myadmin",
            "admin123", "backend", "console", "secret", "graw", "shunx",
            "entry", "backdoor", "god", "root", "system", "cp", "bt", "aauu"]
hits = 0
for w in wordlist:
    st, body = req("GET", f"/api/shunx/status?path={w}")
    try:
        m = json.loads(body).get("matched")
    except Exception:
        m = None
    if m:
        hits += 1
        print(f"    [!] 命中: {w}")
print(f"    字典 {len(wordlist)} 词，命中 {hits}（入口为随机 16 位 hex → 不可字典枚举）")

print("\n=== 2. /status 限流验证（30 次/分钟/IP）===")
codes = {}
for i in range(35):
    st, _ = req("GET", "/api/shunx/status?path=x" + str(i))
    codes[st] = codes.get(st, 0) + 1
print(f"    35 连发状态码分布: {codes}  （出现 429 = 限流生效）")

print("\n=== 3. 登录入口差异响应（预言机）与抹平验证 ===")
# 注意：admin 账号此前已被限流锁定 10 分钟，此处用随机用户名避开 IP|username 锁定
results = []
for i in range(13):
    st, body = req("POST", "/api/auth/login",
                   {"username": f"oracleprobe{i}", "password": "wrongpass1"},
                   {"X-ShunX-Entry": "wrong-entry"})
    detail = ""
    try:
        detail = json.loads(body).get("detail", "")[:30]
    except Exception:
        pass
    results.append((st, detail))
    time.sleep(0.15)
distinct = set(results)
print(f"    13 次错误入口登录，响应种类: {len(distinct)}")
for st, d in sorted(distinct):
    print(f"      HTTP {st} detail={d!r}  ×{sum(1 for r in results if r == (st, d))}")
print("    （若锁定期后统一 401「用户名或密码错误」= 差异已被抹平）")
