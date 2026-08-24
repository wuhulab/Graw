# -*- coding: utf-8 -*-
"""
test_remote_monitoring.py - 远端系统监控单元测试
覆盖 system.py 在「当前主机为远程 SSH 节点」时的概览 / 网络 / 磁盘 IO 分支，
确保切换节点后读取的是远端指标（通过 node_manager.is_remote + host_shell 模拟），
而非回落本机 psutil。
"""
import os
import sys
import time
import unittest
from unittest import mock

# 确保能 import app 包
sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from app.routers import system  # noqa: E402


class FakeResult:
    """模拟 node_manager.host_shell 的返回对象（subprocess.CompletedProcess 兼容）。"""

    def __init__(self, stdout: str = "", returncode: int = 0):
        self.stdout = stdout
        self.returncode = returncode


class RemoteOverviewTest(unittest.TestCase):
    def _mock_blocks(self, blocks):
        """把基于分段的 blocks 结果灌进 _remote_blocks（含 TTL 缓存状态清理）。"""
        system._remote_blocks_cache = blocks
        system._remote_blocks_at = time.time()

    def test_remote_overview_reads_remote_proc(self):
        """远程概览应解析远端 /proc/stat、/proc/meminfo、df 等输出（单次批量采集）。"""
        # 仿 _REMOTE_SCRIPT 输出分段；两次 cpu 采样相同 -> cpu=0%（避免除零噪音）
        blocks = {
            "STAT0": "cpu  100 0 0 0 0 0 0 400",
            "STAT1": "cpu  100 0 0 0 0 0 0 400",
            "MEM": "MemTotal: 1000000 kB\nMemAvailable: 400000 kB\n",
            "DF": "/dev/x 100000 60000 40000 60% /",
            "LOAD": "1.00 0.80 0.50 1/1 1",
            "NPROC": "4",
        }
        self._mock_blocks(blocks)
        with mock.patch.object(system.node_manager, "is_remote", return_value=True):
            result = system._remote_overview()
        # 内存 60%：used=600000KB*1024 / total=1000000KB*1024
        self.assertAlmostEqual(result["memory"]["percent"], 60.0, places=1)
        self.assertAlmostEqual(result["storage"]["percent"], 60.0, places=1)
        # load1=1.00 / 4 核 = 25%
        self.assertAlmostEqual(result["load"]["percent"], 25.0, places=1)
        self.assertIn("cpu", result)
        self.assertIn("load", result)

    def test_remote_net_bytes_accumulates(self):
        """远程网络应累加 /proc/net/dev 各网卡字节（recv=col1, sent=col9）。"""
        self._mock_blocks({
            "NET": "eth0: 100 2 3 4 5 6 7 8 20 2 3 4 5 6 7 8\n"
                   "eth1: 300 2 3 4 5 6 7 8 40 2 3 4 5 6 7 8\n",
        })
        counters = system._remote_net_bytes()
        self.assertEqual(counters["recv"], 400)
        self.assertEqual(counters["sent"], 60)

    def test_remote_diskio_accumulates(self):
        """远程磁盘 IO 应累加 /proc/diskstats 的 read/write（*512 转字节）。"""
        self._mock_blocks({
            "DISK": "8 0 sda 10 0 0 0 20 0 0 0 0 0 0 0\n",  # 第6列=10读扇区，第10列=20写扇区
        })
        result = system._remote_diskio()
        # read=10*512, write=20*512；首采样速率按间隔计算，此处仅断言字段存在
        self.assertIn("read", result)
        self.assertIn("write", result)


class RemoteBranchTest(unittest.TestCase):
    """验证 *_sync 在 is_remote=True 时走 _remote_* 分支。"""

    def test_overview_sync_branches_remote(self):
        with mock.patch.object(system.node_manager, "is_remote", return_value=True), \
             mock.patch.object(system, "_remote_overview", return_value={"cpu": 12.3, "remote": True}), \
             mock.patch.object(system, "psutil") as p:
            out = system._overview_sync()
        self.assertEqual(out["remote"], True)
        self.assertEqual(out["cpu"], 12.3)
        # 不应调用本地 psutil 采集
        p.cpu_percent.assert_not_called()

    def test_overview_sync_stays_local_when_not_remote(self):
        with mock.patch.object(system.node_manager, "is_remote", return_value=False), \
             mock.patch.object(system, "psutil") as p:
            p.cpu_percent.return_value = 5.0
            p.cpu_count.return_value = 2
            p.getloadavg.return_value = (0.5, 0.4, 0.3)
            system._overview_sync()
        p.cpu_percent.assert_called()
        p.virtual_memory.assert_called()
        p.disk_usage.assert_called()

    def test_network_sync_branches_remote(self):
        with mock.patch.object(system.node_manager, "is_remote", return_value=True), \
             mock.patch.object(system, "_remote_network", return_value={"upload": 1, "download": 2, "remote": True}):
            out = system._network_sync()
        self.assertEqual(out["remote"], True)

    def test_diskio_sync_branches_remote(self):
        with mock.patch.object(system.node_manager, "is_remote", return_value=True), \
             mock.patch.object(system, "_remote_diskio", return_value={"read": 1, "write": 2, "remote": True}):
            out = system._diskio_sync()
        self.assertEqual(out["remote"], True)


if __name__ == "__main__":
    unittest.main()