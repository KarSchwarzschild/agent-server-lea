"""对话入口API — SSE流式，走Supervisor图或直接执行Skill。

支持两种模式：
- 同步 SSE (POST /stream 无 async 参数)：请求-响应内直接流回事件
- 异步分离 (POST /stream?async=true + GET /events/{sid})：POST 立返 session_id，
  后台跑图，前端用 EventSource 消费；支持 last_event_id 断点续传 + 心跳。
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import BaseModel
from sqlalchemy.orm import Session, joinedload

from lea_server.core.auth import decode_token
from lea_server.core.deps import get_current_user
from lea_server.database import SessionLocal, get_db
from lea_server.models.models import User
from lea_server.services.skill_service import SkillService
from lea_server.services.sse_bus import get_event_bus
from lea_graph.streaming import stream_graph_as_sse

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatRequest(BaseModel):
    """POST /chat/stream 请求体。

    额外接受但当前未使用的字段（前端 v2 协议保留）: protocol_version, agent_id,
    force_skill, mode, discussion_mode。用 extra=allow 静默吸收。
    """

    model_config = {"extra": "allow"}

    message: str
    session_id: str | None = None
    skill_id: int | None = None
    attachment_ids: list[int] | None = None


@router.post("/stream")
async def chat_stream(
    req: ChatRequest,
    request: Request,
    async_mode: bool = Query(False, alias="async"),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """对话流式接口。

    - async=false（默认）：SSE 同步流式，直接返回事件
    - async=true：入队后台任务，立即返回 {"session_id": ...}；前端用 EventSource
      连 /events/{session_id} 消费事件

    路由：
    1. skill_id 不为空 → 直接用该 Skill 的 prompt+tools 构建 Agent 执行
    2. skill_id 为空 → 走 Supervisor 路由（关键词 → LLM 语义 → Fallback）
    """
    registry = request.app.state.registry
    producer = request.app.state.producer

    session_id = req.session_id or str(uuid.uuid4())
    req.session_id = session_id

    producer.send_audit(
        action="chat",
        tenant_id=getattr(user, "tenant_id", "default"),
        user_id=user.user_id,
        session_id=session_id,
        message_length=len(req.message),
    )

    thread_id = f"{user.user_id}:{session_id}"

    if async_mode:
        # 异步模式：确保 session 行已存在（前端拿到 session_id 后立即路由跳转会先查 detail）
        # 不递增 message_count；实际的 touch/save 由后台任务在 _stream_with_supervisor 里做
        _ensure_session_row(db, user.user_id, session_id, req.message)

        bus = get_event_bus()
        await bus.get_or_create(session_id)
        await bus.publish(session_id, "session_created", {"session_id": session_id})

        task = asyncio.create_task(
            _run_and_publish(req, request, registry, user, thread_id, session_id)
        )
        bus.attach_task(session_id, task)

        return {"session_id": session_id}

    # 同步模式：老路径不变
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
            session_id=session_id,
        )

    return StreamingResponse(
        _with_metrics(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


async def _run_and_publish(req, request, registry, user, thread_id: str, session_id: str) -> None:
    """后台任务：跑图 + 把 SSE chunk 反解为 event/data 推 bus。"""
    bus = get_event_bus()
    # 用独立 DB session 避免和请求 db 混用
    db = SessionLocal()
    try:
        if req.skill_id:
            gen = _stream_with_skill(req, db, registry, thread_id)
        else:
            gen = _stream_with_supervisor(req, request, registry, db, user, thread_id)

        async for chunk in gen:
            # 每个 chunk 是形如 "event: xxx\ndata: {...}\n\n" 的 SSE 文本；反解回结构
            for evt_type, payload in _parse_sse_chunk(chunk):
                await bus.publish(session_id, evt_type, payload)
    except asyncio.CancelledError:
        await bus.publish(session_id, "cancelled", {"reason": "cancelled"})
        raise
    except Exception as e:
        await bus.publish(session_id, "error", {"message": str(e)})
    finally:
        await bus.mark_done(session_id)
        db.close()


def _parse_sse_chunk(chunk: str):
    """把 'event: X\\ndata: {...}\\n\\n' 文本反解回 (event, data_dict) 迭代。"""
    if not isinstance(chunk, str) or "data:" not in chunk:
        return
    current_event: str | None = None
    for line in chunk.split("\n"):
        line = line.strip()
        if not line:
            if current_event is not None:
                current_event = None
            continue
        if line.startswith("event:"):
            current_event = line[len("event:"):].strip()
        elif line.startswith("data:"):
            raw = line[len("data:"):].strip()
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                data = {"raw": raw}
            yield (current_event or "content"), data
            current_event = None


@router.get("/events/{session_id}")
async def chat_events(
    session_id: str,
    token: str = Query(...),
    last_event_id: int | None = Query(None),
):
    """EventSource 消费端。JWT 走 query token（EventSource 无法设 header）。

    支持 last_event_id 断点续传。心跳每 15s 一次（避免中间层超时）。
    """
    token_data = decode_token(token)
    if token_data is None:
        raise HTTPException(status_code=401, detail="invalid token")

    # 拉出 user 校验（要求 session 归属该 user 未来加，Ph2 加租户后处理）
    db = SessionLocal()
    try:
        user = (
            db.query(User)
            .options(joinedload(User.roles))
            .filter(User.user_id == token_data.user_id)
            .first()
        )
        if user is None or not user.is_active:
            raise HTTPException(status_code=401, detail="user inactive")
    finally:
        db.close()

    bus = get_event_bus()

    async def _stream():
        async for evt in bus.subscribe(session_id, last_event_id=last_event_id):
            # id=0 是心跳，用 SSE 注释形式避免和真实事件抢 id
            if evt.id == 0 and evt.event == "heartbeat":
                yield f": heartbeat {evt.data}\n\n"
                continue
            yield f"id: {evt.id}\nevent: {evt.event}\ndata: {evt.data}\n\n"

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
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
    """更新或创建 session 记录（会递增 message_count，供同步/后台任务路径调用）。"""
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


def _ensure_session_row(db, user_id: str, session_id: str, message: str) -> None:
    """Pre-create session row if missing（不递增 count）。供异步 POST 立即返回前使用。"""
    from lea_server.models.chat_session import ChatSession

    existing = db.query(ChatSession).filter(ChatSession.id == session_id).first()
    if existing is None:
        db.add(ChatSession(
            id=session_id,
            user_id=user_id,
            title=message[:30] if message else "新对话",
            message_count=0,
        ))
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
