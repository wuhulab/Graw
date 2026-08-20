# -*- coding: utf-8 -*-
"""
test_dockervolumes.py - Docker 数据卷管理功能测试

放置于 backend 之外运行，避免被 uvicorn --reload 监视导致后端重启崩溃。

策略：
  - 单元测试：_safe_docker_ref 数据卷名校验（选项注入拦截）、字段兼容取值。
  - 集成测试：FastAPI TestClient 挂载 auth + docker_api + dockervolumes 路由，
    登录请求携带 X-ShunX-Entry 头（读真实配置，与前端一致），并 mock CLI 后端
    验证列表 / 删除 / inspect 三个端点；覆盖未登录 401、普通用户 403、无引擎 503。

用法：backend/.venv/Scripts/python.exe test_dockervolumes.py
"""
import json
import os
import sys
import tempfile
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend"))

from fastapi import Depends, FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app import auth as auth_mod  # noqa: E402
from app.routers import auth as auth_router  # noqa: E402
from app.routers import docker_api  # noqa: E402
from app.routers import dockervolumes  # noqa: E402

PASS = 0
FAIL = 0


def ok(name, detail=""):
    global PASS
    PASS += 1
    msg = f"  PASS  {name}"
    if detail:
        msg += f"  ({detail})"
    print(msg)


def fail(name, detail):
    global FAIL
    FAIL += 1
    print(f"  FAIL  {name}: {detail}")


def check(name, cond, detail=""):
    if cond:
        ok(name, detail)
    else:
        fail(name, detail)


# ---------------------------------------------------------------------------
# 单元测试
# ---------------------------------------------------------------------------
def test_param_validation_unit():
    """数据卷名校验：合法通过 / 以 - 开头（选项注入）等非法一律 400。"""
    # 合法数据卷名应通过
    assert dockervolumes._safe_docker_ref("mydata") == "mydata"
    assert dockervolumes._safe_docker_ref("graw-db_01.v2") == "graw-db_01.v2"
    # 选项注入 / 空白 / 引号应 400（注意：共享校验允许 / 与 :，那是镜像引用的
    # 合法字符；对数据卷而言非法名会在引擎层报错，但不会构成注入）
    for bad in ["-help", "--security-opt", "vol a", "vol\"x"]:
        try:
            dockervolumes._safe_docker_ref(bad)
            fail("选项注入拦截", f"应拒绝: {bad!r}")
            return
        except HTTPException as e:
            if e.status_code != 400:
                fail("选项注入拦截", f"{bad!r} 应 400，实际 {e.status_code}")
                return
    ok("选项注入拦截", "非法数据卷名一律 400")

    # 字段兼容取值：podman 不同版本的 volume ls json 大小写字段
    assert dockervolumes._vol_get({"Name": "a", "name": "b"}, "Name", "name") == "a"
    assert dockervolumes._vol_get({"name": "b"}, "Name", "name") == "b"
    assert dockervolumes._vol_get({"Mountpoint": "/mnt/x"}, "Mountpoint", "mountpoint") == "/mnt/x"
    assert dockervolumes._vol_get({"Driver": "local"}, "Driver", "driver") == "local"
    assert dockervolumes._vol_get({}, "Name", "name") == ""
    ok("字段兼容取值", "Name/name、Mountpoint/mountpoint 兼容")


# ---------------------------------------------------------------------------
# 集成测试（TestClient + X-ShunX-Entry）
# ---------------------------------------------------------------------------
def _entry_headers():
    """读真实 ShunX 入口配置，返回登录用 X-ShunX-Entry 头（与前端一致）。"""
    cfg = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend", "data", "shunx.json")
    entry = None
    try:
        with open(cfg, "r", encoding="utf-8") as f:
            entry = json.load(f).get("entry_path")
    except Exception:
        pass
    return {"X-ShunX-Entry": entry} if entry else {}


