# -*- coding: utf-8 -*-
"""
通知中心核心逻辑单元测试（不依赖运行中的后端服务）

覆盖：
  - 渠道字段校验（缺字段 / 非法 URL / SMTP 端口）
  - 渠道脱敏（password / bot_token / key 不回显）
  - 渠道 / 规则 CRUD、配置更新
  - 阈值检查（mock 指标超阈值 → 告警记录 + 渠道推送）
  - 冷却去重（同规则冷却期内不重复告警）
  - 告警记录环形截断

用法：
  python test_notify_unit.py
"""
import os
import sys
import tempfile
import shutil
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.routers import notify  # noqa: E402


class NotifyApiTest(unittest.TestCase):
    """通过 TestClient 验证渠道 / 规则 CRUD 与配置。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        notify.NOTIFY_FILE = os.path.join(self._tmp, "notify.json")
        notify.LOG_FILE = os.path.join(self._tmp, "notify_logs.json")
        notify._last_alert.clear()
        app = FastAPI()
        app.include_router(notify.router)
        self.client = TestClient(app)

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _add_webhook(self, **kw):
        payload = {"name": "ops-webhook", "type": "webhook", "config": {"url": "https://hook.example.com/x"}, "enabled": True}
        payload.update(kw)
        return self.client.post("/channels", json=payload)

    def test_channel_crud_and_mask(self):
        r = self._add_webhook()
        self.assertEqual(r.status_code, 200)
        cid = r.json()["id"]
        # 脱敏：不含 url 之外的敏感字段（webhook 无敏感项）
        self.assertEqual(r.json()["type"], "webhook")

        # 非法 URL 拒绝
        self.assertEqual(self._add_webhook(config={"url": "ftp://x"}).status_code, 400)
        # 缺 URL 拒绝
        self.assertEqual(self._add_webhook(config={}).status_code, 400)

        # 更新
        r = self.client.put(f"/channels/{cid}", json={
            "name": "ops-webhook-2", "type": "webhook",
            "config": {"url": "https://hook.example.com/y"}, "enabled": True,
        })
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["name"], "ops-webhook-2")

        # 删除
        self.assertEqual(self.client.delete(f"/channels/{cid}").status_code, 200)
        self.assertEqual(self.client.delete("/channels/nope").status_code, 404)

    def test_channel_telegram_secret_mask(self):
        r = self.client.post("/channels", json={
            "name": "tg", "type": "telegram",
            "config": {"bot_token": "123:ABC", "chat_id": "42"}, "enabled": True,
        })
        self.assertEqual(r.status_code, 200)
        body = r.json()
        # token 绝不明文返回，只有 has_bot_token 标记
        self.assertNotIn("bot_token", json_path(body, "config"))
        self.assertTrue(body["config"]["has_bot_token"])
        # 缺 chat_id 拒绝
        r = self.client.post("/channels", json={
            "name": "tg-bad", "type": "telegram", "config": {"bot_token": "123:ABC"}, "enabled": True,
        })
        self.assertEqual(r.status_code, 400)

    def test_channel_smtp_validation(self):
        ok_payload = {"name": "mail", "type": "smtp", "config": {
            "host": "smtp.example.com", "port": 465, "ssl": True,
            "username": "u", "password": "p", "from": "a@x.com", "to": "b@x.com",
        }, "enabled": True}
        self.assertEqual(self.client.post("/channels", json=ok_payload).status_code, 200)
        # 非法端口
        bad = dict(ok_payload)
        bad["config"] = dict(ok_payload["config"], port=70000)
        self.assertEqual(self.client.post("/channels", json=bad).status_code, 400)
        # 缺收件人
        bad2 = dict(ok_payload)
        bad2["config"] = dict(ok_payload["config"], to="")
        self.assertEqual(self.client.post("/channels", json=bad2).status_code, 400)

    def test_rule_crud(self):
        r = self.client.post("/rules", json={"metric": "cpu", "threshold": 90, "enabled": True})
        self.assertEqual(r.status_code, 200)
        rid = r.json()["id"]
        self.assertEqual(self.client.post("/rules", json={"metric": "bogus", "threshold": 1}).status_code, 400)
        r = self.client.put(f"/rules/{rid}", json={"metric": "mem", "threshold": 85, "enabled": True})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["threshold"], 85)
        self.assertEqual(self.client.delete(f"/rules/{rid}").status_code, 200)

    def test_config_update(self):
        r = self.client.put("/config", json={"enabled": True, "interval_seconds": 30, "cooldown_seconds": 60})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertTrue(body["enabled"])
        self.assertEqual(body["cooldown_seconds"], 60)


class NotifyCheckTest(unittest.TestCase):
    """阈值检查与告警推送逻辑。"""

    def setUp(self):
        self._tmp = tempfile.mkdtemp()
        notify.NOTIFY_FILE = os.path.join(self._tmp, "notify.json")
        notify.LOG_FILE = os.path.join(self._tmp, "notify_logs.json")
        notify._last_alert.clear()
        # 开启 + 冷却 0（方便连续触发）与冷却 300（测去重）
        notify._save({
            "enabled": True, "interval_seconds": 60, "cooldown_seconds": 300,
            "channels": [{"id": "ch1", "name": "webhook", "type": "webhook", "enabled": True,
                          "config": {"url": "https://hook.example.com/x"}}],
            "rules": [{"id": "rule_cpu", "metric": "cpu", "threshold": 90, "enabled": True}],
        })

    def tearDown(self):
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_check_triggers_alert(self):
        from unittest import mock

        with mock.patch("app.routers.notify._read_metrics", return_value={"cpu": 95, "mem": 10, "disk": 10, "load": 5}), \
             mock.patch("app.routers.notify._send_to_channel", return_value=None) as send:
            n = notify._check_once()
        self.assertEqual(n, 1)
        send.assert_called_once()
        # 告警记录已写入
        logs = notify._load_logs()
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["metric"], "cpu")
        self.assertEqual(logs[0]["value"], 95)
        self.assertEqual(logs[0]["sent_channels"], 1)

    def test_check_cooldown_dedup(self):
        """冷却期内同一规则不重复告警。"""
        from unittest import mock

        with mock.patch("app.routers.notify._read_metrics", return_value={"cpu": 95, "mem": 10, "disk": 10, "load": 5}), \
             mock.patch("app.routers.notify._send_to_channel", return_value=None):
            n1 = notify._check_once()
            n2 = notify._check_once()  # 冷却 300s 内 → 不触发
        self.assertEqual(n1, 1)
        self.assertEqual(n2, 0)
        self.assertEqual(len(notify._load_logs()), 1)

    def test_check_below_threshold_no_alert(self):
        from unittest import mock

        with mock.patch("app.routers.notify._read_metrics", return_value={"cpu": 30, "mem": 10, "disk": 10, "load": 5}), \
             mock.patch("app.routers.notify._send_to_channel", return_value=None) as send:
            n = notify._check_once()
        self.assertEqual(n, 0)
        send.assert_not_called()

    def test_check_disabled_skips(self):
        from unittest import mock

        notify._save(dict(notify._load(), enabled=False))
        with mock.patch("app.routers.notify._read_metrics", return_value={"cpu": 99, "mem": 99, "disk": 99, "load": 99}), \
             mock.patch("app.routers.notify._send_to_channel", return_value=None) as send:
            n = notify._check_once()
        self.assertEqual(n, 0)
        send.assert_not_called()

    def test_channel_failure_still_logs(self):
        """渠道发送失败：记录 failed_channels，不影响其它渠道与记录写入。"""
        from unittest import mock

        with mock.patch("app.routers.notify._read_metrics", return_value={"cpu": 95, "mem": 10, "disk": 10, "load": 5}), \
             mock.patch("app.routers.notify._send_to_channel", side_effect=RuntimeError("boom")):
            n = notify._check_once()
        self.assertEqual(n, 1)
        logs = notify._load_logs()
        self.assertEqual(logs[0]["failed_channels"], 1)
        self.assertEqual(logs[0]["sent_channels"], 0)

    def test_log_ring_truncation(self):
        """告警记录环形截断：逐条追加超过 MAX_LOG_ENTRIES 只保留最新。"""
        for i in range(notify.MAX_LOG_ENTRIES + 50):
            notify._append_log({"id": f"e{i}", "time": f"2026-08-20T00:00:{i:02d}", "metric": "cpu",
                                "value": 95, "threshold": 90, "message": "x",
                                "sent_channels": 0, "failed_channels": 0})
        logs = notify._load_logs()
        self.assertEqual(len(logs), notify.MAX_LOG_ENTRIES)
        # 保留的是最新 200 条（id 从 50 开始）
        self.assertEqual(logs[0]["id"], "e50")
        self.assertEqual(logs[-1]["id"], "e249")


def json_path(obj, key):
    """取 dict 的指定键（测试辅助，直接属性访问）。"""
    return obj.get(key) if isinstance(obj, dict) else None


if __name__ == "__main__":
    unittest.main(verbosity=2)
