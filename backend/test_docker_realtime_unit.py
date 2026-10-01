# -*- coding: utf-8 -*-
"""
test_docker_realtime_unit.py - Docker 实时推送与容器日志修复的单元测试

覆盖本轮改动（见 AGENTS.md 第 5 节与本文件对应模块）：
  1. 容器日志「显示为空」修复：
     docker/podman CLI 会把「容器 stderr」写到自身 stderr，而多数应用
     （nginx / php-fpm / gunicorn 等）的日志写在 stderr，此前只取 stdout，
     导致这些容器显示「(空)」。用例校验两个流被合并、真正为空才回「(空)」、
     非零退出码仍按错误抛出（不把报错吞成正常结果）。
  2. Docker 实时快照采集：_collect_docker_local_sync 在引擎不可用 / 可用两种
     情况下的结构化返回；_collect_docker_for_node 对「已配 Agent 的子节点」
     走 Agent 隧道取数据（与 HTTP 代理路径一致）。
  3. WebSocket 路由注册：/ws 必须挂在「无 Router 级依赖」的 ws_router 上
     （Router 级 ADMIN 依赖会让 ?token= 鉴权在握手阶段直接失败）。
"""
import os
import sys
import unittest
import unittest.mock as mock

from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from app.routers import docker_api  # noqa: E402


class FakeContainer:
    """模拟 docker SDK 的容器对象（只用到 logs）。"""

    def __init__(self, log_bytes=b""):
        self._log_bytes = log_bytes

    def logs(self, tail=200):
        return self._log_bytes


class FakeClient:
    """模拟 docker SDK client（只用到 containers.get）。"""

    def __init__(self, container):
        self.containers = mock.Mock()
        self.containers.get.return_value = container


class ContainerLogsCliTest(unittest.TestCase):
    """CLI 模式容器日志：stdout + stderr 必须合并（修复「日志显示空」）。"""

    def _call(self, rc, out, err):
        with mock.patch.object(docker_api, "get_backend", return_value=("cli", None)), \
             mock.patch.object(docker_api, "_find_podman", return_value=["docker"]), \
             mock.patch.object(docker_api, "_run", return_value=(rc, out, err)):
            return docker_api._container_logs_sync("abc123", 100)

    def test_logs_written_to_stderr_are_returned(self):
        """应用把日志写到 stderr（nginx/gunicorn 等）时不再返回「(空)」。"""
        logs = self._call(0, "", "2026/10/01 [error] upstream timed out\n")
        self.assertEqual(logs["logs"], "2026/10/01 [error] upstream timed out\n")

    def test_logs_from_both_streams_are_merged(self):
        """stdout 与 stderr 都有内容时两段都要返回（此前 stderr 被丢弃）。"""
        logs = self._call(0, "access line\n", "error line\n")
        self.assertEqual(logs["logs"], "access line\nerror line\n")

    def test_truly_empty_logs_return_placeholder(self):
        """两个流都为空（如容器刚创建从未启动）才回「(空)」。"""
        self.assertEqual(self._call(0, "", "")["logs"], "(空)")

    def test_nonzero_exit_still_raises(self):
        """非零退出码仍按错误抛出（错误信息取 stderr），不静默当作正常日志。"""
        with self.assertRaises(HTTPException) as ctx:
            self._call(1, "", "Error: No such container: abc123")
        self.assertEqual(ctx.exception.status_code, 500)
        self.assertIn("No such container", str(ctx.exception.detail))

    def test_blank_lines_only_counts_as_empty(self):
        """只有空白字符（换行/空格）也视为无日志，避免前端显示一片空白。"""
        self.assertEqual(self._call(0, "\n\n", "  ")["logs"], "(空)")


class ContainerLogsSdkTest(unittest.TestCase):
    """SDK 模式容器日志：空内容同样归一为「(空)」，与 CLI 分支语义一致。"""

    def test_sdk_empty_logs_return_placeholder(self):
        client = FakeClient(FakeContainer(b""))
        with mock.patch.object(docker_api, "get_backend", return_value=("sdk", client)):
            self.assertEqual(docker_api._container_logs_sync("abc123")["logs"], "(空)")

    def test_sdk_logs_decoded(self):
        client = FakeClient(FakeContainer("服务已启动\n".encode("utf-8")))
        with mock.patch.object(docker_api, "get_backend", return_value=("sdk", client)):
            self.assertEqual(docker_api._container_logs_sync("abc123")["logs"], "服务已启动\n")


