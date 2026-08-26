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

    额外接受但当前未使用的字段（前端 v2 协议保留）: protocol_version,
    force_skill, mode, discussion_mode。用 extra=allow 静默吸收。

    agent_id 语义：
    - None / 0（stub 前端）→ 兜底为 tenant 的 super-agent
    - >0 → 精确匹配某个 agent；候选池由该 agent 的绑定关系决定
    """

    model_config = {"extra": "allow"}

    message: str
    session_id: str | None = None
    skill_id: int | None = None
    agent_id: int | None = None
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
        generator = _stream_with_skill(req, db, registry, thread_id, user=user)
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
            gen = _stream_with_skill(req, db, registry, thread_id, user=user)
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


async def _stream_with_skill(req, db, registry, thread_id, user=None):
    """显式 Skill 执行路径。"""
    import time

    from lea_graph.skills.hydrator import hydrate_bundle
    from lea_graph.skills.runner import execute_skill_as_sse
    from lea_graph.streaming import _sse

    svc = SkillService(db)
    skill = svc.get_skill(req.skill_id)
    if skill is None:
        yield _sse("error", {"message": f"技能不存在 (id={req.skill_id})"})
        yield _sse("done", {"status": "error"})
        return

    definition = svc.to_definition(skill)
    definition = hydrate_bundle(definition)
    all_tools = _collect_all_tools(registry)

    if user is not None:
        _touch_session(db, user.user_id, req.session_id, req.message)

    collector = _ChatMessageCollector()
    collector.observe("skill_start", {
        "skill_name": definition.name,
        "display_name": definition.display_name,
    })

    start_ts = time.time()
    status = "success"

    async for chunk in execute_skill_as_sse(
        definition, req.message, all_tools, thread_id=thread_id,
    ):
        for evt_type, payload in _parse_sse_chunk(chunk):
            collector.observe(evt_type, payload)
            if evt_type == "error":
                status = "error"
        yield chunk

    duration_ms = round((time.time() - start_ts) * 1000)

    if user is not None:
        _save_messages(
            db,
            req.session_id,
            req.message,
            collector.response_text,
            collector.token_usage,
            artifact_id=collector.artifact_id,
            extra=collector.to_extra(),
        )

    _record_skill_invocation(
        skill_id=skill.id,
        skill_name=skill.name,
        user_id=user.user_id if user else None,
        session_id=req.session_id,
        source="chat",
        status=status,
        input_tokens=(collector.token_usage or {}).get("input_tokens", 0),
        output_tokens=(collector.token_usage or {}).get("output_tokens", 0),
        duration_ms=duration_ms,
    )


async def _stream_with_supervisor(req, request, registry, db, user, thread_id):
    """Supervisor 路由执行路径。"""
    supervisor = request.app.state.supervisor

    # 更新 session 活跃状态
    _touch_session(db, user.user_id, req.session_id, req.message)

    agent_id = _resolve_agent_id(db, req.agent_id, user)
    skill_candidates, skill_definitions = _get_skill_data(
        db, user.user_id, agent_id=agent_id,
        user_roles=[r.code for r in (user.roles or [])],
    )
    all_tools = _collect_all_tools(registry)
    agent_kb_ids = _get_agent_kb_ids(db, agent_id)  # 空 list = 该 agent 未绑任何 KB
    agent_soul_prompt = _get_agent_soul_prompt(db, agent_id)
    team_config = _get_team_config(db, agent_id)

    config = {
        "configurable": {
            "thread_id": thread_id,
            "agent_id": agent_id,
            "tenant_id": getattr(user, "tenant_id", "default"),
            "agent_kb_ids": agent_kb_ids,  # 供 kb_search tool 白名单 + 动态注入判断
            "agent_soul_prompt": agent_soul_prompt,
            "app_skills": registry.get_app_skills_map(),
            "skill_candidates": skill_candidates,
            "skill_definitions": skill_definitions,
            "all_tools": all_tools,
            "team_config": team_config,
            "team_candidates": [],  # TODO: 后续从 DB 加载所有可见 team 供路由
        }
    }

    message_content = _build_message_content(req, db)

    input_state = {
        "messages": [HumanMessage(content=message_content)],
        "user_id": user.user_id,
        "session_metadata": {"db_session_id": req.session_id},
    }

    if team_config:
        input_state["target_app"] = "team"
        input_state["target_agent"] = ""
        input_state["target_skill"] = ""

    # SSE 事件收集器：一并搜集可视化字段（intent/tool_calls/references/...），流结束后落 extra
    collector = _ChatMessageCollector()

    start_ts = time.time()

    async for chunk in stream_graph_as_sse(supervisor, input_state, config):
        for evt_type, payload in _parse_sse_chunk(chunk):
            collector.observe(evt_type, payload)
        yield chunk

    duration_ms = round((time.time() - start_ts) * 1000)

    # 持久化消息到 MySQL（含 artifact 关联 + 可视化字段）
    _save_messages(
        db,
        req.session_id,
        message_content,
        collector.response_text,
        collector.token_usage,
        artifact_id=collector.artifact_id,
        extra=collector.to_extra(),
    )

    if collector.skill and collector.skill.get("name"):
        from lea_server.models.skill import Skill
        skill_name = collector.skill["name"]
        skill_row = db.query(Skill).filter(Skill.name == skill_name).first() if skill_name else None
        _record_skill_invocation(
            skill_id=skill_row.id if skill_row else None,
            skill_name=skill_name,
            user_id=user.user_id,
            session_id=req.session_id,
            source="supervisor",
            input_tokens=(collector.token_usage or {}).get("input_tokens", 0),
            output_tokens=(collector.token_usage or {}).get("output_tokens", 0),
            duration_ms=duration_ms,
        )


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


def _get_skill_data(
    db, user_id: str, agent_id: int | None = None, user_roles: list[str] | None = None,
) -> tuple[list[dict], dict]:
    """从 DB 加载 Skill 候选列表和定义映射。

    - agent_id 提供 → 只返回该 agent 已绑定且启用的 skill（+ published 状态）
    - agent_id 未提供 → 回退到旧行为（按 user 可见性 + 角色白名单拉全部）
    """
    from lea_graph.skills.hydrator import hydrate_bundle
    from lea_server.models.agent_binding import AgentSkillBinding
    from lea_server.models.skill import Skill
    from lea_graph.skills.schema import SKILL_STATUS_PUBLISHED

    svc = SkillService(db)

    if agent_id is not None:
        rows = (
            db.query(Skill)
            .join(AgentSkillBinding, AgentSkillBinding.skill_id == Skill.id)
            .filter(
                AgentSkillBinding.agent_id == agent_id,
                AgentSkillBinding.enabled == True,  # noqa: E712
                Skill.status == SKILL_STATUS_PUBLISHED,
            )
            .order_by(AgentSkillBinding.priority, Skill.id)
            .all()
        )
        skills = rows
    else:
        skills, _ = svc.list_skills(user_id=user_id, user_roles=user_roles, page=1, size=100)

    candidates = [
        {
            "name": s.name,
            "display_name": s.display_name or s.name,
            "description": s.description or s.display_name,
        }
        for s in skills
    ]
    # SG-3: 从 OBS 拉 bundle 补齐运行时字段（system_prompt/scripts/references）。
    # cache 命中零 IO；miss 时同步下载 → 未来热点可加 asyncio.gather 并行
    definitions = {s.name: hydrate_bundle(svc.to_definition(s)) for s in skills}
    return candidates, definitions


def _get_agent_kb_ids(db, agent_id: int | None) -> list[int]:
    """agent 已绑定且启用的 kb_id 列表；agent_id 为空则返回空列表。

    空列表语义 = 该 agent 未挂 KB → executor 不应注入 kb_search tool。
    """
    if agent_id is None:
        return []
    from lea_server.services.agent_binding_service import AgentBindingService

    return AgentBindingService(db).get_bound_kb_ids(agent_id, only_enabled=True)


def _get_agent_soul_prompt(db, agent_id: int | None) -> str:
    """读取 agent 的 soul_prompt（人格提示词）。"""
    if agent_id is None:
        return ""
    from lea_server.models.agent import Agent

    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    return (agent.soul_prompt or "") if agent else ""


def _get_team_config(db, agent_id: int | None) -> dict | None:
    """若 agent_id 指向 Team，返回完整团队配置；否则 None。"""
    if agent_id is None:
        return None
    from lea_server.models.agent import Agent
    from lea_server.models.agent_team import AgentTeam, AgentTeamMember
    from lea_server.services.agent_binding_service import AgentBindingService

    team = db.query(AgentTeam).filter(AgentTeam.agent_id == agent_id).first()
    if team is None:
        return None

    rows = (
        db.query(AgentTeamMember, Agent)
        .join(Agent, Agent.id == AgentTeamMember.member_agent_id)
        .filter(AgentTeamMember.team_id == team.id, AgentTeamMember.enabled == True)  # noqa: E712
        .order_by(AgentTeamMember.sort_order, AgentTeamMember.id)
        .all()
    )
    if not rows:
        return None

    binding_svc = AgentBindingService(db)
    facade = db.query(Agent).filter(Agent.id == agent_id).first()

    members = []
    for m, agent in rows:
        members.append({
            "agent_id": agent.id,
            "name": agent.name,
            "avatar": agent.avatar or "",
            "soul_prompt": agent.soul_prompt or "",
            "role_description": m.role_description or "",
            "skill_ids": binding_svc.get_bound_skill_ids(agent.id, only_enabled=True),
            "kb_ids": binding_svc.get_bound_kb_ids(agent.id, only_enabled=True),
        })

    return {
        "team_id": team.id,
        "team_name": facade.name if facade else "专家团",
        "strategy": team.strategy or "parallel",
        "decomposer_prompt": team.decomposer_prompt or "",
        "synthesizer_prompt": team.synthesizer_prompt or "",
        "max_subtasks": team.max_subtasks or 5,
        "members": members,
    }


def _resolve_agent_id(db, requested: int | None, user: User) -> int | None:
    """解析请求带的 agent_id：None/0（stub 前端）→ tenant 的 super-agent 真实 id。

    找不到就返回 None（回退到旧的全 skill 候选，避免直接 500）。
    """
    from lea_server.core.tenant import DEFAULT_TENANT_ID
    from lea_server.models.agent import Agent

    if requested and requested > 0:
        return requested

    tenant_id = getattr(user, "tenant_id", DEFAULT_TENANT_ID) or DEFAULT_TENANT_ID
    super_agent = (
        db.query(Agent)
        .filter(
            Agent.name == "super-agent",
            Agent.tenant_id == tenant_id,
            Agent.is_system == True,  # noqa: E712
        )
        .first()
    )
    return super_agent.id if super_agent else None


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


class _ChatMessageCollector:
    """SSE 事件收集器：把流经的可视化字段挑出来，落到 chat_messages.extra。

    对齐前端 SuperAgent.vue 期待的字段（intent/skill/tool_calls/references/reasoning/plan），
    这样 switchSession 时能从 DB 恢复整段可视化。前端后期改样式不需要动这里的形状。
    """

    def __init__(self):
        self.response_parts: list[str] = []
        self.token_usage: dict = {}
        self.artifact_id: str | None = None
        self.intent: dict = {}
        self.skill: dict = {}
        self.tool_calls: list[dict] = []
        self._tool_call_index: dict[str, int] = {}
        self.references: list[dict] = []
        self._ref_ids: set[str] = set()
        self.reasoning_blocks: list[dict] = []
        self._reasoning_index: dict[str, int] = {}
        self.plan: dict | None = None
        self.sandbox_calls: list[dict] = []
        self._sandbox_index: dict[str, int] = {}

    def observe(self, evt_type: str, data: dict) -> None:
        if not isinstance(data, dict):
            return

        if evt_type == "content":
            if data.get("text"):
                self.response_parts.append(data["text"])
        elif evt_type == "done":
            if data.get("usage"):
                self.token_usage = data["usage"]
        elif evt_type == "intent":
            self.intent = {
                k: data[k]
                for k in ("skill", "skill_display_name", "app", "agent", "confidence")
                if k in data
            }
        elif evt_type in ("skill_start", "skill_done"):
            name = data.get("skill_name") or data.get("skill")
            if name:
                self.skill = {
                    "name": name,
                    "display_name": data.get("display_name")
                    or data.get("skill_display_name")
                    or name,
                }
        elif evt_type == "tool_call_start":
            tc_id = data.get("tool_call_id") or ""
            if tc_id and tc_id not in self._tool_call_index:
                self._tool_call_index[tc_id] = len(self.tool_calls)
                self.tool_calls.append({
                    "toolCallId": tc_id,
                    "tool": data.get("tool_name") or "",
                    "toolDisplayName": data.get("tool_display_name")
                    or data.get("tool_name")
                    or "",
                    "input": data.get("input") or {},
                    "status": "running",
                })
        elif evt_type == "observation":
            tc_id = data.get("tool_call_id") or ""
            idx = self._tool_call_index.get(tc_id)
            if idx is not None:
                tc = self.tool_calls[idx]
                tc["result"] = str(data.get("result", ""))
                tc["status"] = data.get("status", "success")
                if data.get("tool_name") and not tc.get("tool"):
                    tc["tool"] = data["tool_name"]
        elif evt_type == "references":
            for r in data.get("references") or []:
                rid = str(r.get("id") or r.get("url") or len(self.references))
                if rid in self._ref_ids:
                    continue
                self._ref_ids.add(rid)
                self.references.append(r)
        elif evt_type == "reasoning_chunk":
            expert_id = data.get("expert_id") or "__model__"
            chunk = data.get("chunk", "")
            if not chunk:
                return
            idx = self._reasoning_index.get(expert_id)
            if idx is None:
                self._reasoning_index[expert_id] = len(self.reasoning_blocks)
                self.reasoning_blocks.append({
                    "expertId": expert_id,
                    "expertName": data.get("expert_name") or "思考过程",
                    "reasoning": chunk,
                })
            else:
                self.reasoning_blocks[idx]["reasoning"] += chunk
        elif evt_type == "plan_created":
            self.plan = data
        elif evt_type in ("artifact_create", "artifact_delta", "artifact_done", "result"):
            if data.get("artifact_id"):
                self.artifact_id = data["artifact_id"]
        elif evt_type == "sandbox_start":
            call_id = data.get("call_id") or ""
            if call_id and call_id not in self._sandbox_index:
                self._sandbox_index[call_id] = len(self.sandbox_calls)
                self.sandbox_calls.append({
                    "callId": call_id,
                    "language": data.get("language", ""),
                    "codePreview": data.get("code_preview", ""),
                    "timeout": data.get("timeout"),
                    "status": "running",
                    "artifacts": [],
                })
        elif evt_type == "sandbox_artifact":
            idx = self._sandbox_index.get(data.get("call_id") or "")
            if idx is not None and data.get("path"):
                self.sandbox_calls[idx]["artifacts"].append({"path": data["path"]})
        elif evt_type == "sandbox_done":
            idx = self._sandbox_index.get(data.get("call_id") or "")
            if idx is not None:
                sc = self.sandbox_calls[idx]
                sc["status"] = "error" if data.get("exit_code", 0) != 0 else "success"
                sc["exitCode"] = data.get("exit_code", 0)
                sc["durationMs"] = data.get("duration_ms", 0)
                sc["timedOut"] = data.get("timed_out", False)
                sc["stdoutPreview"] = data.get("stdout_preview", "")
                sc["stderrPreview"] = data.get("stderr_preview", "")
                sc["artifactCount"] = data.get("artifact_count", len(sc.get("artifacts", [])))

    @property
    def response_text(self) -> str:
        return "".join(self.response_parts)

    def to_extra(self) -> dict | None:
        """打包成落库的 extra JSON —— 仅带非空字段，None 表示什么都没收到。"""
        extra: dict = {}
        if self.intent:
            extra["intent"] = self.intent
        if self.skill:
            extra["skill"] = self.skill
        if self.tool_calls:
            extra["tool_calls"] = self.tool_calls
        if self.references:
            extra["references"] = self.references
        if self.reasoning_blocks:
            extra["reasoning_blocks"] = self.reasoning_blocks
        if self.plan:
            extra["plan"] = self.plan
        if self.sandbox_calls:
            extra["sandbox_calls"] = self.sandbox_calls
        return extra or None


def _save_messages(
    db,
    session_id: str,
    user_msg: str,
    assistant_msg: str,
    usage: dict | None = None,
    artifact_id: str | None = None,
    extra: dict | None = None,
) -> None:
    """将用户消息和 AI 回复持久化到 MySQL。

    artifact_id: 若本轮生成了 artifact（如 slides），关联到 assistant 消息，
    以便前端在会话重载后仍能从气泡里点击进入 artifact 面板。
    extra: SSE 事件收集器打包的可视化字段（intent/tool_calls/references/...），
    落到 chat_messages.extra；前端 switchSession 从这里恢复。
    """
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
            artifact_id=artifact_id,
            extra=extra,
        ))
    if user_msg or assistant_msg:
        db.commit()


def _record_skill_invocation(
    *,
    skill_id: int | None,
    skill_name: str,
    user_id: str | None,
    session_id: str | None,
    source: str,
    status: str = "success",
    input_tokens: int = 0,
    output_tokens: int = 0,
    duration_ms: int = 0,
    error_message: str | None = None,
) -> None:
    """写 skill_invocations 记录（fire-and-forget）。"""
    from lea_server.models.skill import SkillInvocation

    try:
        inv_db = SessionLocal()
        inv_db.add(SkillInvocation(
            skill_id=skill_id,
            skill_name=skill_name,
            user_id=user_id,
            session_id=session_id,
            source=source,
            status=status,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            duration_ms=duration_ms,
            error_message=error_message,
        ))
        inv_db.commit()
    except Exception:
        pass
    finally:
        inv_db.close()
