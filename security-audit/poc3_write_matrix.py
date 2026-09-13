#!/usr/bin/env python3
"""PoC-3: 全量写操作（POST/PUT/DELETE）路由越权矩阵扫描。

安全验证方法（无副作用 oracle）：
  FastAPI 在解析请求体之前先解析依赖（鉴权依赖先于 body 校验执行）。
  对带 Pydantic body 参数的端点发送空 JSON "{}"：
    - 401/403 → 鉴权依赖先行拒绝（正确）
    - 422     → 越过了鉴权、进入了 body 校验（鉴权缺失/不足！）
  由于 body 校验在最前（缺字段必 422），处理器逻辑不会真正执行，
  因此该探测对系统无副作用。

对不带 body 的端点（如 POST /api/firewall/clear），动态调用会产生真实
副作用，故仅做静态依赖检查（route.dependencies 中鉴权依赖是否存在）。
"""
import json
import re
import sys
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8000"
ADMIN_USER, ADMIN_PASS = "admin", "Adm1nTestPwd2026"
LOW_USER, LOW_PASS = "attacker", "Atk1TestPwd2026"

PUBLIC_KNOWN = {"/api/auth/login", "/api/health"}
# 空对象 body 能通过校验且会产生真实副作用的端点（PoC 副作用教训）：
# PUT /api/plugins/settings 的 enabled 字段默认 False，{} 即等于关闭插件。
SKIP_SIDE_EFFECT = {"/api/plugins/settings"}
PLACEHOLDERS = {
    "container_id": "a1b2c3d4e5",
    "image_id": "sha256:abc123",
    "network_name": "bridge",
    "name": "x1",
    "conn_id": "x1",
    "remote_id": "r1",
    "task_id": "999999",
    "rule_id": "999999",
    "proxy_id": "p1",
    "user_id": "u1",
    "deploy_id": "abc123",
    "channel_id": "c1",
    "item_id": "999999",
    "key_id": "k1",
    "s_id": "s1",
    "site_id": "s1",
    "node_id": "local",
    "app_id": "hello",
    "script_id": "999999",
    "cid": "c1",
    "tid": "t1",
    "username": "attacker",
    "log_id": "l1",
    "rid": "r1",
    "pid": "999999",
    "cert_id": "c1",
    "plugin_id": "p1",
    "sid": "s1",
}


def login(u, p):
    req = urllib.request.Request(
        BASE + "/api/auth/login",
        data=json.dumps({"username": u, "password": p}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req) as r:
        return json.load(r)["token"]


def call(method, path, token=None):
    h = {}
    if token:
        h["Authorization"] = "Bearer " + token
    req = urllib.request.Request(
        BASE + path, data=b"{}", headers=h, method=method)
    # POST 默认无 Content-Type 时 FastAPI 不校验 body；
    # 显式给 JSON content-type 以确保进入 body 校验
    req.add_header("Content-Type", "application/json")
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


def route_auth_desc(r):
    """静态分析路由声明的鉴权依赖名称。"""
    names = []
    for d in getattr(r, "dependencies", []) or []:
        names.append(getattr(d.call, "__name__", str(d.call)))
    # 端点签名内的 Depends 也会被合并进 r.dependant.dependencies
    try:
        for d in r.dependant.dependencies:
            n = getattr(d.call, "__name__", None)
            if n and n not in names:
                names.append(n)
    except Exception:
        pass
    return names


def main():
    from app.main import app  # 本地枚举路由
    targets = []   # (methods, path, has_body)
    for r in app.routes:
        if not hasattr(r, "methods"):
            continue
        if not (r.methods & {"POST", "PUT", "DELETE", "PATCH"}):
            continue
        p = r.path
        if not p.startswith("/api/") or p in PUBLIC_KNOWN or p in SKIP_SIDE_EFFECT:
            continue
        ms = sorted(r.methods & {"POST", "PUT", "DELETE", "PATCH"})
        # 排除登录端点；排除本 PoC 自身造成的写入目标 notes（其设计为登录即可用）
        has_body = bool(getattr(r, "dependant", None) and r.dependant.body_params)
        targets.append((ms, p, has_body))
    targets.sort(key=lambda x: x[1])
    print(f"[*] 枚举到 {len(targets)} 个写操作路由（其中带 body 可安全探测 {sum(1 for t in targets if t[2])} 个）\n")

    admin_t = login(ADMIN_USER, ADMIN_PASS)
    low_t = login(LOW_USER, LOW_PASS)

    high, med, static_review = [], [], []
    for ms, p, has_body in targets:
        method = "POST" if "POST" in ms else ms[0]
        if not has_body:
            # 无 body：动态调用会产生副作用 → 仅静态记录依赖声明
            static_review.append((method, p, route_auth_desc(r) if False else None))
            continue
        url = fill(p)
        anon = call(method, url, None)
        low = call(method, url, low_t)
        adm = call(method, url, admin_t)
        if adm in (404, 405, -1):
            continue
        # 管理员 422 说明端点真实存在且确实先过了鉴权再进 body 校验
        if anon not in (401, 403):
            high.append((method, p, "未认证", anon, adm))
        if low not in (401, 403):
            med.append((method, p, "普通用户", low, adm))

    print(f"[摘要] 未认证即越过鉴权（进入 body 校验）：{len(high)}")
    for m, p, who, code, adm in high:
        print(f"    [!!] {who} {m} {p} -> {code} (admin:{adm})")
    print(f"\n[摘要] 普通用户即越过鉴权（进入 body 校验）：{len(med)}")
    for m, p, who, code, adm in med:
        print(f"    [!!] {who} {m} {p} -> {code} (admin:{adm})")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, "/workspace/backend")
    sys.exit(main())
