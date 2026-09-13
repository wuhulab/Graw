#!/usr/bin/env python3
"""PoC-5: 面板数据目录（data/）泄露防线验证。

攻击目标：通过文件管理 API 读取 backend/data/ 下的 secret.key / users.json
（JWT 签名密钥 + 用户口令哈希）。拿到 secret.key 即可伪造任意管理员 JWT。

测试向量：
  1. 直接路径                /workspace/backend/data/secret.key
  2. realpath 归一化穿越     /workspace/backend/app/../../data/secret.key
  3. 中间符号链接            /tmp/poc_ln -> data 目录（realpath 应解析后拦截）
  4. download 端点同样路径
  5. list 端点列目录         /workspace/backend/data
预期：全部 403/400（拒绝）。任何 200 = 严重漏洞（HIGH，直接沦陷）。
"""
import json
import os
import sys
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8000"
ADMIN_USER, ADMIN_PASS = "admin", "Adm1nTestPwd2026"
DATA_DIR = "/workspace/backend/data"

LINK_DIR = "/tmp/graw_poc_ln"
TARGETS = []


def login(u, p):
    req = urllib.request.Request(
        BASE + "/api/auth/login",
        data=json.dumps({"username": u, "password": p}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req) as r:
        return json.load(r)["token"]


def call(method, path, token=None, raw=False):
    h = {}
    if token:
        h["Authorization"] = "Bearer " + token
    req = urllib.request.Request(BASE + path, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=12) as r:
            body = r.read()
            return r.status, (body if raw else body[:150].decode("utf-8", "replace"))
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:150].decode("utf-8", "replace")
    except Exception as e:
        return -1, str(e)[:100]


def main():
    admin_t = login(ADMIN_USER, ADMIN_PASS)

    # 准备符号链接攻击向量
    if os.path.islink(LINK_DIR):
        os.unlink(LINK_DIR)
    os.symlink(DATA_DIR, LINK_DIR)

    vectors = [
        ("直接路径 read", "/api/files/read?path=" + DATA_DIR + "/secret.key"),
        ("直接路径 read users", "/api/files/read?path=" + DATA_DIR + "/users.json"),
        ("穿越归一化 read", "/api/files/read?path=/workspace/backend/app/../../data/secret.key"),
        ("符号链接 read", "/api/files/read?path=" + LINK_DIR + "/secret.key"),
        ("符号链接 users", "/api/files/read?path=" + LINK_DIR + "/users.json"),
        ("download data", "/api/files/download?path=" + DATA_DIR + "/secret.key"),
        ("download 符号链接", "/api/files/download?path=" + LINK_DIR + "/secret.key"),
        ("list data 目录", "/api/files/list?path=" + DATA_DIR),
        ("list 符号链接目录", "/api/files/list?path=" + LINK_DIR),
        ("尾斜杠混淆", "/api/files/read?path=" + DATA_DIR + "/./secret.key"),
        ("双斜杠", "/api/files/read?path=" + DATA_DIR.replace("/data", "//data") + "/secret.key"),
        ("编码点分", "/api/files/read?path=/workspace/backend/data/%2e%2e/data/secret.key"),
    ]

    findings = 0
    for name, url in vectors:
        code, body = call("GET", url, admin_t)
        leaked = code == 200 and b"BEGIN" in body.encode() or code == 200
        # 判定：200 = 泄露；403/400/404 = 拦截成功
        status = "LEAK!" if code == 200 else "blocked"
        if code == 200:
            findings += 1
        print(f"  [{status:7s}] {name:22s} -> {code}  {str(body)[:80]}")

    # 清理
    os.unlink(LINK_DIR)
    print(f"\n[摘要] 泄露向量：{findings} / {len(vectors)}")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
