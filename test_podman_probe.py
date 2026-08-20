# -*- coding: utf-8 -*-
"""
test_podman_probe.py - 面板 Docker 引擎（Windows WSL）探测回退逻辑测试

背景：Windows 上 podman/docker 跑在 WSL 里，强制用 root 会让部分发行版启动失败
（getpwnam(root) failed / systemd user session for root 无法启动），导致面板报
「podman 命令失败」。本测试验证 docker_api._find_podman 的多候选回退：
  root 优先 → 默认用户兜底，并用 `info` 确认引擎真正可用。

放置于 backend 之外运行，避免触发 uvicorn --reload 重启后端。

用法：backend/.venv/Scripts/python.exe test_podman_probe.py
"""
import os
import sys
import unittest
from unittest import mock

BACKEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend")
sys.path.insert(0, BACKEND)

from app.routers import docker_api  # noqa: E402


def _reset_probe():
    """清空探测缓存，便于多次独立测试。"""
    docker_api._podman_cmd = None
    docker_api._podman_fail_until = 0.0


# 构造 _run 的按命令分发 mock：cmd[1] 为 '-u' 表示 root；否则默认用户
def _make_run(plan):
    """
    plan: dict mapping (is_root, is_info, key) -> (rc, out, err)
      is_root: bool 是否 root 用户候选
      is_info: bool 是否 info 探测命令
      key:     'podman' / 'docker'
    未命中 → 返回 (1, "", "")（视为该命令不可用）
    """
    def fake_run(cmd, timeout=30):
        # 命令形态：[... , <引擎名>, <子命令>]，倒数第 2 位是引擎名
        sub = cmd[-2]                      # 'podman' / 'docker'
        is_root = "-u" in cmd and "root" in cmd
        is_info = cmd[-1] == "info"
        key = sub
        default = (1, "", "")
        return plan.get((is_root, is_info, key), default)

    return fake_run


class PodmanProbeWindowsTest(unittest.TestCase):
    def test_root_fails_fallback_to_default_user(self):
        """root 报 getpwnam 致命错误 → 自动回退到默认用户 wsl -- podman。"""
        _reset_probe()
        plan = {
            # root podman --version: rc0 但 stderr 含 getpwnam（致命）→ 跳过
            (True, False, "podman"): (0, "podman version 5.4.2\n",
                                      "<3>WSL getpwnam(root) failed\nFailed to start the systemd user session for 'root'"),
            # 默认用户 podman --version: rc0、无致命错误
            (False, False, "podman"): (0, "podman version 5.4.2\n", ""),
            # 默认用户 podman info: rc0 → 引擎可用 → 采用该候选
            (False, True, "podman"): (0, "host:\n  security:\n", ""),
        }
        with mock.patch.object(docker_api.shutil, "which", return_value="/usr/bin/wsl"), \
             mock.patch.object(docker_api, "_run", side_effect=_make_run(plan)):
            found = docker_api._find_podman()
        self.assertEqual(found, ["wsl", "--", "podman"])

    def test_root_version_ok_but_info_fails_fallback(self):
        """root --version 正常但 info 起不来 → 回退默认用户。"""
        _reset_probe()
        plan = {
            (True, False, "podman"): (0, "podman version 5.4.2\n", ""),   # no fatal on --version
            (True, True, "podman"): (1, "", "error: engine not started"), # info 失败
            (False, False, "podman"): (0, "podman version 5.4.2\n", ""),
            (False, True, "podman"): (0, "host:\n  security:\n", ""),
        }
        with mock.patch.object(docker_api.shutil, "which", return_value="/usr/bin/wsl"), \
             mock.patch.object(docker_api, "_run", side_effect=_make_run(plan)):
            found = docker_api._find_podman()
        self.assertEqual(found, ["wsl", "--", "podman"])

    def test_all_fail_returns_none(self):
        """全部候选不可用 → 返回 None（面板报不可用）。"""
        _reset_probe()
        # 所有命令都返回失败
        with mock.patch.object(docker_api.shutil, "which", return_value="/usr/bin/wsl"), \
             mock.patch.object(docker_api, "_run", return_value=(1, "", "cannot connect")):
            found = docker_api._find_podman()
        self.assertIsNone(found)

    def test_root_works_uses_root(self):
        """root 下引擎正常时仍优先使用 root（预期行为不变）。"""
        _reset_probe()
        plan = {
            (True, False, "podman"): (0, "podman version 5.4.2\n", ""),
            (True, True, "podman"): (0, "host:\n  security:\n", ""),
        }
        with mock.patch.object(docker_api.shutil, "which", return_value="/usr/bin/wsl"), \
             mock.patch.object(docker_api, "_run", side_effect=_make_run(plan)):
            found = docker_api._find_podman()
        self.assertEqual(found, ["wsl", "-u", "root", "--", "podman"])


if __name__ == "__main__":
    unittest.main(verbosity=2)