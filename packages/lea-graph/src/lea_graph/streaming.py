"""
LangGraph stream → SSE 适配器。

将 LangGraph 的异步流式输出转换为 Server-Sent Events 格式，
供 FastAPI StreamingResponse 使用。对话入口和App内部API都可复用。

事件投递通过 EventBus 抽象：
- 当前实现（DirectEventBus）：直接 yield，零开销
- 将来需要时可替换为 RedisEventBus：Redis Stream 持久化 + 重连续传
  上下游代码不用改，只需换 EventBus 实现
"""

from __future__ import annotations

import json
import time
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import structlog
from langchain_core.messages import AIMessageChunk

logger = structlog.get_logger()


class EventPriority(Enum):
    NORMAL = "normal"
    CRITICAL = "critical"


CRITICAL_EVENTS = frozenset({"done", "error", "session_created", "plan_created", "artifact_done"})


@dataclass
class SSEEvent:
    event: str
    data: dict[str, Any]
    priority: EventPriority = EventPriority.NORMAL
    id: str | None = None
    timestamp: float = field(default_factory=time.time)

    def format(self) -> str:
        parts = []
        if self.id:
            parts.append(f"id: {self.id}")
        parts.append(f"event: {self.event}")
        parts.append(f"data: {json.dumps(self.data, ensure_ascii=False)}")
        return "\n".join(parts) + "\n\n"


class EventBus(ABC):
    """事件投递抽象——将来替换实现即可支持 Redis Stream 持久化。"""

    @abstractmethod
    async def publish(self, session_id: str, event: SSEEvent) -> None: ...

    @abstractmethod
    async def subscribe(self, session_id: str, last_event_id: str | None = None) -> AsyncIterator[SSEEvent]: ...


class DirectEventBus(EventBus):
    """直通实现：无持久化，事件直接投递。适合当前阶段。"""

    def __init__(self):
        self._events: dict[str, list[SSEEvent]] = {}

    async def publish(self, session_id: str, event: SSEEvent) -> None:
        if session_id not in self._events:
            self._events[session_id] = []
        self._events[session_id].append(event)

    async def subscribe(self, session_id: str, last_event_id: str | None = None) -> AsyncIterator[SSEEvent]:
        for event in self._events.get(session_id, []):
            yield event


def _make_event(event_type: str, data: dict[str, Any]) -> SSEEvent:
    priority = EventPriority.CRITICAL if event_type in CRITICAL_EVENTS else EventPriority.NORMAL
    return SSEEvent(event=event_type, data=data, priority=priority)


def _sse(event: str, data: dict[str, Any]) -> str:
    return _make_event(event, data).format()


async def stream_graph_as_sse(
    graph,
    input_state: dict,
    config: dict,
) -> AsyncIterator[str]:
    """通用流式适配器：LangGraph → SSE。

    支持两种流事件：
    - messages: LLM逐token输出 → SSE content事件
    - custom: Agent/工具通过 get_stream_writer() 发送的自定义事件
    """
    usage_total: dict[str, int] = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    try:
        async for event in graph.astream(
            input_state,
            config=config,
            stream_mode=["messages", "custom"],
            subgraphs=True,
        ):
            if not isinstance(event, tuple) or len(event) < 3:
                continue

            _ns, kind, payload = event[0], event[1], event[2]

            if kind == "messages":
                msg_chunk, _metadata = payload
                if isinstance(msg_chunk, AIMessageChunk):
                    if msg_chunk.content:
                        yield _sse("content", {"text": msg_chunk.content})
                    um = getattr(msg_chunk, "usage_metadata", None)
                    if um:
                        usage_total["input_tokens"] += um.get("input_tokens", 0)
                        usage_total["output_tokens"] += um.get("output_tokens", 0)
                        usage_total["total_tokens"] += um.get("total_tokens", 0)

            elif kind == "custom":
                if isinstance(payload, dict):
                    data = dict(payload)
                    event_type = data.pop("type", "status")
                    yield _sse(event_type, data)

    except Exception as exc:
        logger.error("stream_error", error=str(exc))
        yield _sse("error", {"message": str(exc)})

    done_data: dict[str, Any] = {"status": "completed"}
    if usage_total["total_tokens"] > 0:
        done_data["usage"] = usage_total
    yield _sse("done", done_data)


async def stream_task_progress(
    task_id: str,
    progress_source: AsyncIterator[dict[str, Any]],
    heartbeat_interval: float = 15.0,
) -> AsyncIterator[str]:
    """长耗时后台任务的进度推送适配器。

    用于综述生成、批量导入等场景。当前直接 yield；
    将来接入 EventBus 后可支持断线重连续传。

    Args:
        task_id: 任务ID
        progress_source: 产出 {"type": "progress", "percent": 50, ...} 的异步迭代器
        heartbeat_interval: 心跳间隔（秒），防止代理/防火墙断开空闲连接
    """
    import asyncio

    last_heartbeat = time.time()

    try:
        async for update in progress_source:
            event_type = update.pop("type", "progress")
            yield _sse(event_type, update)
            last_heartbeat = time.time()

            if time.time() - last_heartbeat >= heartbeat_interval:
                yield _sse("heartbeat", {"task_id": task_id})
                last_heartbeat = time.time()

    except asyncio.CancelledError:
        yield _sse("error", {"message": "客户端断开连接", "task_id": task_id})
    except Exception as exc:
        logger.error("task_stream_error", task_id=task_id, error=str(exc))
        yield _sse("error", {"message": str(exc), "task_id": task_id})

    yield _sse("done", {"status": "completed", "task_id": task_id})
