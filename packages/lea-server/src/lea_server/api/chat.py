"""对话入口API — SSE流式，走Supervisor图或直接执行Skill。"""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel
from sqlalchemy.orm import Session

from lea_server.core.deps import get_current_user
from lea_server.database import get_db
from lea_server.models.models import User
from lea_server.services.skill_service import SkillService
from lea_graph.streaming import stream_graph_as_sse

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatRequest(BaseModel):
    message: str
    session_id: str = "default"
    skill_id: int | None = None
    attachment_ids: list[int] | None = None


@router.post("/stream")
async def chat_stream(
    req: ChatRequest,
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """对话流式接口。

    支持两种模式：
    1. skill_id 不为空 → 直接用该 Skill 的 prompt+tools 构建 Agent 执行
    2. skill_id 为空 → 走 Supervisor 路由（关键词 → LLM 语义 → Fallback）
    """
    registry = request.app.state.registry
    producer = request.app.state.producer

    producer.send_audit(
        action="chat",
        tenant_id=getattr(user, "tenant_id", "default"),
        user_id=user.user_id,
        session_id=req.session_id,
        message_length=len(req.message),
    )

    thread_id = f"{user.user_id}:{req.session_id}"

    if req.skill_id:
        generator = _stream_with_skill(req, db, registry, thread_id)
    else:
        generator = _stream_with_supervisor(req, request, registry, db, user, thread_id)

    async def _with_metrics():
        start_ts = time.time()
        async for chunk in generator:
            yield chunk
        producer.send_metrics(
            metric_name="chat_latency_ms",
            value=round((time.time() - start_ts) * 1000),
            tenant_id=getattr(user, "tenant_id", "default"),
            session_id=req.session_id,
        )

    return StreamingResponse(
        _with_metrics(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _stream_with_skill(req, db, registry, thread_id):
    """显式 Skill 执行路径。"""
    from lea_graph.skills.runner import execute_skill_as_sse
    from lea_graph.streaming import _sse

    svc = SkillService(db)
    skill = svc.get_skill(req.skill_id)
    if skill is None:
        yield _sse("error", {"message": f"技能不存在 (id={req.skill_id})"})
        yield _sse("done", {"status": "error"})
        return

    definition = svc.to_definition(skill)
    all_tools = _collect_all_tools(registry)

    async for chunk in execute_skill_as_sse(
        definition, req.message, all_tools, thread_id=thread_id,
    ):
        yield chunk


async def _stream_with_supervisor(req, request, registry, db, user, thread_id):
    """Supervisor 路由执行路径。"""
    supervisor = request.app.state.supervisor

    # 更新 session 活跃状态
    _touch_session(db, user.user_id, req.session_id, req.message)

    skill_candidates, skill_definitions = _get_skill_data(db, user.user_id)
    all_tools = _collect_all_tools(registry)

    config = {
        "configurable": {
            "thread_id": thread_id,
            "app_skills": registry.get_app_skills_map(),
            "skill_candidates": skill_candidates,
            "skill_definitions": skill_definitions,
            "all_tools": all_tools,
        }
    }

    message_content = _build_message_content(req, db)

    input_state = {
        "messages": [HumanMessage(content=message_content)],
        "user_id": user.user_id,
        "session_metadata": {"db_session_id": req.session_id},
    }

    # 收集 assistant 回复文本和 token 用量，流结束后写入 MySQL
    response_parts: list[str] = []
    token_usage: dict = {}

    async for chunk in stream_graph_as_sse(supervisor, input_state, config):
        # 从 SSE chunk 中提取文本和 usage
        if isinstance(chunk, str) and "data: " in chunk:
            try:
                import json as _json
                for line in chunk.split("\n"):
                    if line.startswith("data: "):
                        data = _json.loads(line[6:])
                        if data.get("text"):
                            response_parts.append(data["text"])
                        if data.get("usage"):
                            token_usage = data["usage"]
            except Exception:
                pass
        yield chunk

    # 持久化消息到 MySQL
    _save_messages(db, req.session_id, message_content, "".join(response_parts), token_usage)


def _collect_all_tools(registry) -> dict:
    """收集所有可用 Tool（platform + app + mcp）。"""
    from langchain_core.tools import BaseTool
    from lea_graph.tools import get_platform_tools
    from lea_graph.mcp.client import get_mcp_client

    tools: dict[str, BaseTool] = {}
    for t in get_platform_tools():
        tools[t.name] = t
    for name, app_instance in registry.apps.items():
        for t in app_instance.skills:
            tools[t.name] = t
    mcp = get_mcp_client()
    if mcp:
        for t in mcp.get_tools():
            tools[t.name] = t
    return tools


def _build_message_content(req: ChatRequest, db) -> str:
    """构造消息内容：如果有附件则注入提取的文本。"""
    if not req.attachment_ids:
        return req.message

    from lea_server.models.attachment import ChatAttachment

    attachments = (
        db.query(ChatAttachment)
        .filter(ChatAttachment.id.in_(req.attachment_ids))
        .all()
    )

    if not attachments:
        return req.message

    parts: list[str] = []
    for att in attachments:
        if att.extracted_text:
            parts.append(
                f"[文件: {att.filename}]\n"
                f"--- 内容 ---\n"
                f"{att.extracted_text}\n"
                f"--- 内容结束 ---"
            )
        else:
            parts.append(f"[文件: {att.filename}（无法提取文本内容）]")

    attachment_block = "\n\n".join(parts)
    return f"{attachment_block}\n\n{req.message}"


def _get_skill_data(db, user_id: str) -> tuple[list[dict], dict]:
    """从 DB 加载 Skill 候选列表和定义映射。"""
    svc = SkillService(db)
    skills, _ = svc.list_skills(user_id=user_id, page=1, size=100)

    candidates = [
        {"name": s.name, "description": s.description or s.display_name}
        for s in skills
    ]

    definitions = {
        s.name: svc.to_definition(s)
        for s in skills
    }

    return candidates, definitions


def _touch_session(db, user_id: str, session_id: str, message: str) -> None:
    """更新或创建 session 记录。"""
    from lea_server.models.chat_session import ChatSession

    session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
    if session is None:
        session = ChatSession(
            id=session_id,
            user_id=user_id,
            title=message[:30] if message else "新对话",
            message_count=1,
        )
        db.add(session)
    else:
        session.message_count = (session.message_count or 0) + 1
    db.commit()


def _save_messages(
    db, session_id: str, user_msg: str, assistant_msg: str, usage: dict | None = None
) -> None:
    """将用户消息和 AI 回复持久化到 MySQL。"""
    from lea_server.models.chat_session import ChatMessage

    input_tokens = (usage or {}).get("input_tokens", 0)
    output_tokens = (usage or {}).get("output_tokens", 0)

    if user_msg:
        db.add(ChatMessage(
            session_id=session_id, role="user", content=user_msg,
            token_count=input_tokens or None,
        ))
    if assistant_msg:
        db.add(ChatMessage(
            session_id=session_id, role="assistant", content=assistant_msg,
            token_count=output_tokens or None,
        ))
    if user_msg or assistant_msg:
        db.commit()
