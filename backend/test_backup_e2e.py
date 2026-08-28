# -*- coding: utf-8 -*-
"""
备份中心 E2E 回归测试（需后端运行在 8000 端口）

策略：
  - 脚本执行前备份 users.json，注入临时测试账号（bkadmin/bkuser），
    跑完自动恢复原文件，不影响真实账号。
  - 覆盖：创建/更新/删除任务、手动立即备份、备份记录扫描、
    轮转清理（保留份数）、一键恢复、记录删除、权限分级（普通用户 403）。

用法：
  python test_backup_e2e.py          # 默认 http://localhost:8000
  python test_backup_e2e.py 8011     # 指定端口
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import http.server
import bcrypt
import requests

PORT = sys.argv[1] if len(sys.argv) > 1 else "8000"
BASE = f"http://localhost:{PORT}"
HERE = os.path.dirname(os.path.abspath(__file__))
USERS_FILE = os.path.join(HERE, "data", "users.json")
BACKUP_FILE = USERS_FILE + ".bak_backupe2e"

ADMIN = "bkadmin"
USER = "bkuser"
PASS = "BkPass#123"

PASS_N = 0
FAIL_N = 0


class WebDAVMock(http.server.BaseHTTPRequestHandler):
    """本地 WebDAV mock：接收 MKCOL/PROPFIND/PUT，验证上传链路。"""
    store = {}

    def log_message(self, *a):
        pass

    def _ok(self, code=201):
        self.send_response(code)
        self.end_headers()

    def do_PROPFIND(self):
        self.send_response(207)
        self.send_header("Content-Type", "application/xml")
        self.end_headers()
        self.wfile.write(b'<?xml version="1.0"?><d:multistatus/>')

    def do_MKCOL(self):
        self._ok(201)

    def do_PUT(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        data = self.rfile.read(length) if length else b""
        WebDAVMock.store[self.path] = data
        self._ok(201)


def start_dav_mock():
    """启动本地 WebDAV mock，返回 (server, base_url)。"""
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), WebDAVMock)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    host, port = server.server_address
    WebDAVMock.store.clear()
    return server, f"http://{host}:{port}/dav"


def ok(name, detail=""):
    global PASS_N
    PASS_N += 1
    print(f"  PASS  {name}" + (f"  ({detail})" if detail else ""))


def fail(name, detail):
    global FAIL_N
    FAIL_N += 1
    print(f"  FAIL  {name}: {detail}")


def check(name, cond, detail=""):
    if cond:
        ok(name, detail)
    else:
        fail(name, detail)


def _entry_headers():
    cfg = os.path.join(HERE, "data", "shunx.json")
    entry = None
    try:
        with open(cfg, "r", encoding="utf-8") as f:
            entry = json.load(f).get("entry_path")
    # CodeQL [py/empty-except] 读取可选 shunx.json：未配置/损坏时静默忽略
    except Exception:
        pass
    return {"X-ShunX-Entry": entry} if entry else {}


def login(username, password):
    return requests.post(f"{BASE}/api/auth/login",
                         json={"username": username, "password": password},
                         headers=_entry_headers())


def hdr(token):
    return {"Authorization": f"Bearer {token}"}


def setup():
    shutil.copy2(USERS_FILE, BACKUP_FILE)
    with open(USERS_FILE, "r", encoding="utf-8") as f:
        users = json.load(f)
    pw = bcrypt.hashpw(PASS.encode(), bcrypt.gensalt()).decode()
    users[ADMIN] = {"username": ADMIN, "password": pw, "role": "admin",
                    "must_change_password": False, "created_at": 0}
    users[USER] = {"username": USER, "password": pw, "role": "user",
                   "must_change_password": False, "created_at": 0}
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)


def teardown():
    if os.path.exists(BACKUP_FILE):
        shutil.move(BACKUP_FILE, USERS_FILE)


def main():
    setup()
    try:
        run()
    finally:
        teardown()


def run():
    r = login(ADMIN, PASS)
    check("管理员登录", r.status_code == 200, f"status={r.status_code}")
    if r.status_code != 200:
        return
    token = r.json()["token"]
    H = hdr(token)

    # 普通用户登录（权限分级验证）
    ru = login(USER, PASS)
    user_token = ru.json()["token"] if ru.status_code == 200 else ""
    HU = hdr(user_token) if user_token else None

    # 准备临时目录：源 + 目标
    tmp = tempfile.mkdtemp(prefix="graw_bk_e2e_")
    src = os.path.join(tmp, "web")
    os.makedirs(os.path.join(src, "sub"), exist_ok=True)
    with open(os.path.join(src, "index.html"), "w", encoding="utf-8") as f:
        f.write("v1")
    with open(os.path.join(src, "sub", "data.txt"), "w", encoding="utf-8") as f:
        f.write("hello")
    bdir = os.path.join(tmp, "backups")

    # 1. 创建任务（带计划 + 保留份数 1）
    r = requests.post(f"{BASE}/api/backup/tasks", headers=H, json={
        "name": "e2e-web", "type": "dir", "source": src, "target": bdir,
        "schedule": "30 2 * * *", "keep_count": 1, "keep_days": 0, "enabled": True,
    })
    check("创建任务", r.status_code == 200, f"status={r.status_code} detail={r.text[:120]}")
    if r.status_code != 200:
        return
    tid = r.json()["id"]
    check("任务带 cron_task_id", bool(r.json().get("cron_task_id")), r.json().get("cron_task_id") or "")
    check("任务字段完整", all(k in r.json() for k in ("id", "source", "safe", "keep_count", "schedule")))

    # 2. 权限分级：普通用户访问备份接口必须 403
    rp = requests.get(f"{BASE}/api/backup/status", headers=HU)
    check("普通用户访问备份中心被拒", rp.status_code == 403, f"status={rp.status_code}")

    # 3. 非法源路径拒绝
    r = requests.post(f"{BASE}/api/backup/tasks", headers=H, json={
        "name": "bad", "type": "dir", "source": "relative/path", "target": "", "keep_count": 1,
    })
    check("相对路径源被拒", r.status_code == 400, f"status={r.status_code}")

    # 4. 手动立即备份
    r = requests.post(f"{BASE}/api/backup/tasks/{tid}/run", headers=H)
    check("手动备份", r.status_code == 200, f"status={r.status_code} detail={r.text[:120]}")
    first_file = ""
    if r.status_code == 200:
        first_file = r.json().get("file", "")
        check("备份文件生成", os.path.exists(first_file.replace("/", os.sep)), first_file)

    # 5. 记录扫描
    r = requests.get(f"{BASE}/api/backup/records", headers=H)
    recs = r.json()["records"]
    check("记录列表含新备份", any(x["task_id"] == tid for x in recs), f"count={len(recs)}")

    # 6. 轮转：再次备份，保留份数 1 应只留最新一份
    r = requests.post(f"{BASE}/api/backup/tasks/{tid}/run", headers=H)
    check("再次备份", r.status_code == 200, f"status={r.status_code}")
    recs2 = requests.get(f"{BASE}/api/backup/records", headers=H).json()["records"]
    mine = [x for x in recs2 if x["task_id"] == tid]
    check("轮转后仅保留 1 份", len(mine) == 1, f"count={len(mine)}")
    check("旧备份文件已删除", not os.path.exists(first_file.replace("/", os.sep)), first_file)

    # 7. 恢复：修改源文件后从备份还原
    with open(os.path.join(src, "index.html"), "w", encoding="utf-8") as f:
        f.write("MODIFIED")
    latest = mine[0]["name"]
    restore_dir = os.path.join(tmp, "restored")
    r = requests.post(f"{BASE}/api/backup/tasks/{tid}/restore", headers=H,
                      json={"file": latest, "target": restore_dir})
    check("恢复备份", r.status_code == 200, f"status={r.status_code} detail={r.text[:120]}")
    if r.status_code == 200:
        restored = os.path.join(restore_dir, "web", "index.html")
        with open(restored, encoding="utf-8") as f:
            content = f.read()
        check("恢复内容正确", content == "v1", content)
    else:
        fail("恢复备份", r.text[:200])

    # 8. 非法恢复文件名拒绝
    r = requests.post(f"{BASE}/api/backup/tasks/{tid}/restore", headers=H,
                      json={"file": "../../etc/passwd", "target": restore_dir})
    check("非法备份名恢复被拒", r.status_code == 400, f"status={r.status_code}")

    # 9. 删除记录
    r = requests.delete(f"{BASE}/api/backup/records", headers=H, params={"file": latest})
    check("删除记录", r.status_code == 200, f"status={r.status_code}")
    recs3 = requests.get(f"{BASE}/api/backup/records", headers=H).json()["records"]
    check("删除后记录消失", not any(x["task_id"] == tid for x in recs3))

    # 10. 更新任务（改计划为空 = 仅手动）
    r = requests.put(f"{BASE}/api/backup/tasks/{tid}", headers=H, json={"schedule": "", "keep_count": 5})
    check("更新任务", r.status_code == 200, f"status={r.status_code}")
    check("更新后 cron_task_id 清空", r.json().get("cron_task_id") == "", r.json().get("cron_task_id") or "")
    check("更新后保留份数生效", r.json().get("keep_count") == 5)

    # 11. 删除任务
    r = requests.delete(f"{BASE}/api/backup/tasks/{tid}", headers=H)
    check("删除任务", r.status_code == 200, f"status={r.status_code}")
    r = requests.get(f"{BASE}/api/backup/tasks", headers=H)
    check("任务列表为空", all(t["id"] != tid for t in r.json()["tasks"]))

    # ---------- 13. 远程备份（本地 WebDAV mock） ----------
    dav, dav_base = start_dav_mock()
    try:
        # 创建远程目标
        r = requests.post(f"{BASE}/api/backup/remotes", headers=H, json={
            "name": "e2e-dav", "type": "webdav", "base": dav_base,
            "username": "u", "password": "p",
        })
        check("创建远程目标", r.status_code == 200, f"status={r.status_code}")
        rid = r.json()["id"]
        check("远程目标不返回明文密码", "password" not in r.json() and r.json()["has_password"])

        # 非法 URL 拒绝
        r = requests.post(f"{BASE}/api/backup/remotes", headers=H, json={
            "name": "bad", "type": "webdav", "base": "file:///etc", "username": "", "password": "",
        })
        check("非法远程 URL 被拒", r.status_code == 400, f"status={r.status_code}")

        # 测试连接（PROPFIND → mock 207）
        r = requests.post(f"{BASE}/api/backup/remotes/{rid}/test", headers=H)
        check("远程测试连接", r.status_code == 200, f"status={r.status_code} detail={r.text[:120]}")

        # 创建绑定远程的任务（仅手动）
        r = requests.post(f"{BASE}/api/backup/tasks", headers=H, json={
            "name": "e2e-remote-web", "type": "dir", "source": src, "target": bdir,
            "schedule": "", "keep_count": 1, "keep_days": 0, "remote_id": rid, "enabled": True,
        })
        check("创建绑定远程的任务", r.status_code == 200, f"status={r.status_code} detail={r.text[:120]}")
        rtid = r.json()["id"]

        # 手动备份 → 应上传到 WebDAV mock
        r = requests.post(f"{BASE}/api/backup/tasks/{rtid}/run", headers=H)
        check("绑定远程后手动备份", r.status_code == 200, f"status={r.status_code} detail={r.text[:120]}")
        remote_ok = r.status_code == 200 and r.json().get("remote", {}).get("uploaded")
        check("远程上传成功标志", remote_ok, str(r.json().get("remote")) if r.status_code == 200 else "")
        # 校验 mock 确实收到文件（路径 /dav/<safe>/<file>）
        got = [k for k in WebDAVMock.store.keys()]
        check("WebDAV 收到上传文件", len(got) == 1, got[0] if got else "empty")
        if got:
            fname = os.path.basename(got[0])
            check("上传文件命名符合约定", fname.endswith(".tar.gz") and "_" in fname, fname)

        # 删除远程目标后，任务自动解除绑定
        r = requests.delete(f"{BASE}/api/backup/remotes/{rid}", headers=H)
        check("删除远程目标", r.status_code == 200, f"status={r.status_code}")
        tasks_after = requests.get(f"{BASE}/api/backup/tasks", headers=H).json()["tasks"]
        t_after = next(t for t in tasks_after if t["id"] == rtid)
        check("删除远程后任务解除绑定", t_after["remote_id"] == "", t_after["remote_id"] or "")

        # 清理绑定远程的任务
        requests.delete(f"{BASE}/api/backup/tasks/{rtid}", headers=H)
    finally:
        dav.shutdown()

    # 14. 状态接口
    r = requests.get(f"{BASE}/api/backup/status", headers=H)
    check("状态接口", r.status_code == 200 and "backup_dir" in r.json(), f"status={r.status_code}")

    shutil.rmtree(tmp, ignore_errors=True)

    print(f"\n结果：PASS {PASS_N} 项，FAIL {FAIL_N} 项")
    sys.exit(1 if FAIL_N else 0)


if __name__ == "__main__":
    main()
