# -*- coding: utf-8 -*-
"""
frp 面板功能 E2E 实测：配置 -> 重启 -> 穿透联通
在真实运行的后端（端口 8011）上验证 /api/frp 全链路：
  1) 注入临时管理员并登录
  2) 配置服务端 frps -> 启动 -> 校验 bindPort 监听
  3) 切换客户端 frpc + 添加 tcp 代理 -> 启动
  4) 访问 frps 的远端端口，确认能穿透到本地服务（联通）
结束时恢复 users.json、停止本地目标服务与 frp 进程。
"""
import http.server
import json
import os
import shutil
import socketserver
import subprocess
import sys
import threading
import time

import bcrypt
import requests

BASE = "http://127.0.0.1:8011"
BACKEND = r"s:\Graw\backend"
USERS_FILE = os.path.join(BACKEND, "data", "users.json")
BACKUP_FILE = os.path.join(BACKEND, "data", "users.json.frpe2e.bak")

FRP_DIR = r"s:\frptest\frp_0.61.1_windows_amd64"
FRPS = os.path.join(FRP_DIR, "frps.exe")
FRPC = os.path.join(FRP_DIR, "frpc.exe")

E2E_USER = "frpe2e"
E2E_PASS = "frpe2epass"

BIND_PORT = 17400      # frps 绑定端口
REMOTE_PORT = 17401    # frps 上的对外穿透端口
LOCAL_PORT = 17800     # 本地目标服务端口（要被穿透）

# 配置文件写到面板数据目录下（uvicorn 后台进程对 S: 等映射盘不可见时会报
# Permission denied；面板 data 目录保证可写，仅用于本次演示，用后清理）
FRPS_CFG = r"s:/Graw/backend/data/frp_e2e/frps_e2e.toml"
FRPC_CFG = r"s:/Graw/backend/data/frp_e2e/frpc_e2e.toml"


def entry_headers():
    """读取面板 ShunX 安全入口，登录需匹配该路径。"""
    base = os.path.join(BACKEND, "data")
    for fn in os.listdir(base):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(base, fn), "r", encoding="utf-8") as f:
                d = json.load(f)
            if isinstance(d, dict) and d.get("entry_path"):
                return {"X-ShunX-Entry": d["entry_path"]}
        except Exception:
            continue
    return {}


def setup():
    # 清理可能残留的配置文件与进程，避免 Permission/占用干扰
    for cfg in (FRPS_CFG, FRPC_CFG):
        try:
            os.remove(cfg)
        except OSError:
            pass
    for exe in ("frps.exe", "frpc.exe"):
        subprocess.run(["taskkill", "/F", "/IM", exe], capture_output=True)
    shutil.copy2(USERS_FILE, BACKUP_FILE)
    with open(USERS_FILE, "r", encoding="utf-8") as f:
        users = json.load(f)
    pw = bcrypt.hashpw(E2E_PASS.encode(), bcrypt.gensalt()).decode()
    users[E2E_USER] = {"username": E2E_USER, "password": pw, "role": "admin",
                       "must_change_password": False, "created_at": 0}
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)


def teardown():
    try:
        subprocess.run(["taskkill", "/F", "/IM", "frps.exe"], capture_output=True)
    except Exception:
        pass
    try:
        subprocess.run(["taskkill", "/F", "/IM", "frpc.exe"], capture_output=True)
    except Exception:
        pass
    if os.path.exists(BACKUP_FILE):
        shutil.move(BACKUP_FILE, USERS_FILE)


class _Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"FRP-E2E-OK from local target service"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def start_local_service():
    """起一个本地目标服务（返回可 stop 的 server）。"""
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", LOCAL_PORT), _Handler)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    return srv


def port_open(port, host="127.0.0.1", timeout=2.0):
    import socket
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except Exception:
        return False


