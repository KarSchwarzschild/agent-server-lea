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
from langchain_core.messages import AIMessageChunk, ToolMessage

logger = structlog.get_logger()


class EventPriority(Enum):
    NORMAL = "normal"
    CRITICAL = "critical"


CRITICAL_EVENTS = frozenset({
    "done", "error", "session_created", "plan_created", "artifact_done",
    "sandbox_done", "sandbox_artifact",
})


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


def _extract_reasoning(msg_chunk) -> str | None:
    """从 AIMessageChunk 里挤出"真思考"字段。

    覆盖几种业界主流形态：
    - DeepSeek/QwQ OpenAI-compatible：`reasoning_content` 直接属性
    - LangChain 通用兜底：`additional_kwargs["reasoning_content"]`
    - Anthropic thinking blocks / LangChain 结构化 content：content 是 list，
      形如 [{"type": "thinking", "thinking": "..."}, ...]
    非思考模型对这三个都无 → 返回 None，上游就不会 emit reasoning 事件。
    """
    val = getattr(msg_chunk, "reasoning_content", None)
    if isinstance(val, str) and val:
        return val

    ak = getattr(msg_chunk, "additional_kwargs", None) or {}
    val = ak.get("reasoning_content") if isinstance(ak, dict) else None
    if isinstance(val, str) and val:
        return val

    content = getattr(msg_chunk, "content", None)
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "thinking":
                text = item.get("thinking") or item.get("text")
                if isinstance(text, str) and text:
                    parts.append(text)
        if parts:
            return "".join(parts)

    return None


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

    # tool_call 增量状态：按 index 累积（因为只有首个 chunk 有 id / name，args 逐字符串 chunk 拼接）
    # key=index, value={"tc_id": str, "name": str, "args_buf": str}
    tool_call_states: dict[int, dict] = {}
    tool_call_emitted: set[str] = set()
    # reasoning 累积：有思考的模型才 emit reasoning_done；无 field 就完全静默
    reasoning_seen = False

    def _flush_by_id(tc_id: str, fallback_name: str = "") -> str | None:
        """ToolMessage 到达时兜底：如果这个 tc_id 从来没 emit 过 tool_call_start，补一次。

        事件用 `tool_call_start` 命中前端 useSSEHandler 里创建 ToolCallCard 的 thinkingStep 入口
        （`tool_call` 事件只更新 toolCalls 数据、不渲染卡片）。
        """
        if not tc_id or tc_id in tool_call_emitted:
            return None
        # 找 tc_id 对应的 index state
        for state in tool_call_states.values():
            if state.get("tc_id") == tc_id:
                try:
                    parsed = json.loads(state.get("args_buf") or "{}")
                except json.JSONDecodeError:
                    parsed = {}
                tool_call_emitted.add(tc_id)
                name = state.get("name") or fallback_name
                return _sse("tool_call_start", {
                    "tool_call_id": tc_id,
                    "tool_name": name,
                    "tool_display_name": name,
                    "input": parsed,
                })
        # 完全没记录 → 用 fallback
        tool_call_emitted.add(tc_id)
        return _sse("tool_call_start", {
            "tool_call_id": tc_id,
            "tool_name": fallback_name,
            "tool_display_name": fallback_name,
            "input": {},
        })

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
                    # reasoning_content：DeepSeek-R1 / QwQ / Anthropic thinking 等模型专有
                    reasoning_text = _extract_reasoning(msg_chunk)
                    if reasoning_text:
                        reasoning_seen = True
                        yield _sse("reasoning_chunk", {"chunk": reasoning_text})

                    if msg_chunk.content:
                        yield _sse("content", {"text": msg_chunk.content})

                    # 按 index 累积 tool_call_chunks（id 只在首 chunk 出现，args 是字符串增量拼接）
                    for chunk in (getattr(msg_chunk, "tool_call_chunks", None) or []):
                        idx = chunk.get("index", 0) or 0
                        state = tool_call_states.setdefault(
                            idx, {"tc_id": "", "name": None, "args_buf": ""}
                        )
                        if chunk.get("id"):
                            state["tc_id"] = chunk["id"]
                        if chunk.get("name"):
                            state["name"] = chunk["name"]
                        if chunk.get("args"):
                            state["args_buf"] += chunk["args"]
                        # 完整 JSON 已到位且 tc_id/name 都有 → 发 tool_call_start（对齐前端渲染入口）
                        tc_id = state.get("tc_id")
                        if (
                            tc_id
                            and tc_id not in tool_call_emitted
                            and state.get("name")
                            and state.get("args_buf")
                        ):
                            try:
                                parsed = json.loads(state["args_buf"])
                                yield _sse("tool_call_start", {
                                    "tool_call_id": tc_id,
                                    "tool_name": state["name"],
                                    "tool_display_name": state["name"],
                                    "input": parsed,
                                })
                                tool_call_emitted.add(tc_id)
                            except json.JSONDecodeError:
                                pass  # 还没收完，继续等

                    um = getattr(msg_chunk, "usage_metadata", None)
                    if um:
                        usage_total["input_tokens"] += um.get("input_tokens", 0)
                        usage_total["output_tokens"] += um.get("output_tokens", 0)
                        usage_total["total_tokens"] += um.get("total_tokens", 0)

                elif isinstance(msg_chunk, ToolMessage):
                    tc_id = getattr(msg_chunk, "tool_call_id", "") or ""
                    tool_name = getattr(msg_chunk, "name", "") or ""
                    result_text = str(msg_chunk.content) if msg_chunk.content is not None else ""
                    # 兜底：如果 tool_call_start 从没发过（zero-arg 工具 or 异常流），补一次
                    flushed = _flush_by_id(tc_id, fallback_name=tool_name)
                    if flushed:
                        yield flushed
                    # observation：更新 thinkingStep 状态 → 卡片变绿显示 result
                    yield _sse("observation", {
                        "tool_call_id": tc_id,
                        "tool_name": tool_name,
                        "status": "success",
                        "result": result_text,
                    })

            elif kind == "custom":
                if isinstance(payload, dict):
                    data = dict(payload)
                    event_type = data.pop("type", "status")
                    yield _sse(event_type, data)

    except Exception as exc:
        logger.error("stream_error", error=str(exc))
        yield _sse("error", {"message": str(exc)})

    # 思考流收尾：只有真收到过 reasoning 才 emit reasoning_done（非思考模型完全静默）
    if reasoning_seen:
        yield _sse("reasoning_done", {})

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