class RealtimeSnapshotTest(unittest.TestCase):
    """实时推送使用的快照采集函数。"""

    def test_local_snapshot_engine_unavailable(self):
        """引擎不可用（未安装/未运行）时返回结构化降级结果，而不是抛异常。"""
        with mock.patch.object(docker_api, "_status_sync",
                               side_effect=HTTPException(status_code=503, detail="未检测到 Docker")):
            snap = docker_api._collect_docker_local_sync()
        self.assertFalse(snap["status"]["available"])
        self.assertIn("未检测到", snap["status"]["reason"])
        self.assertEqual(snap["containers"], [])
        self.assertIsInstance(snap["ts"], int)

    def test_local_snapshot_engine_available(self):
        """引擎可用时同时带上引擎状态与容器列表。"""
        with mock.patch.object(docker_api, "_status_sync", return_value={"available": True, "containers": 1}), \
             mock.patch.object(docker_api, "_containers_sync", return_value=[{"id": "abc", "name": "nginx"}]):
            snap = docker_api._collect_docker_local_sync()
        self.assertTrue(snap["status"]["available"])
        self.assertEqual(len(snap["containers"]), 1)
        self.assertEqual(snap["containers"][0]["name"], "nginx")

    def test_local_snapshot_container_list_failure_keeps_status(self):
        """容器列表采集失败不影响「引擎可用」结论（返回空列表 + 保留状态）。"""
        with mock.patch.object(docker_api, "_status_sync", return_value={"available": True}), \
             mock.patch.object(docker_api, "_containers_sync", side_effect=RuntimeError("boom")):
            snap = docker_api._collect_docker_local_sync()
        self.assertTrue(snap["status"]["available"])
        self.assertEqual(snap["containers"], [])

    def test_collect_for_agent_node_uses_tunnel(self):
        """已配 Agent 的 SSH 子节点：经 Agent 隧道取子节点面板自己的数据。"""
        node = {"id": "n1", "type": "ssh"}
        status_body = b'{"available": true, "containers": 2}'
        containers_body = b'[{"id": "c1", "name": "redis"}]'

        def fake_proxy(_node, _method, path, _headers, body=None):
            return {"status": 200, "body": status_body if path.endswith("/status") else containers_body}

        with mock.patch.object(docker_api.node_manager, "get_node", return_value=node), \
             mock.patch.object(docker_api.agent_client, "agent_ready", return_value=True), \
             mock.patch.object(docker_api.agent_client, "agent_proxy", side_effect=fake_proxy):
            snap = docker_api._collect_docker_for_node("n1")
        self.assertTrue(snap["status"]["available"])
        self.assertEqual(snap["containers"][0]["name"], "redis")

    def test_collect_for_plain_remote_node_falls_back_to_cli(self):
        """未配 Agent 的节点：退回「在目标节点上下文里直接调 CLI」的采集路径。"""
        with mock.patch.object(docker_api.node_manager, "get_node", return_value={"id": "n2", "type": "ssh"}), \
             mock.patch.object(docker_api.agent_client, "agent_ready", return_value=False), \
             mock.patch.object(docker_api.node_manager, "run_on_node",
                               side_effect=lambda nid, fn: {"status": {"available": False}, "containers": [], "ts": 1}) as run:
            snap = docker_api._collect_docker_for_node("n2")
        run.assert_called_once()
        self.assertEqual(snap["ts"], 1)


class WebSocketRouteTest(unittest.TestCase):
    """WS 路由必须注册在「无 Router 级依赖」的 ws_router 上。"""

    def test_ws_route_registered_on_ws_router(self):
        paths = [getattr(r, "path", "") for r in docker_api.ws_router.routes]
        self.assertIn("/ws", paths)

    def test_ws_route_not_on_admin_http_router(self):
        # HTTP 业务路由挂了 Router 级 ADMIN 依赖，WS 不能注册在这里（否则 ?token= 无法鉴权）
        http_paths = [getattr(r, "path", "") for r in docker_api.router.routes]
        self.assertNotIn("/ws", http_paths)


if __name__ == "__main__":
    unittest.main()