def main():
    print("=" * 62)
    print("Graw FRP 面板 E2E 实测：配置 -> 重启 -> 穿透联通")
    print("=" * 62)
    setup()
    s = requests.Session()
    s.headers.update(entry_headers())
    fail = 0

    def step(name, cond, detail=""):
        nonlocal fail
        print(("  PASS  " if cond else "  FAIL  ") + name + (f"  ({detail})" if detail else ""))
        if not cond:
            fail += 1

    srv = None
    try:
        try:
            step("后端健康检查", s.get(BASE + "/api/health").status_code == 200)
        except Exception as e:
            step("后端健康检查", False, str(e))
            return fail

        r = s.post(BASE + "/api/auth/login", json={"username": E2E_USER, "password": E2E_PASS})
        step("管理员登录", r.status_code == 200, (r.text[:120] if r.status_code != 200 else ""))
        if r.status_code != 200:
            return fail
        tok = r.json()["token"]
        s.headers.update({"Authorization": f"Bearer {tok}"})

        server_cfg = {
            "configPath": FRPS_CFG, "bindAddr": "127.0.0.1", "bindPort": BIND_PORT,
            "token": "e2etoken", "dashboardAddr": "127.0.0.1", "dashboardPort": 0,
            "dashboardUser": "", "dashboardPwd": "", "logLevel": "info",
        }
        client_cfg = {
            "serverAddr": "127.0.0.1", "serverPort": BIND_PORT, "token": "e2etoken",
            "configPath": FRPC_CFG, "loginFailExit": True, "logLevel": "info",
        }
        r = s.put(BASE + "/api/frp/config", json={
            "mode": "server", "serverBin": FRPS, "clientBin": FRPC,
            "server": server_cfg, "client": client_cfg,
        })
        step("保存 frps 配置", r.status_code == 200, r.text[:120])
        if r.status_code != 200:
            return fail
        print("   写入配置: " + FRPS_CFG + " / " + FRPC_CFG)

        r = s.post(BASE + "/api/frp/start")
        step("启动 frps", r.status_code == 200, r.text[:120])
        time.sleep(1.5)
        step("frps 绑定端口已监听", port_open(BIND_PORT))
        st = s.get(BASE + "/api/frp/status").json()
        step("status.running=true", st.get("running") is True, str(st))

        srv = start_local_service()
        step("本地目标服务已启动", port_open(LOCAL_PORT))

        r = s.post(BASE + "/api/frp/mode", json={"mode": "client"})
        step("切换为客户端模式", r.status_code == 200)
        r = s.post(BASE + "/api/frp/proxies", json={
            "name": "e2e", "type": "tcp", "localIp": "127.0.0.1",
            "localPort": LOCAL_PORT, "remotePort": REMOTE_PORT,
            "customDomains": "", "useEncryption": False, "useCompression": False,
            "enabled": True, "remark": "e2e",
        })
        step("添加 tcp 代理", r.status_code == 200, r.text[:120])

        r = s.post(BASE + "/api/frp/start")
        step("启动 frpc", r.status_code == 200, r.text[:120])
        time.sleep(2.0)
        step("frps 对外端口已监听(待转发)", port_open(REMOTE_PORT))

        try:
            resp = s.get(f"http://127.0.0.1:{REMOTE_PORT}/", timeout=5)
            ok_conn = resp.status_code == 200 and "FRP-E2E-OK" in resp.text
            step("穿透联通(frps远端->本地服务)", ok_conn, f"HTTP {resp.status_code}: {resp.text[:40]}")
        except Exception as e:
            step("穿透联通(frps远端->本地服务)", False, str(e))

        pv = s.get(BASE + "/api/frp/preview").json().get("toml", "")
        step("预览配置已生成", "[[proxies]]" in pv and "e2e" in pv)

        if srv:
            srv.shutdown()
            srv.server_close()
    except Exception:
        import traceback
        traceback.print_exc()
        fail += 1
    finally:
        if srv:
            try:
                srv.shutdown(); srv.server_close()
            except Exception:
                pass
        teardown()

    print("=" * 62)
    print(("E2E 通过" if fail == 0 else f"存在 {fail} 项失败") + "，已清理测试账号/进程")
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())