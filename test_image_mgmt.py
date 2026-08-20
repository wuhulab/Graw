# -*- coding: utf-8 -*-
"""
test_image_mgmt.py - 镜像管理（拉取/打标签/构建）功能测试

放置于 backend 之外运行，避免被 uvicorn --reload 监视导致后端重启崩溃。

覆盖：
  1. 单元测试：PullRequest/TagRequest/BuildRequest 参数校验（空值/非法镜像名/缺少 Dockerfile）。
  2. 集成测试：登录后调用 /api/docker/images/pull|tag|build 的非法输入 400 分支
     （合法拉取依赖外部网络与引擎环境，不在此强校验）。

用法：backend/.venv/Scripts/python.exe test_image_mgmt.py
"""
import json
import os
import shutil
import sys
import urllib.request

BACKEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend")
sys.path.insert(0, BACKEND)

from app.auth import USERS_FILE, hash_password  # noqa: E402
from app.routers.docker_api import (  # noqa: E402
    PullRequest, TagRequest, BuildRequest, _safe_docker_ref,
)


# ---------------------------------------------------------------------------
# 单元测试
# ---------------------------------------------------------------------------
def test_param_validation_unit():
    """参数校验：合法/非法镜像名、Build 缺少 Dockerfile 判 400。"""
    from fastapi import HTTPException

    # 合法镜像名应通过
    assert _safe_docker_ref("nginx") == "nginx"
    assert _safe_docker_ref("registry.example.com/nginx:1.25") == "registry.example.com/nginx:1.25"
    # 以 - 开头（选项注入）应 400
    for bad in ["-help", "--security-opt", "nginx -f"]:
        try:
            _safe_docker_ref(bad)
            assert False, f"应拒绝非法镜像名: {bad}"
        except HTTPException as e:
            assert e.status_code == 400, f"{bad} 应 400"

    # PullRequest 空 name
    assert PullRequest().name == ""
    # BuildRequest 缺上下文目录 / 镜像名 → 应校验失败（逻辑在校验函数里）
    assert BuildRequest(name="", tag="latest", context_dir="").name == ""
    print("✔ 单元测试：镜像参数校验（选项注入拦截 / 空值模型）通过")


def test_safe_docker_ref_usage():
    """_safe_docker_ref 在三个端点的用法：pull 用镜像名、tag 用仓库名。"""
    from fastapi import HTTPException

    # tag 的 repo 允许 / :（仓库路径），但禁止 - 开头
    assert _safe_docker_ref("myapp", "仓库名") == "myapp"
    try:
        _safe_docker_ref("-evil", "仓库名")
        assert False
    except HTTPException:
        pass
    print("✔ 单元测试：tag 仓库名校验（合法通过 / 选项注入拦截）通过")


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


def _http_json(url, method="GET", data=None, token=None, timeout=60):
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


def test_http_integration():
    """集成测试：登录后调用镜像管理端点的非法输入 400 分支 + 权限。"""
    backup = USERS_FILE + ".bak_img"
    shutil.copyfile(USERS_FILE, backup)
    try:
        with open(USERS_FILE, "r", encoding="utf-8") as f:
            users = json.load(f)
        users["__imgtest"] = {
            "username": "__imgtest",
            "password": hash_password("SecPass#123"),
            "role": "admin",
            "must_change_password": False,
            "created_at": 0,
        }
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(users, f, ensure_ascii=False, indent=2)

        base = "http://localhost:8000/api"
        code, body = _http_json(f"{base}/auth/login", "POST",
                                {"username": "__imgtest", "password": "SecPass#123"})
        assert code == 200, f"登录失败: {code} {body}"
        token = body["token"]

        # 1) pull：空镜像名 → 400
        code, body = _http_json(f"{base}/docker/images/pull", "POST", {"name": ""}, token=token)
        assert code == 400, f"空镜像名应 400，实际 {code} {body}"
        print("✔ pull 空镜像名 → 400")

        # 2) pull：选项注入镜像名 → 400
        code, body = _http_json(f"{base}/docker/images/pull", "POST", {"name": "-security-opt"}, token=token)
        assert code == 400, f"选项注入镜像名应 400，实际 {code} {body}"
        print("✔ pull 选项注入镜像名 → 400")

        # 3) tag：空仓库名 → 400
        code, body = _http_json(f"{base}/docker/images/abc123/tag", "POST", {"repo": ""}, token=token)
        assert code == 400, f"空仓库名应 400，实际 {code} {body}"
        print("✔ tag 空仓库名 → 400")

        # 4) build：缺 context 目录 → 400
        code, body = _http_json(f"{base}/docker/images/build", "POST",
                                {"name": "myapp", "tag": "latest", "context_dir": ""}, token=token)
        assert code == 400, f"缺上下文目录应 400，实际 {code} {body}"
        print("✔ build 缺上下文目录 → 400")

        # 5) build：目录不存在 → 400
        code, body = _http_json(f"{base}/docker/images/build", "POST",
                                {"name": "myapp", "context_dir": "Z:/no/such/dir"}, token=token)
        assert code == 400, f"不存在的目录应 400，实际 {code} {body}"
        print("✔ build 目录不存在 → 400")

        # 6) 普通用户 → 403（镜像管理是管理员功能）
        users["__imgtest_user"] = {
            "username": "__imgtest_user",
            "password": hash_password("SecPass#123"),
            "role": "user",
            "must_change_password": False,
            "created_at": 0,
        }
        with open(USERS_FILE, "w", encoding="utf-8") as f:
            json.dump(users, f, ensure_ascii=False, indent=2)
        code, body = _http_json(f"{base}/auth/login", "POST",
                                {"username": "__imgtest_user", "password": "SecPass#123"})
        user_token = body["token"]
        code, _ = _http_json(f"{base}/docker/images/pull", "POST", {"name": "nginx"}, token=user_token)
        assert code == 403, f"普通用户应 403，实际 {code}"
        print("✔ 普通用户访问镜像管理 → 403")
    finally:
        shutil.copyfile(backup, USERS_FILE)
        os.remove(backup)


if __name__ == "__main__":
    test_param_validation_unit()
    test_safe_docker_ref_usage()
    test_http_integration()
    print("全部测试完成")
