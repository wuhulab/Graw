# -*- coding: utf-8 -*-
"""
节点资源聚合视图（/api/nodes/overview + 分组/标签元数据）单元测试（不依赖运行中的后端服务）。

 覆盖：
  - 分组 / 标签清洗与校验（长度、数量、控制字符、去重）；
  - 节点元数据更新（POST /api/nodes/{id}/meta）：本机 local 节点同样可设置分组与标签；
  - 远端采集脚本输出解析（===BLOCK=== 分段 / /proc/stat 差分）；
  - 聚合接口返回结构：本机节点 online 且 CPU/内存/磁盘/负载指标齐全。

 用法：
  python test_node_overview_unit.py
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock as mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.node_manager as node_manager  # noqa: E402
from app.routers import nodes as nodes_api  # noqa: E402


class TempStoreMixin:
    """把 nodes.json 存储重定向到临时目录，避免污染真实 data/。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        self._old_nodes_file = node_manager.NODES_FILE
        self._old_data_dir = node_manager.DATA_DIR
        node_manager.NODES_FILE = os.path.join(self._tmp, "nodes.json")
        node_manager.DATA_DIR = self._tmp
        node_manager._store_cache = None

    def tearDown(self):
        node_manager.NODES_FILE = self._old_nodes_file
        node_manager.DATA_DIR = self._old_data_dir
        node_manager._store_cache = None
        shutil.rmtree(self._tmp, ignore_errors=True)


class TestCleanHelpers(unittest.TestCase):
    """分组 / 标签清洗：node_manager.clean_node_group / clean_node_tags。"""

    def test_group_normal(self):
        self.assertEqual(node_manager.clean_node_group("  生产环境 "), "生产环境")
        self.assertEqual(node_manager.clean_node_group(None), "")
        self.assertEqual(node_manager.clean_node_group(""), "")

    def test_group_too_long(self):
        with self.assertRaises(ValueError):
            node_manager.clean_node_group("a" * (node_manager.NODE_GROUP_MAX + 1))

    def test_group_control_char(self):
        with self.assertRaises(ValueError):
            node_manager.clean_node_group("abc\x1b[31m")

    def test_tags_dedupe_and_strip(self):
        self.assertEqual(
            node_manager.clean_node_tags([" web ", "prod", "web", ""]),
            ["web", "prod"],
        )

    def test_tags_not_list(self):
        with self.assertRaises(ValueError):
            node_manager.clean_node_tags("web,prod")

    def test_tags_too_long_item(self):
        with self.assertRaises(ValueError):
            node_manager.clean_node_tags(["a" * (node_manager.NODE_TAG_MAX + 1)])

    def test_tags_count_capped(self):
        tags = [f"t{i}" for i in range(node_manager.NODE_TAGS_MAX + 5)]
        self.assertEqual(len(node_manager.clean_node_tags(tags)), node_manager.NODE_TAGS_MAX)


class TestNodeMeta(TempStoreMixin, unittest.TestCase):
    """节点元数据更新：本机节点也可设置分组与标签。"""

    def setUp(self):
        super().setUp()
        app = FastAPI()
        app.include_router(nodes_api.router, prefix="/nodes")
        self.client = TestClient(app)

    def test_update_local_node_meta(self):
        r = self.client.post("/nodes/local/meta", json={"group": "生产环境", "tags": ["web", "prod"]})
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["group"], "生产环境")
        self.assertEqual(data["tags"], ["web", "prod"])

    def test_meta_invalid_tags_type(self):
        # 非法类型由 Pydantic 在入口拦截（422），不会进入处理器
        r = self.client.post("/nodes/local/meta", json={"group": "", "tags": "not-a-list"})
        self.assertEqual(r.status_code, 422)

    def test_meta_node_not_found(self):
        r = self.client.post("/nodes/nope/meta", json={"group": "", "tags": []})
        self.assertEqual(r.status_code, 400)

    def test_ssh_node_meta_roundtrip(self):
        """SSH 节点经 upsert 保存的分组/标签应出现在列表里。"""
        created = node_manager.upsert_ssh_node(
            {"name": "n1", "host": "192.168.1.10", "port": 22, "user": "root",
             "auth": "password", "password": "x", "group": "边缘", "tags": ["edge"]}
        )
        pub = next(n for n in node_manager.list_nodes() if n["id"] == created["id"])
        self.assertEqual(pub["group"], "边缘")
        self.assertEqual(pub["tags"], ["edge"])

    def test_edit_without_meta_preserves_values(self):
        """编辑节点时不带 group/tags 字段应保留原值（旧调用方兼容）。"""
        created = node_manager.upsert_ssh_node(
            {"name": "n1", "host": "192.168.1.10", "port": 22, "user": "root",
             "auth": "password", "password": "x", "group": "边缘", "tags": ["edge"]}
        )
        # 模拟旧版设置表单：payload 不含 group/tags 键
        payload = {k: v for k, v in created.items()}
        payload.update({"name": "n1", "host": "192.168.1.10", "port": 22, "user": "root",
                        "auth": "password", "password": ""})
        payload.pop("group", None)
        payload.pop("tags", None)
        node_manager.upsert_ssh_node(payload)
        pub = next(n for n in node_manager.list_nodes() if n["id"] == created["id"])
        self.assertEqual(pub["group"], "边缘")
        self.assertEqual(pub["tags"], ["edge"])

    def test_local_node_in_list_has_meta_fields(self):
        local = next(n for n in node_manager.list_nodes() if n["id"] == "local")
        self.assertIn("group", local)
        self.assertIn("tags", local)


