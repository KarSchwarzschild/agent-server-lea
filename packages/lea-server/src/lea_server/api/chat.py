"""对话入口API — SSE流式，走Supervisor图。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from pydantic import BaseModel

from lea_server.core.deps import get_current_user
from lea_server.models.models import User
from lea_graph.streaming import stream_graph_as_sse

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatRequest(BaseModel):
    message: str
    session_id: str = "default"


@router.post("/stream")
async def chat_stream(
    req: ChatRequest,
    request: Request,
    user: User = Depends(get_current_user),
):
    """对话流式接口。

    用户消息通过Supervisor图路由到合适的App:Agent，
    Agent执行后通过SSE逐token返回结果。

    SSE事件类型:
    - thinking: Agent正在分析意图
    - intent: 意图识别结果（app, agent, confidence）
    - action: 工具调用开始
    - observation: 工具调用结果
    - content: LLM生成的文本（逐token）
    - error: 执行错误
    - done: 完成
    """
    supervisor = request.app.state.supervisor
    registry = request.app.state.registry

    thread_id = f"{user.user_id}:{req.session_id}"
    config = {
        "configurable": {
            "thread_id": thread_id,
            "app_skills": registry.get_app_skills_map(),
        }
    }

    input_state = {
        "messages": [HumanMessage(content=req.message)],
        "target_app": "",
        "target_agent": "",
        "user_id": user.user_id,
        "session_metadata": {},
    }

    return StreamingResponse(
        stream_graph_as_sse(supervisor, input_state, config),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
