# -*- coding: utf-8 -*-
"""
test_phpversions.py - PHP 多版本管理功能测试

放置于 backend 之外运行，避免被 uvicorn --reload 监视导致后端重启崩溃。

策略：
  - 单元测试：mock subprocess/subprocess 工具，验证 Linux 与 Windows 下的
    PHP 版本探测逻辑（版本解析 / FPM socket 推断 / 目录扫描 / 优雅降级）。
  - 集成测试：FastAPI TestClient 挂载 auth + phpversions 路由，登录请求携带
    X-ShunX-Entry 头（读真实配置，与前端一致），覆盖：
      * 未登录 401、普通用户 403、管理员可访问；
      * set-php 合法/非法版本校验、站点类型限制、持久化到 sites.json。

用法：backend/.venv/Scripts/python.exe test_phpversions.py
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
from app.routers import phpversions  # noqa: E402

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
# 单元测试：版本解析 / FPM socket / Linux 探测 / Windows 探测 / 降级
# ---------------------------------------------------------------------------
def test_parse_version_unit():
    """`php -v` 输出解析：提取 major.minor，白名单校验，归一化。"""
    assert phpversions._parse_version("PHP 8.2.10 (cli) (built: ...)") == "8.2"
    assert phpversions._parse_version("PHP 8.3.0RC1 (cli)") == "8.3"
    assert phpversions._parse_version("PHP 7.4.33 (cli)") == "7.4"
    assert phpversions._parse_version("") is None
    assert phpversions._parse_version(None) is None
    assert phpversions._parse_version("some random text") is None
    # 单段版本也接受（8 -> 8）
    assert phpversions._parse_version("PHP 8 (cli)") == "8"
    ok("PHP 版本解析", "major.minor / 白名单 / 空输入 / 非 PHP 文本")


def test_fpm_socket_unit():
    """FPM socket 路径推断。"""
    assert phpversions._fpm_socket_for("8.2") == "/run/php/php8.2-fpm.sock"
    assert phpversions._fpm_socket_for("7.4") == "/run/php/php7.4-fpm.sock"
    ok("FPM socket 推断", "8.2 / 7.4")


def test_detect_linux_unit():
    """Linux 目录扫描：识别 php8.2 / php-fpm 并调用 php -v 回填。"""
    found = {}

    def fake_isdir(d):
        return d == "/usr/bin"

    def fake_listdir(d):
        return ["php", "php8.2", "php8.3", "php-fpm8.2", "other"]

    def fake_isfile(p):
        return True

    def fake_access(p, mode):
        return True

    def fake_run(argv):
        # 仅 php8.2 / php 会执行 -v（返回 8.2.10）
        return "PHP 8.2.10 (cli) (built: ...)"

    with mock.patch.object(phpversions.platform, "system", return_value="Linux"), \
         mock.patch.object(phpversions.os.path, "isdir", side_effect=fake_isdir), \
         mock.patch.object(phpversions.os, "listdir", side_effect=fake_listdir), \
         mock.patch.object(phpversions.os.path, "isfile", side_effect=fake_isfile), \
         mock.patch.object(phpversions.os, "access", side_effect=fake_access), \
         mock.patch.object(phpversions.shutil, "which", return_value=None), \
         mock.patch.object(phpversions, "_run", side_effect=fake_run):
        result = phpversions.detect_php_versions()

    versions = [x["version"] for x in result]
    check("Linux 检测到 8.2/8.3", set(versions) == {"8.2", "8.3"}, str(versions))
    # 每个版本路径非空
    for x in result:
        check(f"{x['version']} 含 path 与 fpm_sock", bool(x["path"]) and bool(x["fpm_sock"]), str(x))
    check("结果按版本排序", versions == sorted(versions), str(versions))
    ok("Linux PHP 版本探测", f"扫描 + php -v 回填，共 {len(result)} 个")


def test_detect_linux_no_php_unit():
    """Linux 无任何 PHP：返回空列表（优雅降级）。"""
    with mock.patch.object(phpversions.platform, "system", return_value="Linux"), \
         mock.patch.object(phpversions.os.path, "isdir", return_value=False), \
         mock.patch.object(phpversions.os, "listdir", side_effect=lambda d: []), \
         mock.patch.object(phpversions.shutil, "which", return_value=None):
        result = phpversions.detect_php_versions()
    check("Linux 无 PHP 返回空", result == [], str(result))
    ok("Linux 无 PHP 降级", "返回空列表")


def test_detect_windows_unit():
    """Windows：PATH 中存在 php -> 通过 php -v 得到版本。"""
    with mock.patch.object(phpversions.shutil, "which", return_value="C:\\php\\php.exe"), \
         mock.patch.object(phpversions, "_run", return_value="PHP 8.1.24 (cli) (built: ...)"):
        result = phpversions._detect_windows()
    check("Windows 检测到 PHP 8.1", len(result) == 1 and result[0]["version"] == "8.1", str(result))
    # Windows 无 FPM socket
    check("Windows fpm_sock 为空", result and result[0]["fpm_sock"] == "", str(result))


def test_detect_windows_no_php_unit():
    """Windows：PATH 中无 php -> 返回空。"""
    with mock.patch.object(phpversions.shutil, "which", return_value=None):
        result = phpversions._detect_windows()
    check("Windows 无 php 返回空", result == [], str(result))
    ok("Windows 无 PHP 降级", "返回空列表")


def test_detect_platform_selects_windows_unit():
    """detect_php_versions 按平台选择：Windows 走 PATH 探测路径。"""
    with mock.patch.object(phpversions.platform, "system", return_value="Windows"), \
         mock.patch.object(phpversions.shutil, "which", return_value=None):
        result = phpversions.detect_php_versions()
    check("平台选择 Windows 分支", result == [], str(result))
    ok("平台分支选择", "Windows -> PATH 探测（此处为空）")


def test_detect_exception_degrades_unit():
    """探测过程抛异常时优雅降级为空列表。"""
    with mock.patch.object(phpversions.platform, "system", side_effect=RuntimeError("boom")):
        result = phpversions.detect_php_versions()
    check("异常降级为空列表", result == [], str(result))
    ok("异常优雅降级", "返回空列表而非抛出")


# ---------------------------------------------------------------------------
# 集成测试（TestClient + X-ShunX-Entry + 管理员/权限）
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


_TMPDIR = tempfile.mkdtemp(prefix="graw_phpver_test_")


def _build_sites_file():
    """写入临时 sites.json：一个 static、一个 subsite、一个 proxy。"""
    os.makedirs(_TMPDIR, exist_ok=True)
    sites_file = os.path.join(_TMPDIR, "sites.json")
    with open(sites_file, "w", encoding="utf-8") as f:
        json.dump([
            {"id": "site-a", "name": "A", "type": "static", "enabled": True, "port": 80},
            {"id": "site-b", "name": "B", "type": "subsite", "enabled": False, "port": 80},
            {"id": "site-c", "name": "C", "type": "proxy", "enabled": True, "port": 3000},
        ], f, ensure_ascii=False, indent=2)
    return sites_file


def _build_app_and_login():
    """构建挂载 auth + phpversions 的测试应用并登录 admin / user。"""
    os.makedirs(_TMPDIR, exist_ok=True)
    users_file = os.path.join(_TMPDIR, "users.json")
    sessions_file = os.path.join(_TMPDIR, "sessions.json")
    auth_mod.USERS_FILE = users_file
    auth_mod.SESSIONS_FILE = sessions_file

    with open(users_file, "w", encoding="utf-8") as f:
        json.dump({
            "__phpadmin": {
                "username": "__phpadmin",
                "password": auth_mod.hash_password("SecPass#123"),
                "role": "admin",
                "must_change_password": False,
                "token_version": 0,
                "created_at": 0,
            },
            "__phpuser": {
                "username": "__phpuser",
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
        phpversions.router,
        prefix="/api/phpversions",
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

    admin_token = login("__phpadmin")
    user_token = login("__phpuser")
    return client, admin_token, user_token


def _h(token):
    headers = {"Authorization": f"Bearer {token}"}
    headers.update(_entry_headers())
    return headers


def test_access_control():
    """未登录 401 / 普通用户 403 / 管理员可访问 status 与 sites。"""
    client, admin_token, user_token = _build_app_and_login()
    sites_file = _build_sites_file()
    phpversions.SITES_FILE = sites_file

    for path in ("/api/phpversions/status", "/api/phpversions/sites", "/api/phpversions/list"):
        r = client.get(path)
        check(f"未登录 {path} 返回 401", r.status_code == 401, str(r.status_code))
        r = client.get(path, headers=_h(user_token))
        check(f"普通用户 {path} 返回 403", r.status_code == 403, str(r.status_code))
        r = client.get(path, headers=_h(admin_token))
        check(f"管理员 {path} 返回 200", r.status_code == 200, str(r.status_code))

    r = client.get("/api/phpversions/sites", headers=_h(admin_token)).json()
    check("sites 返回全部站点", len(r["sites"]) == 3, str(len(r["sites"])))


def test_status_shape():
    """status 返回 available / php_versions / reason 结构。"""
    client, admin_token, _ = _build_app_and_login()
    with mock.patch.object(phpversions, "platform", mock.Mock(system=lambda: "Windows")), \
         mock.patch.object(phpversions.shutil, "which", return_value=None):
        data = client.get("/api/phpversions/status", headers=_h(admin_token)).json()
    check("status 含 available", "available" in data, str(data))
    check("status 含 php_versions", "php_versions" in data, str(data))
    check("status 含 reason", "reason" in data, str(data))
    check("Windows 无 PHP -> available=False", data["available"] is False, str(data))


def test_set_php_flow():
    """set-php：合法版本写入 sites.json；非法版本 400；非 static/subsite 400。"""
    client, admin_token, _ = _build_app_and_login()
    sites_file = _build_sites_file()
    phpversions.SITES_FILE = sites_file

    # 1) static 站点绑定合法版本
    r = client.post("/api/phpversions/site/site-a/set-php", json={"version": "8.2"}, headers=_h(admin_token))
    check("static 绑定 8.2 返回 200", r.status_code == 200, f"status={r.status_code} {r.text[:100]}")
    check("返回站点含 php_version", r.json().get("php_version") == "8.2", str(r.json()))
    # 2) sites 列表反映绑定
    sites = client.get("/api/phpversions/sites", headers=_h(admin_token)).json()["sites"]
    sa = next(s for s in sites if s["id"] == "site-a")
    check("sites 反映 php_version=8.2", sa["php_version"] == "8.2", str(sa))
    # 3) subsite 绑定
    r = client.post("/api/phpversions/site/site-b/set-php", json={"version": "8.1"}, headers=_h(admin_token))
    check("subsite 绑定 8.1 返回 200", r.status_code == 200, f"status={r.status_code}")
    # 4) proxy 拒绝（不支持）
    r = client.post("/api/phpversions/site/site-c/set-php", json={"version": "8.2"}, headers=_h(admin_token))
    check("proxy 站点被拒 400", r.status_code == 400, f"status={r.status_code}")
    # 5) 非法版本拒绝
    r = client.post("/api/phpversions/site/site-a/set-php", json={"version": "8.2; rm -rf /"}, headers=_h(admin_token))
    check("非法版本被拒 400", r.status_code == 400, f"status={r.status_code}")
    # 6) 不存在的站点 404
    r = client.post("/api/phpversions/site/nope/set-php", json={"version": "8.2"}, headers=_h(admin_token))
    check("不存在站点返回 404", r.status_code == 404, f"status={r.status_code}")
    # 7) 清除绑定（空版本）
    r = client.post("/api/phpversions/site/site-a/set-php", json={"version": ""}, headers=_h(admin_token))
    check("清除绑定返回 200", r.status_code == 200 and r.json().get("php_version") == "", str(r.json()))
    # 8) 持久化到磁盘
    with open(sites_file, "r", encoding="utf-8") as f:
        on_disk = json.load(f)
    sa = next(s for s in on_disk if s["id"] == "site-a")
    check("磁盘已写入 php_version 为空串", sa.get("php_version") == "", str(sa))
    sb = next(s for s in on_disk if s["id"] == "site-b")
    check("磁盘已写入 site-b php_version=8.1", sb.get("php_version") == "8.1", str(sb))


def _cleanup():
    import shutil
    shutil.rmtree(_TMPDIR, ignore_errors=True)


if __name__ == "__main__":
    _cleanup()
    print("== 单元测试 ==")
    test_parse_version_unit()
    test_fpm_socket_unit()
    test_detect_linux_unit()
    test_detect_linux_no_php_unit()
    test_detect_windows_unit()
    test_detect_windows_no_php_unit()
    test_detect_platform_selects_windows_unit()
    test_detect_exception_degrades_unit()
    print("\n== 集成测试 ==")
    test_access_control()
    test_status_shape()
    test_set_php_flow()
    print(f"\n结果：PASS {PASS} 项，FAIL {FAIL} 项")
    sys.exit(1 if FAIL else 0)