class TestRemoteParsing(unittest.TestCase):
    """远端采集脚本的输出解析。"""

    def test_parse_stat_line(self):
        total, idle = nodes_api._parse_stat_line("cpu  100 0 100 300 0 0 0 0")
        self.assertEqual(total, 500)
        self.assertEqual(idle, 300)
        # 非法输入不炸，返回 0
        self.assertEqual(nodes_api._parse_stat_line("cpu"), (0, 0))

    def test_parse_blocks(self):
        raw = "===BLOCKSTAT0===\nfoo\n===BLOCKMEM===\nbar\nbaz"
        blocks = nodes_api._remote_overview_blocks(raw)
        self.assertEqual(blocks["STAT0"], "foo")
        self.assertEqual(blocks["MEM"], "bar\nbaz")

    def test_script_markers_all_parseable(self):
        """脚本里声明的每个 ===BLOCK<key>=== 标记都必须能被分段解析器识别。

        回归背景：_OVERVIEW_REMOTE_SCRIPT 曾把标记写成 ===STAT0===（丢了 BLOCK
        前缀），_remote_overview_blocks 一个块都解析不到，子节点全部显示 0。
        """
        markers = re.findall(r"===BLOCK([A-Z0-9]+)===", nodes_api._OVERVIEW_REMOTE_SCRIPT)
        self.assertTrue(markers, "脚本里没有可解析的块标记")
        raw = "\n".join(f"===BLOCK{m}===\nv_{m}" for m in markers)
        blocks = nodes_api._remote_overview_blocks(raw)
        self.assertEqual(set(blocks.keys()), set(markers))

    def test_remote_overview_end_to_end(self):
        """用真实脚本形态的输出走一遍 _remote_overview：各指标都应解析出值。"""
        samples = {
            "STAT0": "cpu  1000 0 500 2000 0 0 100 0 0 0",
            "STAT1": "cpu  1100 0 600 2200 0 0 100 0 0 0",
            "MEM": "MemTotal:        4008984 kB\nMemAvailable:    1161128 kB",
            "DF": "/dev/sda1 50000000 10000000 38000000 21% /",
            "LOAD": "0.50 0.40 0.30 1/500 12345",
            "NPROC": "4",
            "BTIME": "btime 1700000000",
            "HOST": "web-01",
        }
        # 只认脚本里声明过的标记，脚本加块时这里会 KeyError 提醒补样例
        markers = re.findall(r"===BLOCK([A-Z0-9]+)===", nodes_api._OVERVIEW_REMOTE_SCRIPT)
        raw = "\n".join(f"===BLOCK{m}===\n{samples[m]}" for m in markers)
        with mock.patch.object(
            nodes_api.node_manager,
            "host_shell",
            return_value=subprocess.CompletedProcess([], 0, raw, ""),
        ):
            m = nodes_api._remote_overview("node_x")
        self.assertEqual(m["cpu"], 50.0)
        self.assertEqual(m["hostname"], "web-01")
        self.assertEqual(m["memory"]["total"], 4008984 * 1024)
        self.assertGreater(m["memory"]["percent"], 0)
        self.assertEqual(m["storage"]["percent"], 20.0)
        self.assertEqual(m["storage"]["total"], 50000000 * 1024)
        self.assertEqual(m["load"]["load1"], 0.5)
        self.assertGreater(m["uptime_seconds"], 0)

    def test_remote_overview_nonzero_exit(self):
        """远端命令非零退出应报错（调用方据此标记 offline）。"""
        with mock.patch.object(
            nodes_api.node_manager,
            "host_shell",
            return_value=subprocess.CompletedProcess([], 255, "", "Permission denied"),
        ):
            with self.assertRaises(RuntimeError):
                nodes_api._remote_overview("node_x")


class TestOverviewEndpoint(TempStoreMixin, unittest.TestCase):
    """聚合接口返回结构（仅有本机节点，走 psutil 采集路径）。"""

    def setUp(self):
        super().setUp()
        app = FastAPI()
        app.include_router(nodes_api.router, prefix="/nodes")
        self.client = TestClient(app)

    def test_overview_local_only(self):
        r = self.client.get("/nodes/overview")
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertIn("generated_at", data)
        self.assertEqual(len(data["nodes"]), 1)
        node = data["nodes"][0]
        self.assertEqual(node["id"], "local")
        self.assertTrue(node["online"])
        for key in ("cpu", "memory", "storage", "load", "uptime_seconds"):
            self.assertIn(key, node)
        self.assertIn("percent", node["memory"])
        self.assertIn("percent", node["storage"])
        self.assertIn("load1", node["load"])
        # 元数据字段始终存在（可缺省为空）
        self.assertIn("group", node)
        self.assertIn("tags", node)

    def test_overview_remote_node_offline(self):
        """SSH 节点采集失败时应标记 offline 且不中断整批。"""
        node_manager.upsert_ssh_node(
            {"name": "dead", "host": "127.0.0.1", "port": 22, "user": "root",
             "auth": "password", "password": "x"}
        )
        with mock.patch("app.node_manager.run_on_node", side_effect=RuntimeError("connection refused")):
            r = self.client.get("/nodes/overview")
        self.assertEqual(r.status_code, 200)
        dead = next(n for n in r.json()["nodes"] if n["name"] == "dead")
        self.assertFalse(dead["online"])
        self.assertIn("connection refused", dead["error"])
        # 本机节点不受影响
        local = next(n for n in r.json()["nodes"] if n["id"] == "local")
        self.assertTrue(local["online"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
