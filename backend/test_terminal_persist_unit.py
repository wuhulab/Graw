# -*- coding: utf-8 -*-
"""test_terminal_persist_unit.py - 终端「保留持久化终端」会话管理单元测试

背景：
    Graw 终端新增「保留持久化终端」：勾选后后端把 shell 进程常驻（会话生命周期
    与 WebSocket 连接解耦），刷新面板 / 重开窗口接回同一会话并回放最近输出，
    用于长时间任务不因刷新而中断。核心逻辑在 app/tty_persist.py，本测试用可编程
    的假通道（FakeIO）覆盖：

      - 回放快照：新接入的客户端能立刻看到历史输出；
      - 多客户端广播：多窗口共享同一常驻进程，都能收到后续输出；
      - 断开只摘客户端、不杀进程（本功能的核心语义）；
      - 显式销毁 / 面板退出才关闭通道，并通知所有接入端收尾；
      - 进程退出后注册表自动回收，下次连接重建会话；
      - 缓冲超限裁剪且向后对齐换行（避免回放出现半行乱码）；
      - 会话键按节点隔离（不同节点绝不串到同一 shell）。

用法：
    cd backend && pytest test_terminal_persist_unit.py -v
"""
import asyncio
import threading
import time

from app import tty_persist


class FakeIO:
    """可编程的假通道：按预设块吐出输出，块耗尽后按 hold 决定是否「常驻」。

    hold=True  模拟常驻 shell：块读完后阻塞等待，直到会话被销毁才返回 EOF；
    hold=False 模拟进程立即退出：块读完后立刻返回 EOF（b""）。
    """

    def __init__(self, chunks, hold=True):
        self._chunks = list(chunks)
        self._hold = hold
        self._lock = threading.Lock()
        self._release = threading.Event()
        self.written = []      # 收到的用户输入
        self.resized = []      # 收到的窗口尺寸调整
        self.closed = False    # 通道是否被关闭（销毁会话）

    def read(self):
        """阻塞读语义：有数据返回数据，否则按 hold 决定阻塞或 EOF。"""
        with self._lock:
            if self._chunks:
                return self._chunks.pop(0)
        if self._hold:
            # 常驻：一直等待，直到会话被销毁（close 会放开阻塞）
            self._release.wait(timeout=5)
        return b""

    def write(self, data):
        self.written.append(data)

    def resize(self, rows, cols):
        self.resized.append((rows, cols))

    def close(self):
        self.closed = True
        self._release.set()   # 让阻塞中的读取线程退出


