# -*- coding: utf-8 -*-
"""tty_persist.py - 终端「持久化会话」管理器（Graw 终端增强）

背景与目标：
    Graw 的 Web 终端默认「连接即生命周期」——WebSocket 一旦断开（刷新面板、
    切走标签页、网络抖动、关掉窗口），后端立刻销毁对应 shell 进程，重连等于
    新开一个终端。对长时间任务（编译、大文件下载、日志跟踪、数据导入）极为
    致命：刷新一下任务就没了。

    勾选终端工具栏的「保留持久化终端」后，本模块把 **shell 进程** 与
    **客户端连接** 解耦：

      - 首次连接：创建常驻会话（进程 + 最近 256KB 输出缓冲）；
      - 之后每次连接（刷新 / 重开窗口）：按「节点」键接入同一会话，
        先回放缓冲再继续交互，用户视角就是「同一个终端」；
      - WebSocket 断开只是「摘除客户端」，进程继续在后台跑、输出继续入缓冲；
      - 同一会话可被多个窗口同时接入：输入写入同一进程，输出广播给所有接入端
        （与 tmux attach 的多客户端行为一致）。

设计取舍：
    1. 不依赖 tmux / screen：目标主机（容器内、Windows、裸 SSH 节点）未必装
       有这些工具，自持一份轻量实现更可控。
    2. 会话只活在面板进程内：面板重启 / 容器重建后会话消失（进程随之退出）。
       这是「进程级持久化」而非「磁盘级持久化」，既满足「刷新不丢任务」的核心
       诉求，也避免把 shell 状态、凭据落盘带来的安全面。
    3. 会话数量恒定收敛：每个节点最多一个（键 = 节点 ID），不会随刷新增长；
       销毁入口有两个：用户取消勾选（DELETE /api/terminal/persist）与面板
       退出（main.py lifespan 调用 shutdown_all）。

线程模型：
    每个会话一条 daemon 读取线程（阻塞读 PTY / SSH 通道），读到数据后经
    ``loop.call_soon_threadsafe`` 回到事件循环线程，在那里写入回放缓冲并广播
    到各客户端队列（队列 -> 各客户端自己的发送协程 -> WebSocket）。既避免把
    阻塞读塞进事件循环，也保证「缓冲 + 广播」在同一线程串行、无竞争。
"""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Callable, Dict, List, Optional

logger = logging.getLogger("graw.tty_persist")


class SessionIO:
    """持久会话的底层通道抽象：把「进程怎么起来的」与「会话怎么共享」解耦。

    本地 PTY（pty.fork）、Windows ConPTY、远程 ssh -tt、paramiko 交互通道
    都只需在工厂函数里产出这一组回调，会话管理与客户端广播逻辑完全一致。
    """

    __slots__ = ("read", "write", "resize", "close")

    def __init__(
        self,
        read: Callable[[], bytes],
        write: Callable[[bytes], None],
        resize: Callable[[int, int], None],
        close: Callable[[], None],
    ) -> None:
        self.read = read      # 阻塞读；返回 b"" 表示通道结束（EOF）
        self.write = write    # 写入用户输入
        self.resize = resize  # 调整窗口尺寸 (rows, cols)
        self.close = close    # 关闭通道并回收进程 / 连接


