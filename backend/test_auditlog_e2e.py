# -*- coding: utf-8 -*-
"""
面板日志端到端冒烟验证（使用 TestClient，无需真实后端服务）

流程：启动测试用户表快照 -> 用 TestClient 登录 -> 写一个临时文件 ->
检查 data/panel.log 是否包含「登录成功」「写文件」等审计行 -> 恢复 users.json。
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402
from app import auth as auth_mod  # noqa: E402
from app import auditlog  # noqa: E402

USERS_FILE = auth_mod.USERS_FILE
BACKUP = USERS_FILE + ".auditbk"


def snapshot():
    if os.path.exists(USERS_FILE):
        shutil.copy2(USERS_FILE, BACKUP)


def restore():
    if os.path.exists(BACKUP):
        shutil.copy2(BACKUP, USERS_FILE)
        os.remove(BACKUP)


def main():
    snapshot()
    try:
        # 临时注入一个管理员账号用于测试
        users = auth_mod._load_users() or {}
        users["audittestadmin"] = {
            "username": "audittestadmin",
            "password": auth_mod.hash_password("AuditTest12345"),
            "role": "admin",
            "must_change_password": False,
            "token_version": 0,
            "created_at": 0,
        }
        auth_mod._save_users(users)

        client = TestClient(app)
        # 登录 - 需带 ShunX 安全入口头（面板已配置入口 shunianssy）
        entry_headers = {"X-ShunX-Entry": "shunianssy"}
        r = client.post(
            "/api/auth/login",
            json={"username": "audittestadmin", "password": "AuditTest12345"},
            headers=entry_headers,
        )
        assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
        token = r.json()["token"]
        headers = {"Authorization": f"Bearer {token}"}

        # 写一个临时文件触发审计
        tmpf = os.path.join(tempfile.gettempdir(), "graw_audit_smoke.txt")
        r = client.post("/api/files/write", json={"path": tmpf, "content": "audit-smoke"}, headers=headers)
        print("write status:", r.status_code)
        try:
            os.remove(tmpf)
        except OSError:
            pass

        # 读取面板日志
        log_path = auditlog.PANEL_LOG
        if not os.path.exists(log_path):
            print("FAIL panel.log 不存在")
            return 1
        with open(log_path, "r", encoding="utf-8") as f:
            content = f.read()
        ok = True
        for needle in ("登录成功", "audittestadmin", "写文件"):
            if needle not in content:
                print(f"FAIL 面板日志缺少: {needle}")
                ok = False
        if ok:
            print("PASS 面板日志已记录登录/写文件操作")
        # 读日志走的是 logs.py 白名单，确认 panel.log 可被读取
        r = client.get(f"/api/logs/read?path={log_path}&tail=50", headers=headers)
        print("panel.log read status:", r.status_code)
        return 0 if ok and r.status_code == 200 else 1
    finally:
        restore()


if __name__ == "__main__":
    sys.exit(main())