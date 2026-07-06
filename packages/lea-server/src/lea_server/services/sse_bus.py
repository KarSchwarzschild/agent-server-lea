"""In-memory session event bus for async POST → EventSource GET pattern.

前端使用异步流式：
1. POST /chat/stream?async=true  → 立即返回 {session_id}，后台任务开始产生事件
2. GET  /chat/events/{session_id} → EventSource 消费事件

本 bus 提供：
- 每个 session 一个事件通道 + 回放缓冲区（支持 EventSource 的 last_event_id 重连）
- 事件按序编号 (event id)
- 心跳注入（防中间层超时断连）
- 取消支持（发布 cancelled 事件 + 取消后台任务）
- 完成后 quiescence 清理

单节点内存实现；未来上多节点时替换为 Redis pubsub。
"""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import AsyncGenerator, Optional

import structlog

logger = structlog.get_logger()

_BUFFER_MAX = 2000
_HEARTBEAT_INTERVAL_S = 15.0
_CLEANUP_AFTER_DONE_S = 300.0


@dataclass
class _Event:
    id: int
    event: str
    data: str  # already JSON-encoded


@dataclass
class _Channel:
    session_id: str
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    buffer: list[_Event] = field(default_factory=list)
    next_id: int = 1
    done: bool = False
    task: Optional[asyncio.Task] = None
    subscribers: int = 0
    done_at: Optional[float] = None


class SessionEventBus:
    """Session → 事件通道映射，单例。"""

    def __init__(self) -> None:
        self._channels: dict[str, _Channel] = {}
        self._lock = asyncio.Lock()

    async def get_or_create(self, session_id: str) -> _Channel:
        async with self._lock:
            ch = self._channels.get(session_id)
            if ch is None:
                ch = _Channel(session_id=session_id)
                self._channels[session_id] = ch
            return ch

    async def publish(self, session_id: str, event: str, data: dict) -> None:
        """向 session 通道推送一个事件。data 会被 JSON 序列化，自动加 _sid 用于前端串流校验。"""
        ch = await self.get_or_create(session_id)
        payload = dict(data) if isinstance(data, dict) else {"value": data}
        payload["_sid"] = session_id
        evt = _Event(id=ch.next_id, event=event, data=json.dumps(payload, ensure_ascii=False))
        ch.next_id += 1
        ch.buffer.append(evt)
        if len(ch.buffer) > _BUFFER_MAX:
            ch.buffer = ch.buffer[-_BUFFER_MAX:]
        await ch.queue.put(evt)

    async def mark_done(self, session_id: str) -> None:
        """标记 producer 完成，subscriber 拉完 buffer 后应结束。"""
        ch = await self.get_or_create(session_id)
        ch.done = True
        ch.done_at = time.time()
        await ch.queue.put(None)  # sentinel

    def get_channel(self, session_id: str) -> Optional[_Channel]:
        return self._channels.get(session_id)

    def attach_task(self, session_id: str, task: asyncio.Task) -> None:
        ch = self._channels.get(session_id)
        if ch is not None:
            ch.task = task

    async def cancel(self, session_id: str) -> bool:
        """用户主动停止：取消后台任务，发 cancelled 事件。返回是否找到并取消了任务。"""
        ch = self._channels.get(session_id)
        if ch is None:
            return False
        was_running = ch.task is not None and not ch.task.done()
        if was_running and ch.task is not None:
            ch.task.cancel()
        await self.publish(session_id, "cancelled", {"reason": "user_stop"})
        await self.mark_done(session_id)
        return was_running

    async def subscribe(
        self,
        session_id: str,
        last_event_id: Optional[int] = None,
    ) -> AsyncGenerator[_Event, None]:
        """订阅 session 事件流。先回放 buffer 中 id > last_event_id 的事件，再追 queue。

        publish() 同时写 buffer 和 queue，subscribe 用 max_yielded 去重避免重发。
        """
        ch = await self.get_or_create(session_id)
        ch.subscribers += 1
        try:
            max_yielded = last_event_id or 0

            while True:
                # 阶段 1：回放 buffer 中未 yield 过的事件
                for evt in list(ch.buffer):
                    if evt.id > max_yielded:
                        yield evt
                        max_yielded = evt.id

                # 阶段 2：如果 producer 已完成，再扫一遍 buffer 抓可能的漏（并发窗口内）后退出
                if ch.done:
                    for evt in list(ch.buffer):
                        if evt.id > max_yielded:
                            yield evt
                            max_yielded = evt.id
                    return

                # 阶段 3：等下一个事件（超时时发心跳）
                try:
                    item = await asyncio.wait_for(ch.queue.get(), timeout=_HEARTBEAT_INTERVAL_S)
                except asyncio.TimeoutError:
                    yield _Event(id=0, event="heartbeat", data=json.dumps({"ts": time.time()}))
                    continue
                if item is None:
                    # mark_done 的 sentinel，回到 top 走 done 分支
                    continue
                if item.id > max_yielded:
                    yield item
                    max_yielded = item.id
        finally:
            ch.subscribers -= 1

    async def gc(self) -> int:
        """清理 done 且无订阅者且超时的 session。"""
        now = time.time()
        removed = 0
        async with self._lock:
            to_remove = [
                sid
                for sid, ch in self._channels.items()
                if ch.done
                and ch.subscribers == 0
                and ch.done_at
                and (now - ch.done_at) > _CLEANUP_AFTER_DONE_S
            ]
            for sid in to_remove:
                self._channels.pop(sid, None)
                removed += 1
        if removed:
            logger.info("sse_bus_gc", removed=removed)
        return removed


_bus: Optional[SessionEventBus] = None


def get_event_bus() -> SessionEventBus:
    """获取全局单例。"""
    global _bus
    if _bus is None:
        _bus = SessionEventBus()
    return _bus
