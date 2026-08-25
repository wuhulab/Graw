# -*- coding: utf-8 -*-
"""
test_frp_configpath_regression.py - frp configPath 任意文件写入 回归测试

对应漏洞（第十一轮之后新增修复）：
  backend/app/routers/frp.py 的 configPath 字段此前仅做长度 + 控制字符校验，
  未做路径约束，导致已认证管理员可把 frp 配置文件指向任意可写路径，使
  _write_toml 把攻击者可控 TOML 写到系统任意位置（CWE-73 / CWE-22）。

修复：新增 _validate_config_path，要求非空 configPath 必须为绝对路径、
  经 realpath 解析后位于 FRP_CONFIG_DIR（/etc/frp）目录内，阻断 ".." 穿越
  与绝对路径逃逸；默认路径不受影响。

本测试覆盖：
  1. 单元测试 _validate_config_path：默认 / 合法子路径放行，越界 / 穿越 /
     相对路径 / 设备命名空间前缀拒绝。
  2. 集成测试：真实 HTTP 端点 PUT /api/frp/config 对越界 configPath 返回 400，
     对合法 configPath（/etc/frp 内）返回 200。集成测试 stub 掉 _write_toml /
     _save_store，不产生任何真实文件或 data/frp.json 改动。
"""

import os
import sys

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

BACKEND = os.path.normpath(os.path.join(os.path.dirname(__file__)))
if BACKEND not in sys.path:
    sys.path.insert(0, BACKEND)

from app.routers import frp  # noqa: E402
import app.main as app_main  # noqa: E402
from app.main import app  # noqa: E402
from app.auth import get_current_user  # noqa: E402


# ---------------------------------------------------------------------------
# 1. 单元测试：_validate_config_path
# ---------------------------------------------------------------------------
def test_validate_config_path_empty_allowed():
    # 空字符串 -> 使用默认路径，放行
    assert frp._validate_config_path("", "服务端配置路径") == ""
    assert frp._validate_config_path("   ", "服务端配置路径") == ""


def test_validate_config_path_default_and_subpath_allowed():
    # 默认路径与 /etc/frp 内子路径应放行
    assert frp._validate_config_path("/etc/frp/frps.toml", "p") == "/etc/frp/frps.toml"
    assert frp._validate_config_path("/etc/frp/sub/x.toml", "p") == "/etc/frp/sub/x.toml"


def test_validate_config_path_outside_base_rejected():
    with pytest.raises(HTTPException) as ei:
        frp._validate_config_path("/tmp/evil.toml", "服务端配置路径")
    assert ei.value.status_code == 400


def test_validate_config_path_traversal_rejected():
    # ".." 穿越逃逸 /etc/frp 应被 realpath 解析后拦截
    with pytest.raises(HTTPException) as ei:
        frp._validate_config_path("/etc/frp/../../tmp/x.toml", "服务端配置路径")
    assert ei.value.status_code == 400


def test_validate_config_path_relative_rejected():
    # 非绝对路径应拒绝
    with pytest.raises(HTTPException) as ei:
        frp._validate_config_path("../x.toml", "服务端配置路径")
    assert ei.value.status_code == 400


def test_validate_config_path_device_namespace_rejected():
    # Windows 设备命名空间前缀应拒绝
    with pytest.raises(HTTPException) as ei:
        frp._validate_config_path("\\\\?\\C:\\x.toml", "服务端配置路径")
    assert ei.value.status_code == 400


# ---------------------------------------------------------------------------
# 2. 集成测试：真实 HTTP 端点（stub 掉写盘，零副作用）
# ---------------------------------------------------------------------------
@pytest.fixture
def client_and_mocks():
    # 模拟已认证管理员（覆盖鉴权依赖链），并关闭 agent 代理中间件判定，
    # 使请求按本地部署语义落到端点；stub 写盘函数避免真实副作用。
    async def fake_user():
        return {"username": "poc-admin", "role": "admin",
                "must_change_password": False, "token_version": 0,
                "otp_enabled": False}

    orig_override = app.dependency_overrides.get(get_current_user)
    app.dependency_overrides[get_current_user] = fake_user

    orig_should_proxy = app_main._should_agent_proxy
    app_main._should_agent_proxy = lambda path: False

    orig_write = frp._write_toml
    orig_save = frp._save_store
    frp._write_toml = lambda data: ""
    frp._save_store = lambda data: None

    client = TestClient(app)
    try:
        yield client
    finally:
        if orig_override is None:
            app.dependency_overrides.pop(get_current_user, None)
        else:
            app.dependency_overrides[get_current_user] = orig_override
        app_main._should_agent_proxy = orig_should_proxy
        frp._write_toml = orig_write
        frp._save_store = orig_save


def _payload(config_path):
    return {
        "mode": "server",
        "serverBin": "",
        "clientBin": "",
        "server": {
            "configPath": config_path,
            "bindAddr": "0.0.0.0",
            "bindPort": 7000,
            "token": "T",
            "dashboardAddr": "127.0.0.1",
            "dashboardPort": 0,
            "dashboardUser": "",
            "dashboardPwd": "",
            "logLevel": "info",
        },
        "client": {
            "serverAddr": "", "serverPort": 7000, "token": "",
            "configPath": "", "loginFailExit": True, "logLevel": "info",
        },
    }


def test_endpoint_rejects_outside_base(client_and_mocks):
    c = client_and_mocks
    r = c.put("/api/frp/config", json=_payload("/tmp/evil.toml"),
              headers={"Authorization": "Bearer x"})
    assert r.status_code == 400, r.text
    assert "etc/frp" in r.text or "目录内" in r.text


def test_endpoint_rejects_traversal(client_and_mocks):
    c = client_and_mocks
    r = c.put("/api/frp/config", json=_payload("/etc/frp/../../tmp/x.toml"),
              headers={"Authorization": "Bearer x"})
    assert r.status_code == 400, r.text


def test_endpoint_allows_valid_subpath(client_and_mocks):
    c = client_and_mocks
    r = c.put("/api/frp/config", json=_payload("/etc/frp/frps.toml"),
              headers={"Authorization": "Bearer x"})
    assert r.status_code == 200, r.text


if __name__ == "__main__":
    import subprocess
    sys.exit(subprocess.call([sys.executable, "-m", "pytest", __file__, "-v"]))
