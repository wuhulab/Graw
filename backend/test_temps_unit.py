# -*- coding: utf-8 -*-
"""
温度监控单元测试（不依赖运行中的后端服务）

覆盖：
  - 温度换算与合理性过滤（milli_to_celsius）
  - CPU 传感器判定（is_cpu_sensor：驱动名 / label 特征）
  - sysfs 扫描：hwmon 优先、thermal 回退、同名消歧（前缀 + 序号）、排序截断
  - cpu_temp 取样（仅 CPU 类；无传感器返回 None）
  - metrics_store：温度字段落盘 / 缺失行不参与聚合平均
  - notify：温度告警文案单位、_read_metrics 温度字段
  - system._remote_temps：远端 HWMON/THERMAL 块解析（含异常值过滤）

用法：
  python test_temps_unit.py
"""
import os
import sys
import json
import time
import tempfile
import shutil
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import temps  # noqa: E402


class MilliConvertTest(unittest.TestCase):
    """温度换算与异常值过滤。"""

    def test_normal_convert(self):
        self.assertEqual(temps.milli_to_celsius("45000"), 45.0)
        self.assertEqual(temps.milli_to_celsius(" 38500 "), 38.5)
        self.assertEqual(temps.milli_to_celsius("-5000"), -5.0)

    def test_invalid_or_out_of_range(self):
        # 空值 / 非数字 / 超出 [-50, 200] 物理范围 → None
        self.assertIsNone(temps.milli_to_celsius(""))
        self.assertIsNone(temps.milli_to_celsius("abc"))
        self.assertIsNone(temps.milli_to_celsius(None))
        self.assertIsNone(temps.milli_to_celsius("999000"))   # 999°C
        self.assertIsNone(temps.milli_to_celsius("-99000"))   # -99°C


class CpuClassifyTest(unittest.TestCase):
    """CPU 传感器判定：驱动名与 label 特征。"""

    def test_driver_names(self):
        self.assertTrue(temps.is_cpu_sensor("coretemp", ""))
        self.assertTrue(temps.is_cpu_sensor("k10temp", "Tctl"))
        self.assertTrue(temps.is_cpu_sensor("cpu_thermal", ""))  # ARM

    def test_label_features(self):
        self.assertTrue(temps.is_cpu_sensor("acpitz", "CPU Temp"))
        self.assertTrue(temps.is_cpu_sensor("somehw", "Package id 0"))

    def test_non_cpu(self):
        self.assertFalse(temps.is_cpu_sensor("nvme", "Composite"))
        self.assertFalse(temps.is_cpu_sensor("acpitz", ""))


