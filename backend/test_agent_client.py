# -*- coding: utf-8 -*-
"""
test_agent_client.py - 主面板 Agent 客户端单元测试
覆盖签名算法一致性、HTTP-over-channel 解析器、节点 agent 配置提取。
需要子节点 agent_auth 的签名算法保持一致（HMAC-SHA256(key|ts|nonce)）。
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__)))

from app import agent_client  # noqa: E402


class SigAlgoTest(unittest.TestCase):
    def test_sig_matches_agent_auth(self):
        """主面板签名应与子节点 agent_auth._compute_sig 在"同一 secret"下一致。"""
        from app import agent_auth
        from app import agent_cfg
        import time

        # 两边用同一 secret 显式计算（agent_auth._compute_sig 从 agent_cfg.get_config() 读 secret）
        key, ts, nonce = "k", int(time.time()), "n1"
        secret = "shared-secret"
        # agent_client._sig(secret, key, ts, nonce) 显式传参
        client_sig = agent_client._sig(secret, key, ts, nonce)
        # 临时让 agent_cfg 返回该 secret，使 agent_auth 用同一 secret 计算
        with mock.patch.object(
            agent_cfg, "get_config", return_value={"enabled": True, "key": key, "secret": secret, "role": "admin"}
        ):
            auth_sig = agent_auth._compute_sig(key, ts, nonce)
        self.assertEqual(client_sig, auth_sig)


class HttpChannelParserTest(unittest.TestCase):
    def test_response_headers_and_body_split(self):
        """应正确拆分 HTTP/1.0 响应头与 body。"""
        chan = mock.MagicMock()
        chan.recv.side_effect = [
            b"HTTP/1.0 200 OK\r\nContent-Type: application/json\r\n\r\n",
            b'{"hello":"world"}',
            b"",
        ]
        result = agent_client._http_over_channel(chan, "GET", "/x", {"A": "b"})
        self.assertEqual(result["status"], 200)
        self.assertEqual(result["headers"]["content-type"], "application/json")
        self.assertEqual(result["body"], b'{"hello":"world"}')

    def test_status_parse(self):
        chan = mock.MagicMock()
        chan.recv.side_effect = [b"HTTP/1.0 404 Not Found\r\n\r\nbody", b""]
        result = agent_client._http_over_channel(chan, "GET", "/x", {})
        self.assertEqual(result["status"], 404)


class NodeAgentCfgTest(unittest.TestCase):
    def test_cfg_extraction(self):
        node = {"agent_port": 9000, "agent_key": "kk", "agent_secret": "ss"}
        cfg = agent_client._node_agent_cfg(node)
        self.assertEqual(cfg["port"], 9000)
        self.assertEqual(cfg["key"], "kk")
        self.assertEqual(cfg["secret"], "ss")

    def test_cfg_missing_fields(self):
        cfg = agent_client._node_agent_cfg({"agent_port": 8000})
        self.assertEqual(cfg["key"], "")


if __name__ == "__main__":
    unittest.main()