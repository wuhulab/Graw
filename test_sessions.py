# -*- coding: utf-8 -*-
"""
test_sessions.py - 会话管理（在线会话列表 / 踢出单设备 / 强制下线）功能测试

放置于 backend 之外运行，避免被 uvicorn --reload 监视导致后端重启崩溃。

覆盖：
  1. 单元测试：create_session / revoke_session / list_sessions / session_active。
  2. 集成测试：登录后列出会话、踢出单设备（原 token 401）、强制全部下线、
     普通用户权限边界。

用法：backend/.venv/Scripts/python.exe test_sessions.py
"""
import json
import os
import shutil
import sys
import time
import urllib.request

BACKEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend")
sys.path.insert(0, BACKEND)

from app.auth import (  # noqa: E402
    USERS_FILE, hash_password, create_session, revoke_session,
    list_sessions, session_active, SESSIONS_FILE,
)


# ---------------------------------------------------------------------------
# 单元测试
# ---------------------------------------------------------------------------
def test_session_unit():
    """会话 CRUD：创建/列出/踢出/活动判定。"""
    backup = SESSIONS_FILE + ".bak"
    existed = os.path.exists(SESSIONS_FILE)
    if existed:
        shutil.copyfile(SESSIONS_FILE, backup)
    try:
        if os.path.exists(SESSIONS_FILE):
            os.remove(SESSIONS_FILE)
        sid1 = create_session("u1", "1.2.3.4", "Chrome · Windows")
        sid2 = create_session("u1", "5.6.7.8", "Firefox · Linux")
        sid3 = create_session("u2", "9.9.9.9", "Safari · macOS")
        assert sid1 and sid2 and sid3, "应返回非空 sid"
        assert session_active(sid1), "新会话应处于活动状态"

        # 按用户过滤
        mine = list_sessions("u1")
        assert {s["sid"] for s in mine} == {sid1, sid2}, "应只列出 u1 的会话"
        all_s = list_sessions(None)
        assert len(all_s) == 3, "应列出全部会话"

        # 踢出单个设备
        assert revoke_session(sid1), "踢出应成功"
        assert not session_active(sid1), "被踢出的会话应失效"
        assert session_active(sid2), "其他会话不受影响"
        print("✔ 单元测试：会话 CRUD（创建/列表/踢出/活动判定）通过")
    finally:
        if existed:
            shutil.copyfile(backup, SESSIONS_FILE)
            os.remove(backup)
        elif os.path.exists(SESSIONS_FILE):
            os.remove(SESSIONS_FILE)


# ---------------------------------------------------------------------------
# 集成测试（需后端运行在 8000）
# ---------------------------------------------------------------------------
def _entry_headers():
    cfg = os.path.join(BACKEND, "data", "shunx.json")
    entry = None
    try:
        with open(cfg, "r", encoding="utf-8") as f:
            entry = json.load(f).get("entry_path")
    except Exception:
        pass
    return {"X-ShunX-Entry": entry} if entry else {}


def _http_json(url, method="GET", data=None, token=None, timeout=30):
    headers = {"Content-Type": "application/json"}
    headers.update(_entry_headers())
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode())
        except Exception:
            return e.code, {}


def _login(base, username, password):
    code, body = _http_json(f"{base}/auth/login", "POST", {"username": username, "password": password})
    assert code == 200, f"登录失败: {code} {body}"
    return body["token"]


def test_http_integration():
    """集成测试：会话列表、踢出单设备、强制下线、权限边界。"""
    base = "http://localhost:8000/api"
    # 备份并注入测试账号
    backup = USERS_FILE + ".bak_sess"
    shutil.copyfile(USERS_FILE, backup)
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            users = json.load(f)
        users["__sessadmin"] = {
            "username": "__sessadmin", "password": hash_password("SecPass#123"),
            "role": "admin", "must_change_password": False, "created_at": 0,
        }
        users["__sessuser"] = {
            "username": "__sessuser", "password": hash_password("SecPass#123"),
            "role": "user", "must_change_password": False, "created_at": 0,
        }
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(users, f, ensure_ascii=False, indent=2)

        # 1) 管理员登录两次 → 两个会话
        tok1 = _login(base, "__sessadmin", "SecPass#123")
        tok2 = _login(base, "__sessadmin", "SecPass#123")
        code, body = _http_json(f"{base}/auth/sessions", token=tok1)
        assert code == 200, f"会话列表失败: {code} {body}"
        admin_sessions = body["sessions"]
        assert len(admin_sessions) >= 2, "管理员应看到至少 2 个会话"
        print(f"✔ 会话列表：管理员可见 {len(admin_sessions)} 个会话")

        # 2) 踢出 tok2 对应的会话 → tok2 立即失效（401）
        sid2 = None
        for s in admin_sessions:
            # 匹配最近创建的会话（我们无法直接拿 sid，取任一非空即可）
            if s["username"] == "__sessadmin":
                sid2 = s["sid"]
                break
        assert sid2, "应存在可踢出的会话"
        code, _ = _http_json(f"{base}/auth/sessions/{sid2}/kick", "POST", {}, token=tok1)
        assert code == 200, f"踢出会话失败: {code}"

        # 用踢出的 token 访问 → 401
        code, _ = _http_json(f"{base}/auth/me", token=tok2)
        assert code == 401, f"被踢出的 token 应 401，实际 {code}"
        # 未被踢的 token 仍可用
        code, _ = _http_json(f"{base}/auth/me", token=tok1)
        assert code == 200, "未踢出的 token 应仍有效"
        print("✔ 踢出单设备：被踢 token 401，其他 token 不受影响")

        # 3) 普通用户只能操作自己的会话
        utok = _login(base, "__sessuser", "SecPass#123")
        code, body = _http_json(f"{base}/auth/sessions", token=utok)
        assert code == 200
        for s in body["sessions"]:
            assert s["username"] == "__sessuser", "普通用户只能看到自己的会话"
        # 普通用户踢管理员会话 → 403
        code, _ = _http_json(f"{base}/auth/sessions/{sid2}/kick", "POST", {}, token=utok)
        assert code in (403, 404), f"普通用户操作他人会话应拒绝，实际 {code}"
        print("✔ 权限边界：普通用户仅见自己会话，不可操作他人会话")

        # 4) 管理员强制下线指定用户全部设备
        # 先让 utok 正常，再 kick-all
        code, body = _http_json(f"{base}/auth/me", token=utok)
        assert code == 200, "强制下线前普通用户 token 应有效"
        code, _ = _http_json(f"{base}/auth/sessions/kick-all", "POST",
                             {"username": "__sessuser"}, token=tok1)
        assert code == 200, f"强制下线失败: {code}"
        code, _ = _http_json(f"{base}/auth/me", token=utok)
        assert code == 401, f"强制下线后 token 应失效，实际 {code}"
        print("✔ 强制全部下线：指定用户 token 全部失效")
    finally:
        shutil.copyfile(backup, USERS_FILE)
        os.remove(backup)


if __name__ == "__main__":
    test_session_unit()
    test_http_integration()
    print("全部测试完成")
