# -*- coding: utf-8 -*-
"""
test_ssh_auth_fallback.py - 控制器缺 sshpass 时远程节点「密码认证」回退 paramiko 测试

背景：Windows 控制器无法安装 sshpass（Linux 工具），此前密码认证节点直接报
「控制器主机未安装 sshpass」。现已支持回退到纯 Python 的 paramiko 做密码认证。

放置于 backend 之外运行，避免触发 uvicorn --reload 重启后端。

用法：backend/.venv/Scripts/python.exe test_ssh_auth_fallback.py
"""
import os
import sys
import unittest
import warnings
from unittest import mock

BACKEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend")
sys.path.insert(0, BACKEND)

# 屏蔽 paramiko 导入时的 cryptography TripleDES 弃用警告噪音
warnings.filterwarnings("ignore", category=DeprecationWarning)

from app import node_manager as nm  # noqa: E402


def _mk_node(auth="password"):
    return {
        "id": "x", "type": "ssh",
        "host": "h.example.com", "port": 22, "user": "u",
        "auth": auth, "password": "secret",
        "key_path": "/home/u/.ssh/id_ed25519",
    }


class SshAuthFallbackTest(unittest.TestCase):
    def test_password_no_sshpass_falls_back_to_paramiko(self):
        """无 sshpass 且有 paramiko → _run_ssh 走 paramiko。"""
        node = _mk_node()
        with mock.patch.object(nm, "_sshpass_ok", return_value=False), \
             mock.patch.object(nm, "_paramiko_ok", return_value=True), \
             mock.patch.object(
                 nm, "_paramiko_run",
                 return_value=nm.subprocess.CompletedProcess([], 0, "ok", ""),
             ) as pk:
            r = nm._run_ssh(node, "echo ok", capture_output=True, text=True, timeout=10)
        pk.assert_called_once()
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "ok")

    def test_password_no_sshpass_no_paramiko_returns_127(self):
        """两者皆无 → 返回 exit 127 的可读错误，不抛异常。"""
        node = _mk_node()
        with mock.patch.object(nm, "_sshpass_ok", return_value=False), \
             mock.patch.object(nm, "_paramiko_ok", return_value=False):
            r = nm._run_ssh(node, "echo ok")
        self.assertEqual(r.returncode, 127)

    def test_key_auth_still_uses_system_ssh(self):
        """密钥认证仍走系统 ssh，不被 paramiko 抢占。"""
        node = _mk_node(auth="key")
        with mock.patch.object(nm, "_sshpass_ok", return_value=False), \
             mock.patch.object(nm, "_paramiko_ok", return_value=True), \
             mock.patch.object(
                 nm.subprocess, "run",
                 return_value=nm.subprocess.CompletedProcess([], 0, "ok", ""),
             ) as sr:
            r = nm._run_ssh(node, "echo ok", capture_output=True, text=True)
        sr.assert_called_once()
        self.assertEqual(r.returncode, 0)

    def test_connect_test_no_sshpass_uses_paramiko(self):
        """connect_test：无 sshpass 时走 paramiko 连通性测试。"""
        node = _mk_node()
        with mock.patch.object(nm, "_sshpass_ok", return_value=False), \
             mock.patch.object(nm, "_paramiko_ok", return_value=True), \
             mock.patch.object(
                 nm, "_paramiko_connect_test",
                 return_value={"ok": True, "message": "ok"},
             ) as pc:
            res = nm.connect_test(node)
        pc.assert_called_once()
        self.assertTrue(res["ok"])

    def test_connect_test_neither_reports_unavailable(self):
        """connect_test：两者皆无 → 明确提示不可认证。"""
        node = _mk_node()
        with mock.patch.object(nm, "_sshpass_ok", return_value=False), \
             mock.patch.object(nm, "_paramiko_ok", return_value=False):
            res = nm.connect_test(node)
        self.assertFalse(res["ok"])
        self.assertIn("paramiko", res["message"])

    def test_paramiko_ok_true_when_importable(self):
        """paramiko 已安装 → _paramiko_ok() 为 True。"""
        nm._paramiko_checked = False
        self.assertTrue(nm._paramiko_ok())


if __name__ == "__main__":
    unittest.main(verbosity=2)