class ScanTest(unittest.TestCase):
    """用临时目录伪造 sysfs，验证扫描 / 回退 / 消歧 / 排序。"""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="graw_temps_test_")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def _write(self, rel, text):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)

    def _mk_hwmon(self, hw, name, temps_map):
        """temps_map: {索引: (label 或 '', 毫度原始值)}。"""
        self._write(f"sys/class/hwmon/{hw}/name", name)
        for idx, (label, milli) in temps_map.items():
            self._write(f"sys/class/hwmon/{hw}/temp{idx}_input", milli)
            if label:
                self._write(f"sys/class/hwmon/{hw}/temp{idx}_label", label)

    def _collect(self):
        # 把 host_path 映射到伪造根目录（等价容器 /host 语义）
        with mock.patch.object(temps, "host_path", lambda p: os.path.join(self.root, p.lstrip("/"))):
            return temps.collect_temps()

    def test_hwmon_preferred_over_thermal(self):
        self._mk_hwmon("hwmon0", "coretemp", {"1": ("Package id 0", "45000"), "2": ("Core 0", "44000")})
        self._mk_hwmon("hwmon1", "nvme", {"1": ("Composite", "38000")})
        # thermal 也存在，但 hwmon 有数据时应被忽略（避免同温度重复）
        self._write("sys/class/thermal/thermal_zone0/type", "x86_pkg_temp")
        self._write("sys/class/thermal/thermal_zone0/temp", "99000")
        sensors = self._collect()
        names = [s["name"] for s in sensors]
        self.assertNotIn("x86_pkg_temp", names)
        # CPU 优先排序：第一个必须是 CPU 类
        self.assertEqual(sensors[0]["kind"], "cpu")
        # 数值换算正确
        values = {s["name"]: s["value"] for s in sensors}
        self.assertEqual(values["Package id 0"], 45.0)
        self.assertEqual(values["Composite"], 38.0)

    def test_thermal_fallback(self):
        self._write("sys/class/thermal/thermal_zone0/type", "x86_pkg_temp")
        self._write("sys/class/thermal/thermal_zone0/temp", "55000")
        sensors = self._collect()
        self.assertEqual(len(sensors), 1)
        self.assertEqual(sensors[0]["name"], "x86_pkg_temp")
        self.assertEqual(sensors[0]["kind"], "cpu")   # 类型名命中 cpu 特征
        self.assertEqual(sensors[0]["value"], 55.0)

    def test_duplicate_names_disambiguated(self):
        # 两块同型号 NVMe，label 都是 Composite → 前缀驱动名 + 序号
        self._mk_hwmon("hwmon0", "nvme", {"1": ("Composite", "38000")})
        self._mk_hwmon("hwmon1", "nvme", {"1": ("Composite", "40000")})
        sensors = self._collect()
        names = sorted(s["name"] for s in sensors)
        self.assertEqual(names, ["nvme Composite", "nvme Composite #2"])

    def test_invalid_readings_dropped(self):
        self._mk_hwmon("hwmon0", "coretemp", {"1": ("Package id 0", "abc")})
        self.assertEqual(self._collect(), [])

    def test_no_sensors_returns_empty(self):
        # 伪造目录下没有任何传感器（等价 Windows / 虚拟机）
        os.makedirs(os.path.join(self.root, "sys/class"), exist_ok=True)
        self.assertEqual(self._collect(), [])


class CpuTempPickTest(unittest.TestCase):
    """cpu_temp 取值规则。"""

    def test_picks_cpu_kind_only(self):
        sensors = [
            {"name": "Composite", "value": 38.0, "kind": "other"},
            {"name": "Package id 0", "value": 45.0, "kind": "cpu"},
        ]
        self.assertEqual(temps.cpu_temp(sensors), 45.0)

    def test_none_when_no_cpu(self):
        # 仅有主板温度时不误标为 CPU 温度（宁可缺失）
        self.assertIsNone(temps.cpu_temp([{"name": "acpitz", "value": 40.0, "kind": "other"}]))
        self.assertIsNone(temps.cpu_temp([]))
        self.assertIsNone(temps.cpu_temp(None))


class MetricsStoreTempTest(unittest.TestCase):
    """历史采样：温度字段落盘与聚合（缺失行不按 0 参与平均）。"""

    def setUp(self):
        from app import metrics_store
        self.ms = metrics_store
        self._tmp = tempfile.mkdtemp(prefix="graw_metrics_temp_")
        self._old_dir = metrics_store.METRICS_DIR
        metrics_store.METRICS_DIR = self._tmp
        self.ms._pending.clear()

        def _restore():
            self.ms.METRICS_DIR = self._old_dir
            self.ms._pending.clear()
            shutil.rmtree(self._tmp, ignore_errors=True)

        self.addCleanup(_restore)

    def _sample(self, cpu, temps_list):
        return {
            "overview": {
                "cpu": cpu,
                "memory": {"percent": 40},
                "storage": {"percent": 50},
                "load": {"load1": 1.0},
                "temps": temps_list,
            },
            "network": {"upload": 1, "download": 2},
            "diskio": {"read": 3, "write": 4},
        }

    def test_record_and_aggregate(self):
        ms = self.ms
        now = time.time()
        cpu_sensor = [{"name": "Package id 0", "value": 45.0, "kind": "cpu"}]
        # 第一条有 CPU 温度；第二条无传感器（模拟无温度主机）
        ms.record_sample(self._sample(10, cpu_sensor))
        ms.record_sample(self._sample(20, []))
        ms.flush()

        rows = ms._iter_rows(now - 100, now + 10)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0].get("temp"), 45.0)
        self.assertNotIn("temp", rows[1])   # 无传感器不写字段

        # 聚合：temp 只按有值行平均（45.0），不因缺值行被拉低
        agg = ms.history(now - 100, now + 10, bucket=3600)
        self.assertEqual(len(agg["points"]), 1)
        self.assertEqual(agg["points"][0]["temp"], 45.0)
        # 常规字段行为不变：CPU 均值为两条平均
        self.assertEqual(agg["points"][0]["cpu"], 15.0)
        ms.clear()


