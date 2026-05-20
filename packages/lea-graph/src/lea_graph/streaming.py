"""
LangGraph stream → SSE 适配器。

将 LangGraph 的异步流式输出转换为 Server-Sent Events 格式，
供 FastAPI StreamingResponse 使用。对话入口和App内部API都可复用。
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

import structlog
from langchain_core.messages import AIMessageChunk

logger = structlog.get_logger()


def _sse(event: str, data: dict[str, Any]) -> str:
    """格式化为 SSE 事件字符串。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def stream_graph_as_sse(
    graph,
    input_state: dict,
    config: dict,
) -> AsyncIterator[str]:
    """通用流式适配器：LangGraph → SSE。

    支持两种流事件：
    - messages: LLM逐token输出 → SSE content事件
    - custom: Agent/工具通过 get_stream_writer() 发送的自定义事件

    用法示例:
        return StreamingResponse(
            stream_graph_as_sse(graph, input_state, config),
            media_type="text/event-stream",
        )
    """
    try:
        async for event in graph.astream(
            input_state,
            config=config,
            stream_mode=["messages", "custom"],
            subgraphs=True,
        ):
            kind = event[0] if isinstance(event, tuple) else None

            if kind == "messages":
                _ns, (msg_chunk, _metadata) = event[1], event[2]
                if isinstance(msg_chunk, AIMessageChunk) and msg_chunk.content:
                    yield _sse("content", {"text": msg_chunk.content})

            elif kind == "custom":
                data = event[2] if len(event) > 2 else event[1]
                if isinstance(data, dict):
                    event_type = data.pop("type", "status")
                    yield _sse(event_type, data)

    except Exception as exc:
        logger.error("stream_error", error=str(exc))
        yield _sse("error", {"message": str(exc)})

    yield _sse("done", {"status": "completed"})
