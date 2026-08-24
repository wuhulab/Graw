# -*- coding: utf-8 -*-
"""
test_round3_redis_e2e.py — 第三轮审计 Redis 黑名单 HTTP 层回归验证

验证点：Redis 控制台执行危险命令（shutdown / config_set / eval）返回 403，
且不会被 except Exception 兜底改写为 502；普通命令（get）不被黑名单误拦。

运行前提：后端运行在 8000 端口（.venv 已安装 redis 包）。
脚本会临时注入管理员测试账号，跑完自动恢复 users.json。
"""
import json
import os
import sys
import time
import urllib.request

BASE = "http://127.0.0.1:8000"
USERS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "users.json")

PASS = 0
FAIL = 0


def req(method, path, body=None, token=None, headers=None):
    """极简 HTTP 客户端：JSON POST/PUT/GET，返回 (status, data)。"""
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    if token:
        r.add_header("Authorization", f"Bearer {token}")
    for k, v in (headers or {}).items():
        r.add_header(k, v)
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode() or "{}")
        except Exception:
            return e.code, {}


def main():
    global PASS, FAIL
    # 1. 备份 users.json 并注入临时管理员（密码哈希由后端 /auth 生成不可行，
    #    直接复用现有 bcrypt 依赖生成）
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from app.auth import hash_password

    backup = None
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            backup = f.read()
    users = json.loads(backup) if backup else {}
    test_user = "round3tester"
    users[test_user] = {
        "username": test_user,
        "password": hash_password("R3Test!2026#x"),
        "role": "admin",
        "created_at": time.time(),
    }
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)

    try:
        # 2. 登录拿 token
        st, data = req("POST", "/api/auth/login",
                       {"username": test_user, "password": "R3Test!2026#x"},
                       headers={"X-ShunX-Entry": "shunianssy"})
        if st != 200:
            # 入口不匹配时尝试不带头（未配置入口的环境）
            st, data = req("POST", "/api/auth/login",
                           {"username": test_user, "password": "R3Test!2026#x"})
        assert st == 200, f"登录失败: {st} {data}"
        token = data.get("token") or data.get("access_token")

        # 3. 建一个指向不可达 Redis 的连接（127.0.0.1:1）
        st, data = req("POST", "/api/databases/connections", {
            "name": "r3-redis", "db_type": "redis",
            "host": "127.0.0.1", "port": 1, "database": "",
        }, token=token)
        assert st == 200, f"创建连接失败: {st} {data}"
        conn_id = data.get("id")

        # 4. 危险命令 → 403（黑名单在连接前拦截，不得变 502）
        for evil in ("shutdown", "config_set dir /tmp", "eval \"return 1\" 0"):
            st, data = req("POST", f"/api/databases/connections/{conn_id}/query",
                           {"command": evil}, token=token)
            if st == 403:
                print(f"  PASS  危险命令 {evil!r} → 403")
                PASS += 1
            else:
                print(f"  FAIL  危险命令 {evil!r} → {st} {data}")
                FAIL += 1

        # 5. 普通命令 → 不被黑名单误拦（无 Redis 服务，预期 502 连接失败；
        #    redis-py 对不可达端口有指数重试，客户端超时也证明已放行至执行层）
        try:
            st, data = req("POST", f"/api/databases/connections/{conn_id}/query",
                           {"command": "get somekey"}, token=token)
        except Exception:
            st, data = 0, {}
        if st == 403:
            print("  FAIL  普通命令 get 被误拦为 403")
            FAIL += 1
        elif st == 0:
            print("  PASS  普通命令 get 放行至执行层（连接重试中，客户端超时）")
            PASS += 1
        else:
            print(f"  PASS  普通命令 get 放行至执行层（{st}）")
            PASS += 1

        # 6. 清理连接
        req("DELETE", f"/api/databases/connections/{conn_id}", token=token)
    finally:
        # 7. 恢复 users.json
        if backup is not None:
            with open(USERS_FILE, "w", encoding="utf-8") as f:
                f.write(backup)
        elif os.path.exists(USERS_FILE):
            os.remove(USERS_FILE)

    print(f"\n结果：{PASS} 通过，{FAIL} 失败")
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    main()