async def _wait_for(predicate, timeout=2.0):
    """轮询等待条件成立（读取线程到事件循环的投递是异步的）。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.01)
    return predicate()


def test_replay_snapshot_broadcast_and_detach():
    """回放快照 + 多客户端广播 + 断开只摘客户端（进程继续常驻）。"""
    async def scenario():
        io = FakeIO([b"$ echo hi\r\n", b"hi\r\n"], hold=True)
        manager = tty_persist.SessionManager()
        key = manager.key("local")
        session = manager.create(key, "本机 · 持久终端", io)

        # 等读取线程把两块输出投递到事件循环（回放缓冲就绪）
        await _wait_for(lambda: session.buffer_size == len(b"$ echo hi\r\nhi\r\n"))

        # 两个客户端接入：都能拿到完整历史回放（多窗口共享同一终端）
        qid1, queue1, replay1 = session.attach()
        qid2, queue2, replay2 = session.attach()
        assert replay1 == b"$ echo hi\r\nhi\r\n"
        assert replay2 == replay1
        assert session.client_count == 2

        # 后续输出广播给所有接入端
        session._on_output(b"tail\r\n")
        assert queue1.get_nowait() == b"tail\r\n"
        assert queue2.get_nowait() == b"tail\r\n"

        # 摘除一个客户端：不影响另一个客户端，也不影响常驻进程
        session.detach(qid1)
        assert session.client_count == 1
        session._on_output(b"more\r\n")
        assert queue2.get_nowait() == b"more\r\n"
        assert io.closed is False, "断开客户端不得杀死常驻进程"
        assert session.alive() is True

        # 显式销毁才关闭通道
        assert manager.destroy(key) is True
        assert io.closed is True

    asyncio.run(scenario())


def test_destroy_notifies_all_clients_and_is_idempotent():
    """销毁会话：关闭通道、通知所有接入端收尾；重复销毁返回 False。"""
    async def scenario():
        io = FakeIO([], hold=True)
        manager = tty_persist.SessionManager()
        key = manager.key("local")
        session = manager.create(key, "t", io)
        _, queue1, _ = session.attach()
        _, queue2, _ = session.attach()

        assert manager.destroy(key) is True
        await _wait_for(lambda: not session.alive())
        assert io.closed is True
        # 结束哨兵（b""）投递给所有接入端，各客户端连接自行收尾
        assert queue1.get_nowait() == tty_persist.PersistentSession.EOT
        assert queue2.get_nowait() == tty_persist.PersistentSession.EOT
        # 幂等：再次销毁无事发生
        assert manager.destroy(key) is False

    asyncio.run(scenario())


def test_session_eof_recycles_registry():
    """进程自行退出（EOF）后：会话标记结束、注册表自动回收，下次连接重建。"""
    async def scenario():
        io = FakeIO([b"bye\r\n"], hold=False)
        manager = tty_persist.SessionManager()
        key = manager.key("local")
        session = manager.create(key, "t", io)

        await _wait_for(lambda: not session.alive())
        assert bytes(session._buffer) == b"bye\r\n"
        # get() 会顺手回收已结束的会话（返回 None 让调用方重建）
        assert manager.get(key) is None
        assert manager.list() == []

    asyncio.run(scenario())


def test_key_isolation_and_destroy_node():
    """会话键按节点隔离；按节点批量销毁不会误伤其他节点。"""
    async def scenario():
        manager = tty_persist.SessionManager()
        io_a, io_b = FakeIO([], hold=True), FakeIO([], hold=True)
        manager.create(manager.key("node-a"), "a", io_a)
        manager.create(manager.key("node-b"), "b", io_b)

        assert manager.key("node-a") != manager.key("node-b")
        assert manager.key("") == "local|shell"
        assert len(manager.list()) == 2

        assert manager.destroy_node("node-a") == 1
        assert io_a.closed is True
        assert io_b.closed is False, "销毁某节点不得影响其他节点的会话"
        remaining = manager.list()
        assert len(remaining) == 1
        assert remaining[0]["key"] == manager.key("node-b")

    asyncio.run(scenario())


def test_input_forwarding_resize_and_eof_guard():
    """输入 / 尺寸转发到常驻进程；会话结束后写入被丢弃。"""
    async def scenario():
        io = FakeIO([], hold=True)
        manager = tty_persist.SessionManager()
        key = manager.key("node-1")
        session = manager.create(key, "t", io)

        session.write(b"ls\r")
        session.resize(30, 100)
        assert io.written == [b"ls\r"]
        assert io.resized == [(30, 100)]

        manager.destroy(key)
        await _wait_for(lambda: not session.alive())
        # 会话已结束：写入被丢弃，不再打到通道上
        session.write(b"ignored\r")
        assert io.written == [b"ls\r"]

    asyncio.run(scenario())


def test_buffer_trim_aligns_to_newline():
    """缓冲超限时裁剪头部并向后对齐换行，避免回放以半行 / 半截转义序列开头。"""
    async def scenario():
        io = FakeIO([], hold=True)
        session = tty_persist.PersistentSession(
            "local|shell", "t", io, asyncio.get_running_loop()
        )
        session.MAX_BUFFER = 8    # 收紧上限便于构造超限场景
        session.TRIM_ALIGN = 64   # 对齐搜索窗口放大，保证能命中换行

        session._on_output(b"AAAA\nBBBB\nCCCC\n")
        # 裁剪后从换行之后开始（"CCCC\n"），不会出现半行
        assert bytes(session._buffer) == b"CCCC\n"
        assert session.buffer_size <= session.MAX_BUFFER

    asyncio.run(scenario())


def test_shutdown_all_closes_every_session():
    """面板退出（lifespan shutdown）时关闭全部常驻会话，不留孤儿进程。"""
    async def scenario():
        manager = tty_persist.SessionManager()
        io1, io2 = FakeIO([], hold=True), FakeIO([], hold=True)
        manager.create(manager.key("node-a"), "a", io1)
        manager.create(manager.key("node-b"), "b", io2)

        manager.shutdown_all()
        assert io1.closed is True and io2.closed is True
        assert manager.list() == []

    asyncio.run(scenario())