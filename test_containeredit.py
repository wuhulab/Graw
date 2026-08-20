# -*- coding: utf-8 -*-
"""
test_containeredit.py - 容器资源与端口编辑功能测试

放置于 backend 之外运行，避免被 uvicorn --reload 监视导致后端重启崩溃。

策略：
  - 单元测试：参数解析（env / 端口 / CPU 核数）、rebuild 命令参数剥离与重组、
    资源限制参数校验。
  - 集成测试：FastAPI TestClient 挂载 auth + docker_api + containeredit 路由，
    登录请求携带 X-ShunX-Entry 头（读真实配置，与前端一致），并 mock CLI 后端
    验证 info / update-limits / rebuild 三个端点；覆盖未登录 401、普通用户 403、
    无引擎 503。

用法：backend/.venv/Scripts/python.exe test_containeredit.py
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
from app.routers import containeredit  # noqa: E402

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
def test_parse_cpu_cores_unit():
    """CPU 核数解析：NanoCpus 优先，CpuQuota/CpuPeriod 回退，均 0 表示不限。"""
    assert containeredit._parse_cpu_cores({"NanoCpus": 2_500_000_000}) == 2.5
    assert containeredit._parse_cpu_cores({"CpuQuota": 50000, "CpuPeriod": 100000}) == 0.5
    assert containeredit._parse_cpu_cores({"CpuQuota": 0, "CpuPeriod": 100000}) == 0.0
    assert containeredit._parse_cpu_cores({}) == 0.0
    ok("CPU 核数解析", "NanoCpus / CpuQuota / 未限制 三种情况")


def test_parse_env_list_unit():
    """环境变量解析：KEY=value 拆分为 {key, value}；畸形条目兼容。"""
    assert containeredit._parse_env_list(["A=1", "B=hello=world", "C"]) == [
        {"key": "A", "value": "1"},
        {"key": "B", "value": "hello=world"},
        {"key": "C", "value": ""},
    ]
    assert containeredit._parse_env_list(None) == []
    assert containeredit._parse_env_list([]) == []
    ok("环境变量解析", "正常 / 含=的值 / 无= / 空列表")


def test_parse_port_bindings_unit():
    """端口绑定解析：dict 展开为 [{ip, host_port, container_port, protocol}]。"""
    pb = {
        "8080/tcp": [{"HostIp": "0.0.0.0", "HostPort": "80"}],
        "9000/udp": [{"HostIp": "", "HostPort": "9000"}],
    }
    result = containeredit._parse_port_bindings(pb)
    assert result == [
        {"ip": "0.0.0.0", "host_port": "80", "container_port": "8080", "protocol": "tcp"},
        {"ip": "", "host_port": "9000", "container_port": "9000", "protocol": "udp"},
    ]
    # 无端口 / 无绑定时返回空
    assert containeredit._parse_port_bindings(None) == []
    assert containeredit._parse_port_bindings({"8080/tcp": None}) == []
    # 无协议后缀时默认 tcp
    assert containeredit._parse_port_bindings({"8080": [{"HostPort": "8080"}]}) == [
        {"ip": "", "host_port": "8080", "container_port": "8080", "protocol": "tcp"}
    ]
    ok("端口绑定解析", "tcp/udp / 默认协议 / 空绑定")


def test_strip_flags_unit():
    """rebuild 命令参数剥离：移除 --env/-e/--publish/-p（分 token 与 = 两种形式）。"""
    args = ["run", "-d", "--name", "test", "-e", "A=1", "--env=B=2", "-p", "8080:80", "--publish=9090:81/tcp", "nginx"]
    result = containeredit._strip_flags(args, ("--env", "-e", "--publish", "-p"))
    # 仅保留 -d、--name test、nginx（--env/-e/--publish/-p 及值全部移除）
    assert result == ["run", "-d", "--name", "test", "nginx"], str(result)
    ok("命令参数剥离", "分 token 与 = 两种形式均移除")


def test_update_limits_validation_unit():
    """资源限制参数校验：cpus 0.1-64；memory 0 或 32-262144。"""
    # 非法 CPU：过低 / 过高 → 400
    for cpus in (0.05, 100.0, -1):
        try:
            containeredit._update_limits_sync(
                "abc", containeredit.UpdateLimitsRequest(cpus=cpus, memory_mb=0)
            )
            fail("CPU 校验", f"应拒绝 cpus={cpus}")
            return
        except HTTPException as e:
            if e.status_code != 400:
                fail("CPU 校验", f"cpus={cpus} 应 400，实际 {e.status_code}")
                return
    # 非法内存：非 0 且不在 32-262144 → 400
    for mb in (1, 31, 300000, -5):
        try:
            containeredit._update_limits_sync(
                "abc", containeredit.UpdateLimitsRequest(cpus=1, memory_mb=mb)
            )
            fail("内存校验", f"应拒绝 memory_mb={mb}")
            return
        except HTTPException as e:
            if e.status_code != 400:
                fail("内存校验", f"memory_mb={mb} 应 400，实际 {e.status_code}")
                return
    ok("资源限制参数校验", "非法 CPU / 非法内存均 400")


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
    """构建挂载 auth + docker + containeredit 的测试应用，并登录获取 admin / user 令牌。"""
    tmp = tempfile.mkdtemp(prefix="graw_cedit_test_")
    users_file = os.path.join(tmp, "users.json")
    sessions_file = os.path.join(tmp, "sessions.json")
    auth_mod.USERS_FILE = users_file
    auth_mod.SESSIONS_FILE = sessions_file

    with open(users_file, "w", encoding="utf-8") as f:
        json.dump({
            "__ceadmin": {
                "username": "__ceadmin",
                "password": auth_mod.hash_password("SecPass#123"),
                "role": "admin",
                "must_change_password": False,
                "token_version": 0,
                "created_at": 0,
            },
            "__ceuser": {
                "username": "__ceuser",
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
        containeredit.router,
        prefix="/api/containeredit",
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

    admin_token = login("__ceadmin")
    user_token = login("__ceuser")
    return client, admin_token, user_token


def _admin_headers(token):
    headers = {"Authorization": f"Bearer {token}"}
    headers.update(_entry_headers())
    return headers


def _sample_inspect():
    """构造一份 podman inspect 输出（对应 CLI 模式 _podman_json 的返回值）。"""
    return [{
        "Id": "a1b2c3d4e5f6",
        "Name": "web",
        "ImageName": "nginx:latest",
        "State": {"Status": "running"},
        "Config": {
            "Image": "nginx:latest",
            "Env": ["NGINX_PORT=80", "TZ=Asia/Shanghai"],
            "CreateCommand": ["podman", "run", "-d", "--name", "web", "-e", "NGINX_PORT=80", "-p", "8080:80/tcp", "nginx:latest"],
        },
        "HostConfig": {
            "NanoCpus": 1_500_000_000,
            "Memory": 536870912,  # 512 MB
            "PortBindings": {"80/tcp": [{"HostIp": "", "HostPort": "8080"}]},
            "RestartPolicy": {"Name": "always"},
        },
    }]


def test_access_control():
    """未登录 401 / 普通用户 403 / 管理员可访问。"""
    client, admin_token, user_token = _build_app_and_login()

    r = client.get("/api/containeredit/abc/info")
    check("未登录访问返回 401", r.status_code == 401, str(r.status_code))

    r = client.get("/api/containeredit/abc/info", headers=_admin_headers(user_token))
    check("普通用户访问返回 403", r.status_code == 403, str(r.status_code))

    with mock.patch.object(containeredit, "get_backend", return_value=("cli", None)), \
         mock.patch.object(containeredit, "_podman_json", return_value=_sample_inspect()):
        r = client.get("/api/containeredit/abc/info", headers=_admin_headers(admin_token))
    check("管理员访问返回 200", r.status_code == 200, str(r.status_code))


def test_info_cli():
    """CLI 模式：info 端点解析 inspect 为可编辑配置。"""
    client, admin_token, _ = _build_app_and_login()
    with mock.patch.object(containeredit, "get_backend", return_value=("cli", None)), \
         mock.patch.object(containeredit, "_podman_json", return_value=_sample_inspect()):
        r = client.get("/api/containeredit/abc/info", headers=_admin_headers(admin_token))
    check("info 返回 200", r.status_code == 200, str(r.status_code))
    d = r.json()
    check("CPU/内存解析", d["cpu_cores"] == 1.5 and d["memory_mb"] == 512
          and d["memory_unlimited"] is False, str(d))
    check("环境变量解析", d["env"] == [
        {"key": "NGINX_PORT", "value": "80"},
        {"key": "TZ", "value": "Asia/Shanghai"},
    ], str(d.get("env")))
    check("端口解析", d["ports"] == [
        {"ip": "", "host_port": "8080", "container_port": "80", "protocol": "tcp"}
    ], str(d.get("ports")))
    check("基础信息", d["name"] == "web" and d["state"] == "running"
          and d["image"] == "nginx:latest" and d["restart_policy"] == "always", str(d))


def test_info_not_found_404():
    """info 端点：容器不存在返回 404。"""
    client, admin_token, _ = _build_app_and_login()
    with mock.patch.object(containeredit, "get_backend", return_value=("cli", None)), \
         mock.patch.object(containeredit, "_podman_json", return_value=[]):
        r = client.get("/api/containeredit/ghost/info", headers=_admin_headers(admin_token))
    check("容器不存在返回 404", r.status_code == 404, str(r.status_code))


def test_update_limits_cli():
    """CLI 模式：update-limits 拼 `podman update --cpus --memory` 命令。"""
    client, admin_token, _ = _build_app_and_login()
    calls = []

    def fake_run(cmd, timeout=30):
        calls.append(cmd)
        return 0, "", ""

    with mock.patch.object(containeredit, "get_backend", return_value=("cli", None)), \
         mock.patch.object(containeredit, "_find_podman", return_value=["podman"]), \
         mock.patch.object(containeredit, "_run", side_effect=fake_run):
        r = client.post(
            "/api/containeredit/abc/update-limits",
            json={"cpus": 2.5, "memory_mb": 512},
            headers=_admin_headers(admin_token),
        )
    check("update-limits 返回 200", r.status_code == 200 and r.json().get("ok") is True, r.text)
    check("CLI 命令正确", len(calls) == 1 and calls[0] == ["podman", "update", "--cpus", "2.5", "--memory", "512m", "abc"],
          str(calls))

    # memory_mb=0 → --memory 0；整数 CPU 去掉尾零（1.0 -> 1）
    calls.clear()
    with mock.patch.object(containeredit, "get_backend", return_value=("cli", None)), \
         mock.patch.object(containeredit, "_find_podman", return_value=["podman"]), \
         mock.patch.object(containeredit, "_run", side_effect=fake_run):
        r = client.post(
            "/api/containeredit/abc/update-limits",
            json={"cpus": 1.0, "memory_mb": 0},
            headers=_admin_headers(admin_token),
        )
    check("不限内存命令正确", len(calls) == 1 and calls[0] == ["podman", "update", "--cpus", "1", "--memory", "0", "abc"],
          str(calls))


def test_update_limits_invalid_400():
    """update-limits：非法参数返回 400，不应触发 CLI。"""
    client, admin_token, _ = _build_app_and_login()
    for body in ({"cpus": 0.05, "memory_mb": 0}, {"cpus": 1, "memory_mb": 10}):
        with mock.patch.object(containeredit, "get_backend", return_value=("cli", None)):
            r = client.post(
                "/api/containeredit/abc/update-limits",
                json=body,
                headers=_admin_headers(admin_token),
            )
        check("非法参数返回 400", r.status_code == 400, f"{body} -> {r.status_code}")

    # 非法容器标识（选项注入）→ 400
    with mock.patch.object(containeredit, "get_backend", return_value=("cli", None)):
        r = client.post(
            "/api/containeredit/-evil/update-limits",
            json={"cpus": 1, "memory_mb": 0},
            headers=_admin_headers(admin_token),
        )
    check("选项注入返回 400", r.status_code == 400, str(r.status_code))


def test_rebuild_cli():
    """CLI 模式：rebuild 剥离旧 env/port 并追加新参数重建。"""
    client, admin_token, _ = _build_app_and_login()
    calls = []

    def fake_run(cmd, timeout=30):
        calls.append(cmd)
        # 第一次是 rm -f，第二次是 create 命令（返回新容器 id）
        if "rm" in cmd:
            return 0, "", ""
        return 0, "a1b2c3d4e5f6", ""

    body = {
        "env": [{"key": "NGINX_PORT", "value": "8080"}, {"key": "TZ", "value": "Asia/Shanghai"}],
        "ports": [{"host_port": "9090", "container_port": "80", "protocol": "tcp"}],
    }
    with mock.patch.object(containeredit, "get_backend", return_value=("cli", None)), \
         mock.patch.object(containeredit, "_podman_json", return_value=_sample_inspect()), \
         mock.patch.object(containeredit, "_find_podman", return_value=["podman"]), \
         mock.patch.object(containeredit, "_run", side_effect=fake_run):
        r = client.post("/api/containeredit/abc/rebuild", json=body, headers=_admin_headers(admin_token))
    check("rebuild 返回 200", r.status_code == 200 and r.json().get("new_container_id") == "a1b2c3d4e5f6", r.text)
    check("rebuild 执行两步", len(calls) == 2, str(len(calls)))
    if len(calls) == 2:
        check("先删旧容器", calls[0] == ["podman", "rm", "-f", "abc"], str(calls[0]))
        # 旧 -e/-p 被移除，新增 --env/--publish，--name 保留
        expected = ["podman", "run",
                    "--env", "NGINX_PORT=8080", "--env", "TZ=Asia/Shanghai",
                    "--publish", "9090:80/tcp",
                    "-d", "--name", "web", "nginx:latest"]
        check("重建命令正确", calls[1] == expected, str(calls[1]))


def test_rebuild_create_starts():
    """CLI 模式：create 类型 CreateCommand 重建后需要手动 start。"""
    client, admin_token, _ = _build_app_and_login()
    calls = []

    def fake_run(cmd, timeout=30):
        calls.append(cmd)
        if "rm" in cmd:
            return 0, "", ""
        if "start" in cmd:
            return 0, "", ""
        return 0, "newid123456", ""

    inspect = [{
        "Id": "x", "Name": "svc",
        "Config": {
            "CreateCommand": ["podman", "create", "-d", "--name", "svc", "redis:7"],
        },
        "HostConfig": {},
    }]
    with mock.patch.object(containeredit, "get_backend", return_value=("cli", None)), \
         mock.patch.object(containeredit, "_podman_json", return_value=inspect), \
         mock.patch.object(containeredit, "_find_podman", return_value=["podman"]), \
         mock.patch.object(containeredit, "_run", side_effect=fake_run):
        r = client.post("/api/containeredit/svc/rebuild", json={"env": [], "ports": []}, headers=_admin_headers(admin_token))
    check("create 重建返回 200", r.status_code == 200, r.text)
    check("create 重建后 start", any("start" in c for c in calls), str(calls))


def test_rebuild_missing_create_cmd_400():
    """rebuild：无法获取 CreateCommand 时返回 400。"""
    client, admin_token, _ = _build_app_and_login()
    inspect = [{"Id": "x", "Name": "web", "Config": {}, "HostConfig": {}}]
    with mock.patch.object(containeredit, "get_backend", return_value=("cli", None)), \
         mock.patch.object(containeredit, "_podman_json", return_value=inspect):
        r = client.post("/api/containeredit/abc/rebuild", json={"env": [], "ports": []}, headers=_admin_headers(admin_token))
    check("无 CreateCommand 返回 400", r.status_code == 400, str(r.status_code) + r.text)


def test_no_engine_503():
    """后端不可用时统一返回 503。"""
    client, admin_token, _ = _build_app_and_login()
    with mock.patch.object(containeredit, "get_backend",
                           side_effect=HTTPException(status_code=503, detail="未检测到运行中的 Docker/Podman 服务")):
        r = client.get("/api/containeredit/abc/info", headers=_admin_headers(admin_token))
    check("无引擎返回 503", r.status_code == 503, str(r.status_code) + r.text)


def main():
    print("== containeredit.py 回归测试 ==")
    test_parse_cpu_cores_unit()
    test_parse_env_list_unit()
    test_parse_port_bindings_unit()
    test_strip_flags_unit()
    test_update_limits_validation_unit()
    test_access_control()
    test_info_cli()
    test_info_not_found_404()
    test_update_limits_cli()
    test_update_limits_invalid_400()
    test_rebuild_cli()
    test_rebuild_create_starts()
    test_rebuild_missing_create_cmd_400()
    test_no_engine_503()
    print(f"\n结果: PASS={PASS} FAIL={FAIL}")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
