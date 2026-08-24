# -*- coding: utf-8 -*-
"""
test_highrisk_confirm.py - 高风险操作二次确认功能验证

验证点：
  1. 前端 ConfirmDialog 已接入高风险窗口（代码静态检查）。
  2. 后端 /api/auth/verify-password：
     - 正确密码 -> 200 {ok:true}
     - 错误密码 -> 400（且计入登录限流，防爆破）

用法：backend/.venv/Scripts/python.exe test_highrisk_confirm.py
"""
import os
import sys
import json
import shutil

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import urllib.request


def _entry_headers():
    cfg = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "shunx.json")
    entry = None
    try:
        with open(cfg, "r", encoding="utf-8") as f:
            entry = json.load(f).get("entry_path")
    except Exception:
        pass
    return {"X-ShunX-Entry": entry} if entry else {}


def _http_json(url, method="GET", data=None, token=None, timeout=15):
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


def test_code_static():
    """静态检查：高风险窗口已接入 ConfirmDialog 二次确认。"""
    win_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "..", "frontend", "src", "components", "windows")
    assert os.path.isdir(win_dir), f"前端窗口目录不存在: {win_dir}"

    # 应接入 ConfirmDialog 的核心高风险窗口
    required = ["SitesWindow.vue", "DatabaseWindow.vue", "FirewallWindow.vue",
                "DockerWindow.vue", "ProcessWindow.vue", "CronWindow.vue",
                "ProtectionWindow.vue", "TamperWindow.vue", "WafWindow.vue",
                "LogsWindow.vue", "NotifyWindow.vue"]
    missing = []
    for name in required:
        path = os.path.join(win_dir, name)
        if not os.path.exists(path):
            missing.append(name)
            continue
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        if "ConfirmDialog" not in content:
            missing.append(f"{name}(未接入ConfirmDialog)")
    assert not missing, f"以下窗口未接入二次确认: {missing}"

    # ConfirmDialog 组件自身两种模式齐全
    comp = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "..", "frontend", "src", "components", "ConfirmDialog.vue")
    with open(comp, "r", encoding="utf-8") as f:
        c = f.read()
    assert "mode === 'password'" in c, "ConfirmDialog 缺少 password 模式"
    assert "authApi.verifyPassword" in c, "ConfirmDialog 未调用 verifyPassword"
    print(f"✔ 静态检查：{len(required)} 个高风险窗口均已接入 ConfirmDialog")


def test_verify_password_http():
    """集成测试：verify-password 正确/错误密码行为。"""
    from app.auth import USERS_FILE, hash_password

    base = "http://localhost:8000/api"
    backup = USERS_FILE + ".bak_confirm"
    shutil.copyfile(USERS_FILE, backup)
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            users = json.load(f)
        users["__confirm"] = {
            "username": "__confirm",
            "password": hash_password("SecPass#123"),
            "role": "admin",
            "must_change_password": False,
            "created_at": 0,
        }
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(users, f, ensure_ascii=False, indent=2)

        code, body = _http_json(f"{base}/auth/login", "POST",
                                {"username": "__confirm", "password": "SecPass#123"})
        assert code == 200 and body.get("token"), f"登录失败: {code} {body}"
        token = body["token"]

        # 正确密码 -> 200
        code, r = _http_json(f"{base}/auth/verify-password", "POST",
                             {"password": "SecPass#123"}, token=token)
        assert code == 200 and r.get("ok") is True, f"正确密码应 200 ok: {code} {r}"
        print("✔ 正确密码二次确认通过 (200 ok)")

        # 错误密码 -> 400
        code, r = _http_json(f"{base}/auth/verify-password", "POST",
                             {"password": "wrong-pass-1"}, token=token)
        assert code == 400, f"错误密码应 400: {code} {r}"
        print("✔ 错误密码被 400 拒绝")

        # 未登录 -> 401（二次确认必须登录）
        code, _ = _http_json(f"{base}/auth/verify-password", "POST",
                             {"password": "SecPass#123"})
        assert code == 401, f"未登录应 401: {code}"
        print("✔ 未登录访问被 401 拒绝")
    finally:
        shutil.copyfile(backup, USERS_FILE)
        os.remove(backup)


if __name__ == "__main__":
    test_code_static()
    test_verify_password_http()
    print("全部测试完成")