class PersistentSession:
    """一个常驻终端会话：进程 + 回放缓冲 + 多个客户端订阅队列。"""

    MAX_BUFFER = 256 * 1024   # 回放缓冲上限：保留最近 256KB 输出
    TRIM_ALIGN = 4096         # 截断头部后最多再向后找 4KB 的换行做对齐
    EOT = b""                 # 队列结束哨兵（与 _pump_output 的空块语义一致）

    def __init__(self, key: str, title: str, io: SessionIO, loop: asyncio.AbstractEventLoop) -> None:
        self.key = key
        self.title = title
        self.io = io
        self.created_at = time.time()
        self.last_active = time.time()
        self._loop = loop
        self._buffer = bytearray()
        self._clients: Dict[int, "asyncio.Queue[bytes]"] = {}
        self._qid = 0                        # 客户端自增 ID（会话内唯一即可）
        self._eof = False                    # 通道已结束：不可再写入
        self._eof_notified = False           # 结束哨兵是否已广播（避免重复投递）
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def start(self) -> None:
        """启动后台读取线程（daemon：随面板进程退出而结束，不阻塞退出）。"""
        self._thread = threading.Thread(
            target=self._reader, name=f"graw-tty-{self.key}", daemon=True
        )
        self._thread.start()

    def alive(self) -> bool:
        """会话是否仍然可用（读取线程未判定通道结束）。"""
        return not self._eof

    def close(self) -> None:
        """销毁会话：关闭底层通道，并通知所有接入端收尾。"""
        try:
            self.io.close()
        except Exception:
            # 通道已断开 / 进程已退出：关闭失败无害
            logger.debug("持久终端通道关闭失败 key=%s", self.key, exc_info=True)
        try:
            # 交由事件循环线程广播结束哨兵（保持与输出广播同一线程语义）
            self._loop.call_soon_threadsafe(self._on_eof)
        except RuntimeError:
            # 事件循环已关闭（面板正在退出）：直接置位即可
            self._eof = True

    # ------------------------------------------------------------------
    # 读取线程：阻塞读 -> 回放缓冲 + 广播
    # ------------------------------------------------------------------
    def _reader(self) -> None:
        """后台读取线程：循环读取通道输出并投递到事件循环线程。

        读到 EOF 或抛出异常即认为通道结束（shell 退出 / ssh 断开 / 面板关停），
        标记会话结束并广播结束哨兵，让各客户端连接优雅收尾。
        """
        while True:
            try:
                chunk = self.io.read()
            except Exception:
                chunk = b""
            if not chunk:
                break
            try:
                self._loop.call_soon_threadsafe(self._on_output, chunk)
            except RuntimeError:
                # 事件循环已关闭：停止读取（面板退出中）
                break
        try:
            self._loop.call_soon_threadsafe(self._on_eof)
        except RuntimeError:
            self._eof = True

    def _on_output(self, chunk: bytes) -> None:
        """事件循环线程内处理一段输出：写缓冲 + 广播给所有接入端。"""
        self.last_active = time.time()
        self._append(chunk)
        for queue in list(self._clients.values()):
            try:
                queue.put_nowait(chunk)
            except Exception:
                # 单个客户端队列异常不影响其他接入端
                logger.debug("持久终端广播失败 key=%s", self.key, exc_info=True)

    def _on_eof(self) -> None:
        """事件循环线程内收尾：标记 EOF 并向所有接入端投递结束哨兵。"""
        if self._eof_notified:
            return
        self._eof_notified = True
        self._eof = True
        for queue in list(self._clients.values()):
            try:
                queue.put_nowait(self.EOT)
            except Exception:
                # 队列已满 / 已关闭：忽略，客户端随后自行断开
                pass

    def _append(self, chunk: bytes) -> None:
        """追加输出到回放缓冲，超出上限时从头部裁剪并向后对齐换行。

        裁剪点向后对齐到换行符，避免回放时开头出现「半行」或半截 ANSI 转义
        序列（表现为乱码）。若很长一段输出都没有换行（例如进度条只用 \\r
        刷新），最多再向后找 TRIM_ALIGN 字节就放弃对齐，防止把缓冲清空。
        """
        self._buffer.extend(chunk)
        over = len(self._buffer) - self.MAX_BUFFER
        if over <= 0:
            return
        start = over
        limit = min(len(self._buffer), over + self.TRIM_ALIGN)
        newline = self._buffer.find(b"\n", start, limit)
        if newline != -1:
            start = newline + 1
        del self._buffer[:start]

    # ------------------------------------------------------------------
    # 客户端接入 / 摘除
    # ------------------------------------------------------------------
    @property
    def client_count(self) -> int:
        """当前接入的客户端数量。"""
        return len(self._clients)

    @property
    def buffer_size(self) -> int:
        """回放缓冲当前字节数。"""
        return len(self._buffer)

    def attach(self) -> "tuple[int, asyncio.Queue[bytes], bytes]":
        """接入一个客户端。

        返回 ``(客户端 ID, 输出队列, 回放快照)``。快照在事件循环线程内同步
        取出，与后续经队列送达的新输出严格有序、不重不漏。
        """
        self._qid += 1
        qid = self._qid
        queue: "asyncio.Queue[bytes]" = asyncio.Queue()
        self._clients[qid] = queue
        self.last_active = time.time()
        return qid, queue, bytes(self._buffer)

    def detach(self, qid: int) -> None:
        """摘除一个客户端（WS 断开只摘客户端，不杀进程）。"""
        self._clients.pop(qid, None)
        self.last_active = time.time()

    # ------------------------------------------------------------------
    # 输入 / 尺寸
    # ------------------------------------------------------------------
    def write(self, data: bytes) -> None:
        """把用户输入写入常驻进程（会话已结束时静默丢弃）。"""
        if self._eof:
            return
        try:
            self.io.write(data)
        except Exception:
            # 通道瞬时异常（进程刚退出等）：忽略单次输入，避免打断会话循环
            logger.debug("持久终端写入失败 key=%s", self.key, exc_info=True)

    def resize(self, rows: int, cols: int) -> None:
        """同步终端窗口尺寸（失败忽略：尺寸变化属增强信息）。"""
        try:
            self.io.resize(rows, cols)
        except Exception:
            logger.debug("持久终端调整尺寸失败 key=%s", self.key, exc_info=True)

    # ------------------------------------------------------------------
    # 状态信息
    # ------------------------------------------------------------------
    def info(self) -> dict:
        """返回会话的脱敏状态（供 GET /api/terminal/persist 展示）。

        绝不包含命令、输出或任何凭据，只给「谁在跑、跑了多久、几个客户端」。
        """
        return {
            "key": self.key,
            "title": self.title,
            "created_at": self.created_at,
            "last_active": self.last_active,
            "clients": self.client_count,
            "buffer": self.buffer_size,
            "alive": self.alive(),
        }


