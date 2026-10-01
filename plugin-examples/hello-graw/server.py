# -*- coding: utf-8 -*-
"""
server.py - Graw 应用接口开放协议（GPOP）示例插件主程序

作用：
  演示一个符合 GPOP 的插件应用如何与面板双向交互：
    1. 启动时读取面板注入的环境变量（GRAW_PLUGIN_ID / GRAW_PLUGIN_TOKEN /
       GRAW_PANEL_URL），调用 GET /api/op/me 完成握手（面板鉴权 + 回显插件信息）；
    2. 暴露 HTTP 服务：
         GET  /health       => 健康检查（供面板/Docker 探测）
         GET  /api/hello    => 业务端点，返回一条问候语
         POST /api/notify   => 触发一次面板通知（演示 notify 能力）
    3. 使用 config 能力在面板侧持久化一个简单计数器。

运行：
  在插件容器内由 docker-compose 直接启动（无需手动执行）。
"""
import json
import os
import urllib.request

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PANEL_URL = os.environ.get("GRAW_PANEL_URL", "").rstrip("/")
PLUGIN_ID = os.environ.get("GRAW_PLUGIN_ID", "")
PLUGIN_TOKEN = os.environ.get("GRAW_PLUGIN_TOKEN", "")
COUNT = int(os.environ.get("HELLO_WORLD_COUNT", "1"))

_counter = 0  # 本进程内存计数（演示用）


def panel_request(path: str, method: str = "GET", body: dict = None):
    """调用面板开放接口 /api/op/*；无令牌 / 面板地址时返回 None（保持健壮）。"""
    if not PANEL_URL or not PLUGIN_TOKEN:
        return None
    url = f"{PANEL_URL}/api/op{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Content-Type": "application/json",
            "X-Graw-Plugin-Id": PLUGIN_ID,
            "Authorization": f"Bearer {PLUGIN_TOKEN}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:  # 协议调用失败不拖垮业务
        return {"error": str(e)}


def handshake():
    """启动握手：调用 /api/op/me，打印面板回显信息。"""
    info = panel_request("/me")
    if info is None:
        print("[hello-graw] 未获得面板地址/令牌，跳过握手")
        return
    if "error" in info:
        print(f"[hello-graw] 握手失败: {info['error']}")
        return
    plugin = info.get("plugin", {})
    print(
        f"[hello-graw] 握手成功 -> 插件: {plugin.get('name')} v{plugin.get('version')}, "
        f"协议: {info.get('panel', {}).get('timezone_utc_offset')}"
    )


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        global _counter
        if self.path == "/health":
            body = json.dumps({"ok": True, "plugin": PLUGIN_ID})
        elif self.path.startswith("/api/hello"):
            _counter += 1
            msg = ("Hello from Graw Plugin! " * COUNT).strip()
            body = json.dumps({"message": msg, "call_count": _counter})
        else:
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":"not found"}')
            return
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if self.path == "/api/notify":
            # 演示 notify 能力：向面板通知中心推送一条消息
            res = panel_request("/notify", method="POST", body={
                "title": "Hello Graw 插件被调用",
                "message": "有客户端触发了一次插件通知",
                "level": "info",
            })
            body = json.dumps(res or {"ok": True})
        else:
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":"not found"}')
            return
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *args):  # 精简容器日志
        print("[hello-graw] %s" % (fmt % args), flush=True)


if __name__ == "__main__":
    print(f"[hello-graw] 启动 plugin={PLUGIN_ID or '(未注入)'} panel={PANEL_URL or '(未注入)'}")
    if PLUGIN_ID and PLUGIN_TOKEN:
        handshake()
    ThreadingHTTPServer(("0.0.0.0", 8080), Handler).serve_forever()