#!/usr/bin/env python3
"""PoC-4: WebSocket 端点鉴权验证。

测试矩阵（对 4 个 WS 端点）：
  1. 无 token            → 应拒绝（403/4401/关闭）
  2. 伪造/垃圾 token      → 应拒绝
  3. 低权限用户 token     → terminal 应拒绝（管理员专用）；
                            system/tamper 视设计（登录即可 or 管理员）
  4. 管理员 token         → 应接受（作为对照，验证我们的判定方式正确）

判定：等待 2s 内收到服务端主动关闭/拒绝即视为「已拒绝」；
成功完成握手并保持连接 2s 视为「已接受」。
"""
import asyncio
import json
import sys
import urllib.request

BASE = "http://127.0.0.1:8000"
WS_HOST = "127.0.0.1:8000"
ADMIN_USER, ADMIN_PASS = "admin", "Adm1nTestPwd2026"
LOW_USER, LOW_PASS = "attacker", "Atk1TestPwd2026"

ENDPOINTS = ["/api/system/ws", "/api/terminal/ws", "/api/terminal/ws/container", "/api/tamper/ws"]


def login(u, p):
    req = urllib.request.Request(
        BASE + "/api/auth/login",
        data=json.dumps({"username": u, "password": p}).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req) as r:
        return json.load(r)["token"]


async def probe(path, token):
    """返回 (accepted, close_code_or_reason)"""
    import websockets
    url = f"ws://{WS_HOST}{path}"
    if token:
        url += ("&" if "?" in url else "?") + f"token={token}"
    try:
        async with websockets.connect(url, open_timeout=6) as ws:
            try:
                # 等 2.5s：被拒绝的连接通常立刻被服务端关闭
                msg = await asyncio.wait_for(ws.recv(), timeout=2.5)
                return (True, f"收到消息: {str(msg)[:80]}")
            except asyncio.TimeoutError:
                return (True, "连接保持存活 2.5s（未被拒绝）")
            except websockets.ConnectionClosed as e:
                return (False, f"服务端关闭 code={e.code}")
    except websockets.exceptions.InvalidStatus as e:
        return (False, f"握手被拒 HTTP {e.response.status_code}")
    except Exception as e:
        return (False, f"{type(e).__name__}: {str(e)[:80]}")


async def main():
    admin_t = login(ADMIN_USER, ADMIN_PASS)
    low_t = login(LOW_USER, LOW_PASS)

    cases = [
        ("无token", None),
        ("垃圾token", "eyJhbGciOiJIUzI1NiJ9.forged.sig"),
        ("低权限token", low_t),
        ("管理员token", admin_t),
    ]
    for ep in ENDPOINTS:
        print(f"\n=== {ep} ===")
        for name, tok in cases:
            accepted, detail = await probe(ep, tok)
            flag = ""
            if name in ("无token", "垃圾token") and accepted:
                flag = "  <-- [!!] 未认证 WS 连接被接受"
            if name == "低权限token" and accepted and "/terminal/" in ep:
                flag = "  <-- [!!] 低权限用户可建立终端 WS"
            print(f"  {name:10s} accepted={accepted}  {detail}{flag}")
    return 0


if __name__ == "__main__":
    try:
        import websockets  # noqa
    except ImportError:
        print("[!] 缺少 websockets 库，安装: pip install websockets")
        sys.exit(2)
    sys.exit(asyncio.run(main()))