def _build_app_and_login():
    """构建挂载 auth + docker + dockervolumes 的测试应用，并登录获取 admin / user 令牌。

    将用户表与会话文件指向临时文件，避免污染真实数据。
    返回 (client, admin_token, user_token)。
    """
    tmp = tempfile.mkdtemp(prefix="graw_vol_test_")
    users_file = os.path.join(tmp, "users.json")
    sessions_file = os.path.join(tmp, "sessions.json")
    # 用户表 / 会话文件指向临时文件（auth 模块按调用时全局变量取值，patch 生效）
    auth_mod.USERS_FILE = users_file
    auth_mod.SESSIONS_FILE = sessions_file

    with open(users_file, "w", encoding="utf-8") as f:
        json.dump({
            "__voladmin": {
                "username": "__voladmin",
                "password": auth_mod.hash_password("SecPass#123"),
                "role": "admin",
                "must_change_password": False,
                "token_version": 0,
                "created_at": 0,
            },
            "__voluser": {
                "username": "__voluser",
                "password": auth_mod.hash_password("SecPass#123"),
                "role": "user",
                "must_change_password": False,
                "token_version": 0,
                "created_at": 0,
            },
        }, f, ensure_ascii=False, indent=2)

    app = FastAPI()
    app.include_router(auth_router.router, prefix="/api/auth")
    app.include_router(
        docker_api.router, prefix="/api/docker", dependencies=[Depends(auth_mod.require_admin)]
    )
    app.include_router(
        dockervolumes.router,
        prefix="/api/dockervolumes",
        dependencies=[Depends(auth_mod.require_admin)],
    )
    client = TestClient(app)

    def login(username):
        r = client.post(
            "/api/auth/login",
            json={"username": username, "password": "SecPass#123"},
            headers=_entry_headers(),
        )
        assert r.status_code == 200, f"登录失败 {username}: {r.status_code} {r.text}"
        return r.json()["token"]

    admin_token = login("__voladmin")
    user_token = login("__voluser")
    return client, admin_token, user_token


def _admin_headers(token):
    headers = {"Authorization": f"Bearer {token}"}
    headers.update(_entry_headers())
    return headers


def test_access_control():
    """未登录 401 / 普通用户 403 / 管理员可访问。"""
    client, admin_token, user_token = _build_app_and_login()

    r = client.get("/api/dockervolumes")
    check("未登录访问返回 401", r.status_code == 401, str(r.status_code))

    r = client.get("/api/dockervolumes", headers=_admin_headers(user_token))
    check("普通用户访问返回 403", r.status_code == 403, str(r.status_code))

    # 管理员：mock CLI 后端后应 200（真实环境无引擎会 503，这里先验证鉴权放行）
    with mock.patch.object(dockervolumes, "get_backend", return_value=("cli", None)), \
         mock.patch.object(dockervolumes, "_podman_json", return_value=[]):
        r = client.get("/api/dockervolumes", headers=_admin_headers(admin_token))
    check("管理员访问返回 200", r.status_code == 200, str(r.status_code))


def test_volumes_list_cli():
    """CLI 模式：volume ls --format json 解析为 {name, driver, mountpoint}。"""
    client, admin_token, _ = _build_app_and_login()
    sample = [
        {"Name": "pgdata", "Driver": "local", "Mountpoint": "/var/lib/containers/storage/volumes/pgdata"},
        {"name": "webdata", "driver": "local", "mountpoint": "/var/lib/docker/volumes/webdata/_data"},
    ]
    with mock.patch.object(dockervolumes, "get_backend", return_value=("cli", None)), \
         mock.patch.object(dockervolumes, "_podman_json", return_value=sample):
        r = client.get("/api/dockervolumes", headers=_admin_headers(admin_token))
    check("列表返回 200", r.status_code == 200, str(r.status_code))
    data = r.json()
    check("列表解析 2 条", isinstance(data, list) and len(data) == 2, str(data))
    if len(data) == 2:
        check("首条字段完整", data[0]["name"] == "pgdata" and data[0]["driver"] == "local"
              and "pgdata" in data[0]["mountpoint"], str(data[0]))
        check("小写字段兼容", data[1]["name"] == "webdata", str(data[1]))


