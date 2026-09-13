#!/usr/bin/env python3
"""PoC-1: 越权矩阵扫描。

对每个敏感端点分别以三种身份（未认证 / 普通用户 / 管理员）发起请求，
检测是否存在：
  1. 未认证可访问的管理端点（鉴权缺失）
  2. 普通用户可访问的管理端点（权限提升）

这是真实攻击者的第一步：先用未认证和低权限身份探测全接口。
"""
import json
import sys
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8000"

ADMIN_USER, ADMIN_PASS = "admin", "Adm1nTestPwd2026"
LOW_USER, LOW_PASS = "attacker", "Atk1TestPwd2026"


def login(u, p):
    req = urllib.request.Request(
        BASE + "/api/auth/login",
        data=json.dumps({"username": u, "password": p}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req) as r:
        return json.load(r)["token"]


def call(method, path, token=None, body=None, headers=None, base=BASE):
    h = dict(headers or {})
    if token:
        h["Authorization"] = "Bearer " + token
    data = json.dumps(body).encode() if body is not None else None
    if data:
        h.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(base + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, r.read()[:600]
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:600]
    except Exception as e:
        return -1, str(e).encode()[:200]


# 敏感端点清单：写操作/信息泄露类。方法+路径+body。
TARGETS = [
    # 文件管理（应 ADMIN）
    ("GET", "/api/files/list?path=/", None),
    ("GET", "/api/files/read?path=/etc/passwd", None),
    # 进程（应 ADMIN）
    ("GET", "/api/process/list", None),
    # Docker（应 ADMIN）
    ("GET", "/api/docker/list", None),
    # 计划任务（应 ADMIN）
    ("GET", "/api/cron/list", None),
    # 防火墙（应 ADMIN）
    ("GET", "/api/firewall/list", None),
    # 登录日志（PROTECTED：list 内部 admin）
    ("GET", "/api/loginlog/list", None),
    # 备忘录（PROTECTED）
    ("GET", "/api/notes/", None),
    # 节点管理（应 ADMIN）
    ("GET", "/api/nodes/list", None),
    # 面板备份（应 ADMIN）
    ("GET", "/api/panelbackup/list", None),
    # 插件（应 ADMIN）
    ("GET", "/api/plugins/list", None),
    # 应用商店（应 ADMIN）
    ("GET", "/api/appstore/index", None),
    # 通知（应 ADMIN）
    ("GET", "/api/notify/channels", None),
    # SSH 密钥（应 ADMIN）
    ("GET", "/api/sshkeys/list", None),
    # 批量命令（应 ADMIN）
    ("GET", "/api/batch/nodes", None),
    # 慢查询（应 ADMIN）
    ("GET", "/api/slowquery/connections", None),
    # Web 统计（应 ADMIN）
    ("GET", "/api/webstats/sites", None),
    # 工具箱（应 ADMIN）
    ("GET", "/api/toolbox/portscan?host=127.0.0.1", None),
    # 用户列表（应 ADMIN）
    ("GET", "/api/auth/users", None),
    # 运行时（应 ADMIN）
    ("GET", "/api/runtime/list", None),
    # 任务中心（应 ADMIN）
    ("GET", "/api/tasks/", None),
    # netstorage（应 ADMIN）
    ("GET", "/api/netstorage/connections", None),
]


def main():
    admin_t = login(ADMIN_USER, ADMIN_PASS)
    low_t = login(LOW_USER, LOW_PASS)
    findings = []
    print(f"[*] 管理员与低权限用户登录成功，开始扫描 {len(TARGETS)} 个端点 x 3 种身份\n")
    for method, path, body in TARGETS:
        anon = call(method, path, None, body)
        low = call(method, path, low_t, body)
        # 匿名可访问（非 401/403）→ 鉴权缺失
        if anon[0] not in (401, 403, -1):
            findings.append(("HIGH", f"未认证可访问 {method} {path}", anon))
            print(f"[!!] HIGH 未认证可访问 {method} {path} -> {anon[0]} {anon[1][:120]}")
        # 低权限可访问（2xx/4xx 非 403）→ 越权
        if low[0] not in (401, 403, -1):
            findings.append(("HIGH", f"普通用户可访问 {method} {path}", low))
            print(f"[!!] HIGH 普通用户可访问 {method} {path} -> {low[0]} {low[1][:120]}")
    print(f"\n[*] 扫描完成：发现 {len(findings)} 个疑似问题（404 = 端点路径猜测错误，已自动忽略严重性判断）")
    # 404 也会落到上面：排除 404
    real = [f for f in findings if f[2][0] != 404]
    print(f"[*] 其中非 404 的真实发现：{len(real)}")
    for sev, desc, resp in real:
        print(f"    [{sev}] {desc} -> HTTP {resp[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
