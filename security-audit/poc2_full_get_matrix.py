#!/usr/bin/env python3
"""PoC-2: 全量 GET 路由越权矩阵扫描。

从运行中的应用枚举全部 GET 路由，分别用「未认证 / 普通用户 / 管理员」
请求同一端点，比对状态码：
  - 未认证拿到非 401 → 鉴权缺失（HIGH）
  - 普通用户拿到 2xx 且管理员也是 2xx → 需人工核对是否应限管理员（MED）

为避免副作用，只扫描 GET 方法；路径参数以合法占位值代入（从响应推断是否
真实存在），并跳过 /api/auth/*、/api/health 等已知公开端点。
"""
import json
import re
import sys
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8000"
ADMIN_USER, ADMIN_PASS = "admin", "Adm1nTestPwd2026"
LOW_USER, LOW_PASS = "attacker", "Atk1TestPwd2026"

# 已知公开端点（设计如此）
PUBLIC_KNOWN = {
    "/api/health",
    "/api/ui/public",
    "/api/auth/login",
    "/api/shunx/status",
}
# 路径参数占位值（合法格式）
PLACEHOLDERS = {
    "container_id": "a1b2c3d4e5",
    "name": "x",
    "conn_id": "x1",
    "remote_id": "r1",
    "task_id": "1",
    "rule_id": "1",
    "proxy_id": "p1",
    "user_id": "u1",
    "deploy_id": "abc123",
    "channel_id": "c1",
    "item_id": "1",
    "key_id": "k1",
    "s_id": "s1",
    "site_id": "s1",
    "node_id": "local",
    "app_id": "hello",
    "script_id": "1",
    "cid": "c1",
    "tid": "t1",
    "username": "attacker",
    "log_id": "l1",
    "network": "bridge",
    "image": "alpine:latest",
}


def login(u, p):
    req = urllib.request.Request(
        BASE + "/api/auth/login",
        data=json.dumps({"username": u, "password": p}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req) as r:
        return json.load(r)["token"]


def call(method, path, token=None, body=None, headers=None):
    h = dict(headers or {})
    if token:
        h["Authorization"] = "Bearer " + token
    data = json.dumps(body).encode() if body is not None else None
    if data:
        h.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(BASE + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return -1


def fill(path):
    def rep(m):
        return PLACEHOLDERS.get(m.group(1), "x1")
    return re.sub(r"\{(\w+)\}", rep, path)


def main():
    from app.main import app  # 本地枚举路由
    gets = []
    for r in app.routes:
        if hasattr(r, "methods") and "GET" in r.methods:
            p = r.path
            if p.startswith("/api/") and p not in PUBLIC_KNOWN and not p.startswith("/api/auth/login"):
                gets.append(p)
    gets = sorted(set(gets))
    print(f"[*] 枚举到 {len(gets)} 个 GET 路由\n")

    admin_t = login(ADMIN_USER, ADMIN_PASS)
    low_t = login(LOW_USER, LOW_PASS)

    high, med = [], []
    for p in gets:
        url = fill(p)
        anon = call("GET", url, None)
        low = call("GET", url, low_t)
        adm = call("GET", url, admin_t)
        # 仅当管理员可达（非 404/405）时端点才真实存在
        if adm in (404, 405, -1):
            continue
        if anon not in (401, 403):
            high.append((p, "未认证", anon, adm))
        if low not in (401, 403) and low != 404:
            med.append((p, "普通用户", low, adm))

    print(f"[*] 端点真实存在的扫描完成")
    print(f"\n[摘要] 未认证可达（潜在鉴权缺失）：{len(high)}")
    for p, who, code, adm in high:
        print(f"    {who:6s} {p} -> {code} (admin:{adm})")
    print(f"\n[摘要] 普通用户可达（需人工核对级别设计）：{len(med)}")
    for p, who, code, adm in med:
        print(f"    {who:6s} {p} -> {code} (admin:{adm})")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, "/workspace/backend")
    sys.exit(main())
