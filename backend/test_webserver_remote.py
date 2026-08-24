# -*- coding: utf-8 -*-
"""
test_webserver_remote.py - Web 引擎可用性在「容器化 Web 引擎」场景下的单元测试

覆盖 1Panel 等把 openresty/nginx 跑在 Docker 容器里（宿主机无对应二进制）时，
webserver.available() 能通过容器回退检测将其识别为可用。
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from app import webserver  # noqa: E402


def _fake_proc(rc=0, stdout="", stderr=""):
    """模拟 CompletedProcess（node_manager.host_shell 返回）。"""
    from subprocess import CompletedProcess

    return CompletedProcess(["sh", "-c", ""], rc, stdout=stdout, stderr=stderr)


class ContainerEngineDetectionTest(unittest.TestCase):
    def test_container_openresty_detected(self):
        """宿主机无 openresty 二进制，但容器内运行 openresty → available(openresty) 应 True。"""
        proc = _fake_proc(0, "1panel/openresty:1.31.1.1-2-4-noble\n")
        with mock.patch.object(webserver, "host_which", return_value=None), \
             mock.patch.object(webserver, "host_cmd", return_value=_fake_proc(127, "", "not found")), \
             mock.patch.object(webserver.node_manager, "host_shell", return_value=proc):
            self.assertTrue(webserver.available("openresty"))

    def test_container_nginx_detected(self):
        """容器内运行 nginx → available(nginx) True。"""
        proc = _fake_proc(0, "nginx:1.25\n")
        with mock.patch.object(webserver, "host_which", return_value=None), \
             mock.patch.object(webserver, "host_cmd", return_value=_fake_proc(127, "", "not found")), \
             mock.patch.object(webserver.node_manager, "host_shell", return_value=proc):
            self.assertTrue(webserver.available("nginx"))

    def test_no_container_no_binary(self):
        """宿主机与容器都没有 → available False。"""
        with mock.patch.object(webserver, "host_which", return_value=None), \
             mock.patch.object(webserver, "host_cmd", return_value=_fake_proc(127, "", "not found")), \
             mock.patch.object(webserver.node_manager, "host_shell", return_value=_fake_proc(0, "")):
            self.assertFalse(webserver.available("openresty"))
            self.assertFalse(webserver.available("nginx"))

    def test_binary_takes_priority(self):
        """宿主机已有 nginx 二进制时优先命中，不再走容器检测。"""
        with mock.patch.object(webserver, "host_which", return_value="/usr/bin/nginx"):
            self.assertTrue(webserver.available("nginx"))


if __name__ == "__main__":
    unittest.main()