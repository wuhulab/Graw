# -*- coding: utf-8 -*-
"""
test_agent_cfg_unit.py - 「作为子节点」Agent 收取模式配置的单元测试

背景：
  agent_cfg.reveal_secret() 曾在持锁（_lock）状态下重入 _load()/_save()，
  而 _lock 原先为 threading.Lock（非重入），同线程二次 acquire 会永久死锁；
  一旦触发，agent 配置的读/改请求全部被占死，表现为「保存后卡顿、报保存失败」。
  改为 RLock 后应可重入，本测试用带超时保护的子线程验证不会死锁。

用法：
  cd backend && pytest test_agent_cfg_unit.py -v
"""
import threading

from app import agent_cfg


def test_reveal_secret_does_not_deadlock(monkeypatch, tmp_path):
    """回归：set_config -> reveal_secret 应在限定时间内返回且不卡死。"""
    # 把持久化文件改到临时目录，避免污染真实 backend/data/agent.json
    monkeypatch.setattr(agent_cfg, "AGENT_CFG_FILE", str(tmp_path / "agent.json"))
    # 清空模块级缓存，防止跨用例残留状态
    monkeypatch.setattr(agent_cfg, "_cache", None)

    # 步骤1：写入启用的 agent 配置（持锁路径）
    t1 = threading.Thread(
        target=lambda: agent_cfg.set_config(
            enabled=True, key="test-key", secret="s-very-secret", role="admin"
        )
    )
    t1.start()
    t1.join(timeout=5)
    assert not t1.is_alive(), "set_config 死锁或超时"

    # 步骤2：reveal_secret 在持锁上下文内调用 _load/_save，必须在时限内完成
    holder = {}

    def do_reveal():
        holder["v"] = agent_cfg.reveal_secret()

    t2 = threading.Thread(target=do_reveal)
    t2.start()

    # 死锁时 join 永不返回；此处用 join(timeout) 探测
    t2.join(timeout=5)
    assert not t2.is_alive(), "reveal_secret 死锁或超时"
    assert holder["v"]["secret"] == "s-very-secret"

    # 展示后已标记，且缓存被重置后二次 reveal 不应再返回明文
    agent_cfg.reload()
    second = agent_cfg.reveal_secret()
    assert second["secret"] == ""


def test_get_config_blocking_thread_does_not_freeze(monkeypatch, tmp_path):
    """回归：enable 流程结束后，常规读（get_config/public_status）不再被卡住。"""
    monkeypatch.setattr(agent_cfg, "AGENT_CFG_FILE", str(tmp_path / "agent.json"))
    monkeypatch.setattr(agent_cfg, "_cache", None)

    agent_cfg.set_config(enabled=True, key="k", secret="s")
    done = []

    def reader():
        agent_cfg.get_config()
        done.append(True)

    t = threading.Thread(target=reader)
    t.start()
    t.join(timeout=5)
    assert not t.is_alive(), "get_config 死锁或超时"
    assert done == [True]