def test_volume_remove_cli():
    """CLI 模式：删除数据卷拼 `volume rm -f <name>`，非法名 400。"""
    client, admin_token, _ = _build_app_and_login()
    calls = {}

    def fake_run(cmd, timeout=30):
        calls["cmd"] = cmd
        return 0, "", ""

    with mock.patch.object(dockervolumes, "get_backend", return_value=("cli", None)), \
         mock.patch.object(dockervolumes, "_find_podman", return_value=["podman"]), \
         mock.patch.object(dockervolumes, "_run", side_effect=fake_run):
        r = client.post("/api/dockervolumes/mydata/remove", headers=_admin_headers(admin_token))
    check("删除返回 200", r.status_code == 200 and r.json().get("ok") is True, r.text)
    check("CLI 命令正确", calls.get("cmd") == ["podman", "volume", "rm", "-f", "mydata"],
          str(calls.get("cmd")))

    # 非法名（选项注入）→ 400，不应触发 CLI
    calls.clear()
    with mock.patch.object(dockervolumes, "get_backend", return_value=("cli", None)):
        r = client.post("/api/dockervolumes/-evil/remove", headers=_admin_headers(admin_token))
    check("选项注入删除返回 400", r.status_code == 400, str(r.status_code) + r.text)
    check("非法名未触发 CLI", "cmd" not in calls, str(calls))


def test_volume_inspect_cli():
    """CLI 模式：inspect 返回属性；不存在返回 404。"""
    client, admin_token, _ = _build_app_and_login()
    attrs = {"Name": "pgdata", "Driver": "local", "Mountpoint": "/var/lib/...",
             "Labels": {"app": "pg"}, "Options": {}}
    with mock.patch.object(dockervolumes, "get_backend", return_value=("cli", None)), \
         mock.patch.object(dockervolumes, "_podman_json", return_value=[attrs]):
        r = client.get("/api/dockervolumes/pgdata/inspect", headers=_admin_headers(admin_token))
    check("inspect 返回 200", r.status_code == 200, str(r.status_code))
    check("inspect 返回属性", r.json().get("Name") == "pgdata" and r.json().get("Mountpoint"),
          str(r.json()))

    with mock.patch.object(dockervolumes, "get_backend", return_value=("cli", None)), \
         mock.patch.object(dockervolumes, "_podman_json", return_value=[]):
        r = client.get("/api/dockervolumes/ghost/inspect", headers=_admin_headers(admin_token))
    check("inspect 不存在返回 404", r.status_code == 404, str(r.status_code))


def test_no_engine_503():
    """后端不可用时统一返回 503（与 docker_api 行为一致）。"""
    client, admin_token, _ = _build_app_and_login()
    with mock.patch.object(dockervolumes, "get_backend",
                           side_effect=HTTPException(status_code=503, detail="未检测到运行中的 Docker/Podman 服务")):
        r = client.get("/api/dockervolumes", headers=_admin_headers(admin_token))
    check("无引擎返回 503", r.status_code == 503, str(r.status_code) + r.text)


def test_networks_endpoint_available():
    """前端「网络」标签依赖的 dockerApi.networks() 端点存在且可解析（mock CLI）。"""
    client, admin_token, _ = _build_app_and_login()
    sample = [{
        "name": "bridge", "id": "abcdef1234567890",
        "driver": "bridge", "network_interface": "br0",
        "created": "2026-01-01T00:00:00", "internal": False,
        "subnets": [{"subnet": "172.17.0.0/16", "gateway": "172.17.0.1"}],
        "labels": {},
    }]
    with mock.patch.object(docker_api, "get_backend", return_value=("cli", None)), \
         mock.patch.object(docker_api, "_podman_json", return_value=sample):
        r = client.get("/api/docker/networks", headers=_admin_headers(admin_token))
    check("网络列表返回 200", r.status_code == 200, str(r.status_code))
    data = r.json()
    check("网络字段解析", len(data) == 1 and data[0]["name"] == "bridge"
          and data[0]["subnets"][0]["gateway"] == "172.17.0.1", str(data))


def main():
    print("== dockervolumes.py 回归测试 ==")
    test_param_validation_unit()
    test_access_control()
    test_volumes_list_cli()
    test_volume_remove_cli()
    test_volume_inspect_cli()
    test_no_engine_503()
    test_networks_endpoint_available()
    print(f"\n结果: PASS={PASS} FAIL={FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
