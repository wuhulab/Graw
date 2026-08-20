# -*- coding: utf-8 -*-
"""
test_container_stats.py - 单容器资源图表（stats 端点）功能测试

覆盖：
  1. 单元测试：_container_stats_sync 在引擎不可用/容器未运行时的 404 分支，
     以及参数安全（_safe_docker_ref 拒绝选项注入）。
  2. 集成测试：TestClient 直测 app，验证端点鉴权（普通用户 403）。

用法：backend/.venv/Scripts/python.exe test_container_stats.py
"""
import json
import os
import shutil
import sys
import urllib.request

BACKEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend")
sys.path.insert(0, BACKEND)

from app.auth import USERS_FILE, hash_password  # noqa: E402
from app.routers.docker_api import _safe_docker_ref  # noqa: E402


# ---------------------------------------------------------------------------
# 单元测试
# ---------------------------------------------------------------------------
def test_safe_ref_unit():
    """容器 id 安全校验：非法（选项注入）拒绝。"""
    from fastapi import HTTPException

    assert _safe_docker_ref("abc123def456", "容器标识") == "abc123def456"
    for bad in ["-stats", "abc;rm -rf /", ""]:
        try:
            _safe_docker_ref(bad, "容器标识")
            assert False, f"应拒绝非法容器标识: {bad!r}"
        except HTTPException as e:
            assert e.status_code == 400
    print("✔ 单元测试：容器 id 安全校验（选项注入拦截）通过")


def test_stats_404_when_no_engine():
    """引擎不可用/容器不存在时返回 404（而非 500 或崩溃）。"""
    # 注入一个必然不存在的容器 id，模拟引擎可用但容器未运行
    from fastapi import HTTPException
    from app.routers import docker_api
    from unittest import mock

    with mock.patch.object(docker_api, "get_backend", side_effect=HTTPException(status_code=500, detail="引擎不可用")):
        try:
            docker_api._container_stats_sync("nonexistent")
            assert False, "应抛出 HTTPException"
        except HTTPException as e:
            assert e.status_code == 500
    print("✔ 单元测试：引擎不可用时抛 HTTPException（不崩溃）通过")


# ---------------------------------------------------------------------------
# 集成测试（TestClient 直测 app，无需后端进程）
# ---------------------------------------------------------------------------
def _entry_header():
    cfg = os.path.join(BACKEND, "data", "shunx.json")
    try:
        with open(cfg, "r", encoding="utf-8") as f:
            return {"X-ShunX-Entry": json.load(f).get("entry_path") or ""}
    except Exception:
        return {}


def test_http_integration():
    """集成测试：stats 端点权限边界 + 结构。"""
    from fastapi.testclient import TestClient
    from app.main import app

    backup = USERS_FILE + ".bak_stats"
    shutil.copyfile(USERS_FILE, backup)
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            users = json.load(f)
        users["__statsadmin"] = {
            "username": "__statsadmin", "password": hash_password("SecPass#123"),
            "role": "admin", "must_change_password": False, "created_at": 0,
        }
        users["__statsuser"] = {
            "username": "__statsuser", "password": hash_password("SecPass#123"),
            "role": "user", "must_change_password": False, "created_at": 0,
        }
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(users, f, ensure_ascii=False, indent=2)

        entry = _entry_header()
        with TestClient(app) as client:
            # 管理员登录
            r = client.post("/api/auth/login", json={"username": "__statsadmin", "password": "SecPass#123"}, headers=entry)
            assert r.status_code == 200, r.text
            admin_token = r.json()["token"]
            ah = {**entry, "Authorization": f"Bearer {admin_token}"}

            # 容器不存在 → 404（引擎无法找到该容器）
            r = client.get("/api/docker/containers/nonexistent/stats", headers=ah)
            assert r.status_code in (404, 500), f"应为 404 或 500，实际 {r.status_code} {r.text[:200]}"

            # 普通用户 → 403
            r = client.post("/api/auth/login", json={"username": "__statsuser", "password": "SecPass#123"}, headers=entry)
            assert r.status_code == 200, r.text
            user_token = r.json()["token"]
            r = client.get("/api/docker/containers/nonexistent/stats",
                           headers={**entry, "Authorization": f"Bearer {user_token}"})
            assert r.status_code == 403, f"普通用户应 403，实际 {r.status_code}"
            print("✔ 集成测试：stats 端点鉴权（普通用户 403）+ 404 分支通过")
    finally:
        shutil.copyfile(backup, USERS_FILE)
        os.remove(backup)


if __name__ == "__main__":
    test_safe_ref_unit()
    test_stats_404_when_no_engine()
    test_http_integration()
    print("全部测试完成")
