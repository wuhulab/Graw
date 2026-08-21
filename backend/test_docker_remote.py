# -*- coding: utf-8 -*-
"""
test_docker_remote.py - Docker 多机（节点）远端执行单元测试
覆盖 docker_api 在「当前主机为 SSH 远程节点」时的关键行为：
  - _run 远端分支：命令经 node_manager.host_cmd 在远端执行
  - _find_podman 远端探测：探测远端 docker/podman，缓存按节点隔离
  - get_backend：远端节点不尝试本地 docker SDK
  - 引擎缓存切换节点自动重探
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from app.routers import docker_api  # noqa: E402


class FakeProc:
    """模拟 node_manager.host_cmd / subprocess.run 的返回对象。"""

    def __init__(self, returncode=0, stdout=b"", stderr=b""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class RunRemoteBranchTest(unittest.TestCase):
    def test_run_remote_uses_node_manager(self):
        """远端节点下 _run 应走 node_manager.host_cmd，且解码为 UTF-8。"""
        with mock.patch.object(docker_api.node_manager, "is_remote", return_value=True), \
             mock.patch.object(docker_api.node_manager, "host_cmd",
                               return_value=FakeProc(0, "容器名".encode("utf-8"), b"")) as hc:
            rc, out, err = docker_api._run(["docker", "ps"], timeout=15)
        self.assertEqual(rc, 0)
        self.assertEqual(out, "容器名")
        hc.assert_called_once()
        args = hc.call_args[0][0]
        self.assertEqual(args, ["docker", "ps"])

    def test_run_local_uses_subprocess(self):
        """本机节点下 _run 应走 subprocess.run（行为不变）。"""
        with mock.patch.object(docker_api.node_manager, "is_remote", return_value=False), \
             mock.patch.object(docker_api, "subprocess") as sp:
            sp.run.return_value = FakeProc(0, b"local-out", b"")
            docker_api._run(["docker", "ps"], timeout=15)
        sp.run.assert_called_once()


class FindPodmanRemoteTest(unittest.TestCase):
    def _reset_cache(self):
        docker_api._podman_cmd = None
        docker_api._podman_fail_until = 0.0
        docker_api._podman_cmd_node = "local"

    def test_remote_detects_docker(self):
        """远端探测：podman 不可用、docker 可用时返回 ["docker"]（经 _run 走远端）。"""
        self._reset_cache()

        def fake_run(cmd, timeout=20):
            if cmd[0] == "podman":
                return 1, "", "podman not found"
            if cmd[0] == "docker":
                if cmd[1] == "--version":
                    return 0, "Docker version 27.0.0", ""
                if cmd[1] == "info":
                    return 0, "Server Version: 27.0.0", ""
            return 1, "", "no"

        with mock.patch.object(docker_api.node_manager, "is_remote", return_value=True), \
             mock.patch.object(docker_api.node_manager, "current_node_id", return_value="node_b"), \
             mock.patch.object(docker_api, "_run", side_effect=fake_run):
            cli = docker_api._find_podman()
        self.assertEqual(cli, ["docker"])
        self.assertEqual(docker_api._podman_cmd_node, "node_b")

    def test_remote_cache_invalidated_on_node_switch(self):
        """A 节点探测到引擎后切到 B 节点，缓存应归零并重新探测（不串场）。"""
        self._reset_cache()
        # 先让 A 节点探到 docker
        with mock.patch.object(docker_api.node_manager, "is_remote", return_value=True), \
             mock.patch.object(docker_api.node_manager, "current_node_id", return_value="node_a"), \
             mock.patch.object(docker_api, "_run",
                               side_effect=(
                                   lambda cmd, timeout=20: FakeProc(0, f"{cmd[0]} version" if False else f"Docker version 27.0.0" if cmd[0] == "docker" else "", b"").returncode if False else
                                   (0, "Docker version 27.0.0", "") if cmd[0] == "docker" and cmd[-1] == "--version" else
                                   (0, "Server Version: 27", "") if cmd[0] == "docker" and cmd[-1] == "info" else (1, "", ""))):
            cli_a = docker_api._find_podman()
        self.assertEqual(cli_a, ["docker"])

        # 切到 B 节点：缓存应被判定为「早前的节点」而重新探测
        calls = {"n": 0}

        def fake_run2(cmd, timeout=20):
            calls["n"] += 1
            if cmd[0] == "docker":
                if "--version" in cmd:
                    return 0, "Docker version 27", ""
                if "info" in cmd:
                    return 0, "Server", ""
            return 1, "", ""

        with mock.patch.object(docker_api.node_manager, "is_remote", return_value=True), \
             mock.patch.object(docker_api.node_manager, "current_node_id", return_value="node_b"), \
             mock.patch.object(docker_api, "_run", side_effect=fake_run2):
            cli_b = docker_api._find_podman()
        self.assertEqual(cli_b, ["docker"])
        # 切换后确实发生了新的探测（至少 device --version + info）
        self.assertGreaterEqual(calls["n"], 2)

    def test_remote_no_cli_returns_none(self):
        """远端没有 docker/podman 时返回 None。"""
        self._reset_cache()
        with mock.patch.object(docker_api.node_manager, "is_remote", return_value=True), \
             mock.patch.object(docker_api.node_manager, "current_node_id", return_value="node_x"), \
             mock.patch.object(docker_api, "_run", return_value=(1, "", "not found")):
            cli = docker_api._find_podman()
        self.assertIsNone(cli)


class GetBackendRemoteTest(unittest.TestCase):
    def test_remote_get_backend_refuses_local_sdk(self):
        """远端节点下 get_backend 不得回退到本地 docker SDK。"""
        docker_api._podman_cmd = None
        docker_api._podman_cmd_node = "local"
        with mock.patch.object(docker_api.node_manager, "is_remote", return_value=True), \
             mock.patch.object(docker_api.node_manager, "current_node_id", return_value="node_r"), \
             mock.patch.object(docker_api, "_try_docker_sdk", return_value=object()) as sdk, \
             mock.patch.object(docker_api, "_find_podman", return_value=None):
            with self.assertRaises(Exception):  # HTTPException(503)
                docker_api.get_backend()
        # 绝不应尝试本地 SDK
        sdk.assert_not_called()


class PodmanJsonParseTest(unittest.TestCase):
    """_podman_json 对 docker/podman 不同 JSON 输出形态的归一化解析。"""

    def setUp(self):
        self._find = mock.patch.object(docker_api, "_find_podman", return_value=["docker"])
        self._find.start()

    def tearDown(self):
        self._find.stop()

    def test_docker_single_container(self):
        """docker ps 单容器输出为单行 JSON 对象 → 应解析为单元素 list，而非报 str.get。"""
        single = '{"Id":"abcd","Names":["openresty"],"Status":"Up 1 hour","Image":"1panel/openresty:1.31","Ports":"","Created":"x"}\n'
        with mock.patch.object(docker_api, "_run", return_value=(0, single, "")):
            arr = docker_api._podman_json(["ps", "-a", "--format", "json"])
        self.assertIsInstance(arr, list)
        self.assertEqual(len(arr), 1)
        self.assertEqual(arr[0]["Names"], ["openresty"])
        self.assertTrue(arr[0].get("Status", "").startswith("Up"))

    def test_docker_multiple_containers(self):
        """docker ps 多容器输出为多行 JSON 对象 → 逐行解析为 list。"""
        multi = (
            '{"Id":"aaa","Names":["c1"],"Status":"Up","Image":"nginx:1","Ports":"","Created":"x"}\n'
            '{"Id":"bbb","Names":["c2"],"Status":"Exited","Image":"redis:7","Ports":"","Created":"y"}\n'
        )
        with mock.patch.object(docker_api, "_run", return_value=(0, multi, "")):
            arr = docker_api._podman_json(["ps", "-a", "--format", "json"])
        self.assertEqual(len(arr), 2)

    def test_podman_array(self):
        """podman ps 输出单个 JSON 数组 → 直接作为 list 使用。"""
        arr_out = '[{"Id":"aaa","Names":["c1"],"Status":"Up","Image":"nginx:1","Ports":"","Created":"x"}]\n'
        with mock.patch.object(docker_api, "_run", return_value=(0, arr_out, "")):
            arr = docker_api._podman_json(["ps", "-a", "--format", "json"])
        self.assertEqual(len(arr), 1)

    def test_empty_output(self):
        """空输出 → 空列表。"""
        with mock.patch.object(docker_api, "_run", return_value=(0, "", "")):
            self.assertEqual(docker_api._podman_json(["ps", "-a"]), [])


if __name__ == "__main__":
    unittest.main()