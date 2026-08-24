# -*- coding: utf-8 -*-
"""
站点可用性检测核心逻辑单元测试（不依赖运行中的后端服务）

覆盖：
  - URL 校验（http/https、超长拒绝）
  - 监控项 CRUD
  - 探测逻辑：HEAD 成功 / HEAD 失败回退 GET / 超时 / 状态码与预期不符
  - 状态机告警：ok→down 推送宕机、down→ok 推送恢复、持续状态不推送
  - 后台调度：到期才探测

用法：
  python test_uptime_unit.py
"""
import os
import sys
import tempfile
import shutil
import unittest
from unittest import mock
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.routers import uptime  # noqa: E402


class UptimeApiTest(unittest.TestCase):
    """通过 TestClient 验证监控项 CRUD 与 URL 校验。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        uptime.UPTIME_FILE = os.path.join(self._tmp, "uptime.json")
        app = FastAPI()
        app.include_router(uptime.router)
        self.client = TestClient(app)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _add(self, **kw):
        payload = {"name": "官网", "url": "https://example.com", "expect_status": 200,
                   "timeout_seconds": 10, "interval_seconds": 60, "enabled": True}
        payload.update(kw)
        return self.client.post("/items", json=payload)

    def test_crud_and_url_validation(self):
        r = self._add()
        self.assertEqual(r.status_code, 200)
        iid = r.json()["id"]
        # 非法 URL 拒绝
        self.assertEqual(self._add(url="ftp://x").status_code, 400)
        self.assertEqual(self._add(url="http://" + "a" * 2100).status_code, 400)
        # 更新
        r = self.client.put(f"/items/{iid}", json={"name": "官网2", "interval_seconds": 120})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["name"], "官网2")
        # 删除
        self.assertEqual(self.client.delete(f"/items/{iid}").status_code, 200)
        self.assertEqual(self.client.delete("/items/nope").status_code, 404)
        # 状态接口
        st = self.client.get("/status").json()
        self.assertIn("item_count", st)


class UptimeLogicTest(unittest.TestCase):
    """探测逻辑与状态机告警。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        uptime.UPTIME_FILE = os.path.join(self._tmp, "uptime.json")
        self.item = {
            "id": "up_1", "name": "site", "url": "https://example.com",
            "expect_status": 200, "timeout_seconds": 10, "interval_seconds": 60,
            "enabled": True, "last_status": None, "last_code": None,
            "last_latency_ms": None, "last_checked_at": "", "last_checked_ts": 0,
            "down_since": "", "history": [],
        }

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _probe(self, responses, call_side_effect=False):
        """构造 requests.request 的 mock 响应列表。"""
        if call_side_effect:
            return mock.patch("requests.request", side_effect=responses)
        it = iter(responses)
        return mock.patch("requests.request", side_effect=lambda *a, **k: next(it))

    def test_probe_head_success(self):
        with self._probe([SimpleNamespace(status_code=200)]):
            r = uptime._probe_and_alert(self.item)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["code"], 200)
        self.assertIn("latency_ms", r)

    def test_probe_head_fallback_get(self):
        """HEAD 抛异常（服务器不支持）时回退 GET。"""
        import requests

        with mock.patch("requests.request", side_effect=[
            requests.RequestException("head not allowed"), SimpleNamespace(status_code=200),
        ]):
            r = uptime._probe_and_alert(self.item)
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["code"], 200)

    def test_probe_timeout_down(self):
        import requests

        with mock.patch("requests.request", side_effect=[
            requests.exceptions.Timeout(), requests.exceptions.Timeout(),
        ]):
            r = uptime._probe_and_alert(self.item)
        self.assertEqual(r["status"], "down")
        self.assertEqual(r["error"], "连接超时")

    def test_expect_status_mismatch_down(self):
        with self._probe([SimpleNamespace(status_code=500)]):
            r = uptime._probe_and_alert(self.item)
        self.assertEqual(r["status"], "down")
        self.assertIn("500", r.get("error") or "")

    def test_state_machine_alerts(self):
        """ok→down 推送宕机；down→ok 推送恢复；连续状态不推送。"""
        from app.routers import notify

        # 先 ok
        with self._probe([SimpleNamespace(status_code=200)]), \
             mock.patch.object(notify, "push_all", return_value=(1, 0)) as p1:
            r1 = uptime._probe_and_alert(self.item)
        self.assertEqual(r1["status"], "ok")
        p1.assert_not_called()  # 首次探测 prev=None 不推送

        # 再 ok（持续正常）→ 不推送
        with self._probe([SimpleNamespace(status_code=200)]), \
             mock.patch.object(notify, "push_all", return_value=(1, 0)) as p2:
            r2 = uptime._probe_and_alert(self.item)
        p2.assert_not_called()

        # ok→down → 推送宕机
        with self._probe([SimpleNamespace(status_code=502)]), \
             mock.patch.object(notify, "push_all", return_value=(1, 0)) as p3:
            r3 = uptime._probe_and_alert(self.item)
        self.assertEqual(r3["status"], "down")
        p3.assert_called_once()
        self.assertIn("站点告警", p3.call_args[0][0])

        # down→ok → 推送恢复
        with self._probe([SimpleNamespace(status_code=200)]), \
             mock.patch.object(notify, "push_all", return_value=(1, 0)) as p4:
            r4 = uptime._probe_and_alert(self.item)
        self.assertEqual(r4["status"], "ok")
        p4.assert_called_once()
        self.assertIn("站点恢复", p4.call_args[0][0])

        # 历史环形：不超过 MAX_HISTORY
        self.assertLessEqual(len(self.item["history"]), uptime.MAX_HISTORY)

    def test_tick_only_probes_due(self):
        """后台调度：未到期的项不探测。"""
        uptime._save({"items": [self.item]})
        with mock.patch("app.routers.uptime._probe_and_alert") as probe:
            n = uptime._tick_once()
        # last_checked_ts=0 → 到期，应探测一次
        self.assertEqual(n, 1)
        probe.assert_called_once()

        # 刚探测过 → 未到期，不再探测
        with mock.patch("app.routers.uptime._probe_and_alert") as probe2:
            n2 = uptime._tick_once()
        self.assertEqual(n2, 0)
        probe2.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