class NotifyTempTest(unittest.TestCase):
    """通知中心：温度告警文案单位与指标读取。"""

    def test_alert_message_unit(self):
        from app.routers import notify
        self.assertIn("temp", notify.METRICS)
        msg = notify._alert_message("temp", 87.5, 85)
        self.assertIn("CPU 温度", msg)
        self.assertIn("87.5°C", msg)
        self.assertIn("阈值 85°C", msg)
        # 常规指标仍为百分比
        self.assertIn("90.0%", notify._alert_message("cpu", 90.0, 85))

    def test_read_metrics_with_temp(self):
        from app.routers import notify
        with mock.patch.object(
            notify, "collect_temps",
            return_value=[{"name": "Package id 0", "value": 61.0, "kind": "cpu"}],
        ):
            m = notify._read_metrics()
        self.assertEqual(m["temp"], 61.0)

    def test_read_metrics_without_temp(self):
        from app.routers import notify
        with mock.patch.object(notify, "collect_temps", return_value=[]):
            m = notify._read_metrics()
        self.assertIsNone(m["temp"])   # 无传感器 → None → _check_once 自动跳过

    def test_check_once_skips_missing_temp(self):
        """无温度数据时温度规则不触发、也不报错（require 静默跳过）。"""
        from app.routers import notify
        with mock.patch.object(notify, "_load", return_value={
            "enabled": True,
            "interval_seconds": 60,
            "cooldown_seconds": 300,
            "channels": [],
            "rules": [{"id": "r1", "metric": "temp", "threshold": 50, "enabled": True}],
        }), mock.patch.object(notify, "_read_metrics", return_value={"cpu": 1, "temp": None}):
            self.assertEqual(notify._check_once(), 0)


class RemoteTempsParseTest(unittest.TestCase):
    """远端温度块解析（HWMON / THERMAL）。"""

    def test_parse_hwmon_block(self):
        from app.routers import system
        blocks = {
            "HWMON": "coretemp|Package id 0|45000\nnvme|Composite|bad",
            "THERMAL": "x86_pkg_temp|99000",
        }
        with mock.patch.object(system, "_remote_blocks", return_value=blocks):
            sensors = system._remote_temps()
        # 非法读数被丢弃；hwmon 有数据时 thermal 不参与
        self.assertEqual(len(sensors), 1)
        self.assertEqual(sensors[0]["name"], "Package id 0")
        self.assertEqual(sensors[0]["value"], 45.0)
        self.assertEqual(sensors[0]["kind"], "cpu")

    def test_parse_thermal_fallback(self):
        from app.routers import system
        blocks = {"HWMON": "", "THERMAL": "x86_pkg_temp|55000\nacpitz|40000"}
        with mock.patch.object(system, "_remote_blocks", return_value=blocks):
            sensors = system._remote_temps()
        names = [s["name"] for s in sensors]
        self.assertEqual(names[0], "x86_pkg_temp")   # CPU 优先排序
        self.assertEqual(sensors[0]["kind"], "cpu")
        self.assertEqual(sensors[1]["kind"], "other")

    def test_empty_blocks(self):
        from app.routers import system
        with mock.patch.object(system, "_remote_blocks", return_value={}):
            self.assertEqual(system._remote_temps(), [])


if __name__ == "__main__":
    unittest.main(verbosity=2)