class SessionManager:
    """持久会话注册表：按会话键（节点维度）取 / 建 / 销毁。

    仅由事件循环线程访问（WebSocket 处理函数与 lifespan 钩子），因此内部
    不做加锁；读取线程只通过会话对象自身的 call_soon_threadsafe 回投。
    """

    def __init__(self) -> None:
        self._sessions: Dict[str, PersistentSession] = {}

    @staticmethod
    def key(node_id: str) -> str:
        """会话键：按节点隔离，保证不同节点的终端绝不会串到同一个 shell。"""
        return f"{node_id or 'local'}|shell"

    def get(self, key: str) -> Optional[PersistentSession]:
        """取会话；若其进程已退出则顺手回收，返回 None 让调用方重建。"""
        session = self._sessions.get(key)
        if session is not None and not session.alive():
            self._sessions.pop(key, None)
            logger.info("持久终端会话已结束，回收注册 key=%s", key)
            session = None
        return session

    def create(self, key: str, title: str, io: SessionIO) -> PersistentSession:
        """创建并启动一个常驻会话（须在事件循环线程内调用）。"""
        session = PersistentSession(key, title, io, asyncio.get_running_loop())
        self._sessions[key] = session
        session.start()
        logger.info("创建持久终端会话 key=%s title=%s", key, title)
        return session

    def destroy(self, key: str) -> bool:
        """销毁指定会话（进程随之退出）。返回是否确实销毁了会话。"""
        session = self._sessions.pop(key, None)
        if session is None:
            return False
        session.close()
        logger.info("销毁持久终端会话 key=%s", key)
        return True

    def destroy_node(self, node_id: str) -> int:
        """销毁某节点下的全部会话（节点被删除时调用，避免残留 shell）。"""
        prefix = f"{node_id or 'local'}|"
        return sum(1 for k in [k for k in self._sessions if k.startswith(prefix)] if self.destroy(k))

    def list(self) -> List[dict]:
        """列出全部会话的脱敏状态。"""
        return [s.info() for s in self._sessions.values()]

    def shutdown_all(self) -> None:
        """关闭全部会话（面板退出时调用，确保不留下孤儿 shell 进程）。"""
        for key in list(self._sessions.keys()):
            self.destroy(key)


_manager: Optional[SessionManager] = None


def get_manager() -> SessionManager:
    """获取全局会话管理器单例（延迟创建，避免导入期依赖事件循环）。"""
    global _manager
    if _manager is None:
        _manager = SessionManager()
    return _manager