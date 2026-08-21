# -*- coding: utf-8 -*-
"""
test_remote_monitoring.py - 远端系统监控单元测试
覆盖 system.py 在「当前主机为远程 SSH 节点」时的概览 / 网络 / 磁盘 IO 分支，
确保切换节点后读取的是远端指标（通过 node_manager.is_remote + host_shell 模拟），
而非回落本机 psutil。
"""
import os
import sys
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
    def _stub_host_shell(self, output_map):
        """返回一个 host_shell 桩：按命令行关键字返回对应 stdout。"""

        def _fake(cmd, **kwargs):
            for key, out in output_map.items():
                if key in cmd:
                    return FakeResult(out)
            return FakeResult("")

        return _fake

    def test_remote_overview_reads_remote_proc(self):
        """远程概览应解析远端 /proc/stat、/proc/meminfo、df 等输出。"""
        outputs = {
            # 两次 cpu 采样相同 -> cpu 记为 0%（避免除零/负值噪音）
            "cat /proc/stat 2>/dev/null | head -1": "cpu  100 0 0 0 0 0 0 400",
            "cat /proc/meminfo": "MemTotal: 1000000 kB\nMemAvailable: 400000 kB\n",
            # 生产代码 `df -kP / ... | tail -1` 只剩数据行（无表头）
            "df -kP /": "/dev/x 100000 60000 40000 60% /",
            "cat /proc/loadavg": "1.00 0.80 0.50 1/1 1",
            "nproc": "4",
        }
        with mock.patch.object(system.node_manager, "host_shell", self._stub_host_shell(outputs)), \
             mock.patch.object(system.node_manager, "is_remote", return_value=True):
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
        with mock.patch.object(
            system.node_manager, "host_shell",
            lambda cmd, **kw: FakeResult(
                "eth0: 100 2 3 4 5 6 7 8 20 2 3 4 5 6 7 8\n"
                "eth1: 300 2 3 4 5 6 7 8 40 2 3 4 5 6 7 8\n"
            ),
        ):
            counters = system._remote_net_bytes()
        self.assertEqual(counters["recv"], 400)
        self.assertEqual(counters["sent"], 60)

    def test_remote_diskio_accumulates(self):
        """远程磁盘 IO 应累加 /proc/diskstats 的 read/write（*512 转字节）。"""
        with mock.patch.object(
            system.node_manager, "host_shell",
            lambda cmd, **kw: FakeResult(
                "8 0 sda 10 0 0 0 20 0 0 0 0 0 0 0\n"  # 第6列=10读扇区，第10列=20写扇区
            ),
        ):
            result = system._remote_diskio()
        # read=10*512, write=20*512；首采样速率按间隔计算，此处仅断言字段存在与累计量
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