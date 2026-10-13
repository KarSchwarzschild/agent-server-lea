"""检索服务 — 记忆路由（从 lea_graph.memory.server 迁移）。"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

logger = structlog.get_logger()
router = APIRouter(prefix="/memory", tags=["memory"])


class MemoryAddRequest(BaseModel):
    user_id: str
    content: str
    category: str = "fact"
    importance: int = 5
    tenant_id: str = "default"
    source_session_id: str = ""
    scope: str = "global"


class MemorySearchRequest(BaseModel):
    user_id: str
    query: str
    top_k: int = 10
    tenant_id: str = "default"
    scopes: list[str] | None = None


class MemoryUpdateRequest(BaseModel):
    content: str | None = None
    category: str | None = None


class MemoryOut(BaseModel):
    id: int
    content: str
    category: str
    importance: int
    access_count: int
    score: float = 0.0
    scope: str = "global"
    created_at: str | None = None


class ExtractRequest(BaseModel):
    user_id: str
    messages: list[dict]
    tenant_id: str = "default"
    session_id: str = ""
    agent_id: str = ""


@router.post("/add")
async def add_memory(body: MemoryAddRequest):
    """添加一条记忆。"""
    from lea_rag.memory.service import MemoryService

    svc = MemoryService()
    memory_id = await svc.add(
        user_id=body.user_id,
        content=body.content,
        category=body.category,
        importance=body.importance,
        tenant_id=body.tenant_id,
        source_session_id=body.source_session_id,
        scope=body.scope,
    )
    return {"id": memory_id}


@router.post("/search", response_model=list[MemoryOut])
async def search_memories(body: MemorySearchRequest):
    """语义检索用户记忆。支持 scopes 过滤（默认 global + 指定 agent）。"""
    from lea_rag.memory.retriever import MemoryRetriever

    retriever = MemoryRetriever()
    results = await retriever.search(
        user_id=body.user_id,
        query=body.query,
        top_k=body.top_k,
        tenant_id=body.tenant_id,
        scopes=body.scopes,
    )
    return [
        MemoryOut(
            id=m.id, content=m.content, category=m.category,
            importance=m.importance, access_count=m.access_count,
            score=m.score,
            scope=getattr(m, "scope", "global"),
            created_at=m.created_at.isoformat() if m.created_at else None,
        )
        for m in results
    ]


@router.get("/list", response_model=list[MemoryOut])
async def list_memories(
    user_id: str = Query(...),
    tenant_id: str = Query("default"),
    category: str | None = Query(None),
):
    """获取用户所有记忆。"""
    from lea_rag.memory.service import MemoryService

    svc = MemoryService()
    results = await svc.get_all(user_id, tenant_id, category)
    return [
        MemoryOut(
            id=m.id, content=m.content, category=m.category,
            importance=m.importance, access_count=m.access_count,
            score=m.score,
            scope=getattr(m, "scope", "global"),
            created_at=m.created_at.isoformat() if m.created_at else None,
        )
        for m in results
    ]


@router.put("/{memory_id}")
async def update_memory(memory_id: int, body: MemoryUpdateRequest):
    """更新记忆。"""
    from lea_rag.memory.service import MemoryService

    svc = MemoryService()
    ok = await svc.update(memory_id, content=body.content, category=body.category)
    if not ok:
        raise HTTPException(404, "Memory not found")
    return {"ok": True}


@router.delete("/{memory_id}")
async def delete_memory(memory_id: int):
    """删除记忆。"""
    from lea_rag.memory.service import MemoryService

    svc = MemoryService()
    ok = await svc.delete(memory_id)
    if not ok:
        raise HTTPException(404, "Memory not found")
    return {"ok": True}


@router.delete("/user/{user_id}")
async def delete_all_memories(user_id: str, tenant_id: str = Query("default")):
    """清空用户所有记忆。"""
    from lea_rag.memory.service import MemoryService

    svc = MemoryService()
    count = await svc.delete_all(user_id, tenant_id)
    return {"deleted": count}


@router.post("/extract")
async def extract_memories(body: ExtractRequest):
    """从对话中提取记忆。内置前置过滤规则，跳过无价值 session。"""
    from langchain_core.messages import AIMessage, HumanMessage

    # ── 前置过滤 ──
    user_msgs = [m for m in body.messages if m.get("role") == "user"]
    if len(user_msgs) < 1:
        return {"extracted": 0, "memories": [], "skipped": "too_few_turns"}
    total_chars = sum(len(m.get("content", "")) for m in user_msgs)
    if total_chars < 30:
        return {"extracted": 0, "memories": [], "skipped": "too_short"}

    from lea_rag.memory.extractor import extract_memories as do_extract

    messages = []
    for m in body.messages:
        role = m.get("role", "")
        content = m.get("content", "")
        if role == "user":
            messages.append(HumanMessage(content=content))
        elif role == "assistant":
            messages.append(AIMessage(content=content))

    results = await do_extract(
        messages, body.user_id, body.tenant_id, body.session_id,
        agent_id=body.agent_id,
    )
    return {"extracted": len(results), "memories": results}


# ── 用户画像 ───────────────────────────────────────────────────


class ProfileMergeRequest(BaseModel):
    user_id: str
    agent_id: str = ""
    tenant_id: str = "default"
    session_id: str = ""


@router.get("/profile")
async def get_profile_endpoint(
    user_id: str = Query(...),
    agent_id: str = Query(""),
    tenant_id: str = Query("default"),
):
    """获取用户画像（PreTurn 调用，纯 DB 读取）。"""
    from lea_rag.memory.profile import get_profile

    text = await get_profile(user_id, agent_id, tenant_id)
    return {"profile": text}


@router.post("/profile/merge")
async def merge_profile_endpoint(body: ProfileMergeRequest):
    """SessionEnd 时触发：提取记忆 + merge 画像。"""
    from lea_rag.memory.profile import merge_profile

    text = await merge_profile(
        body.user_id, body.agent_id, body.tenant_id, body.session_id
    )
    return {"profile": text, "updated": bool(text)}


# ── Debug Trace ───────────────────────────────────────────────


class DebugMemoryTraceRequest(BaseModel):
    user_id: str
    agent_id: str = ""
    tenant_id: str = "default"
    message: str = "你好，我是一名大二学生，正在学微积分"
    assistant_reply: str = "你好！微积分是数学的重要分支，我可以帮你理解极限、导数和积分的概念。"


@router.post("/debug/trace")
async def debug_memory_trace(body: DebugMemoryTraceRequest):
    """完整记忆流程 trace：PreTurn(读画像+搜记忆) → Extract → Merge。

    一个 curl 看到整个记忆系统执行过程，无需 Go 引擎。
    """
    import time as _time

    from lea_rag.memory.profile import get_profile, merge_profile
    from lea_rag.memory.retriever import MemoryRetriever

    trace = []

    # ── Phase 1: PreTurn — 读全局画像 ──
    t0 = _time.time()
    global_profile = await get_profile(body.user_id, "", body.tenant_id)
    trace.append({
        "phase": "pre_turn", "action": "get_global_profile",
        "result": global_profile or "(empty)",
        "ms": int((_time.time() - t0) * 1000),
    })

    # ── Phase 1b: PreTurn — 读 agent 画像 ──
    t1 = _time.time()
    agent_profile = await get_profile(body.user_id, body.agent_id, body.tenant_id)
    trace.append({
        "phase": "pre_turn", "action": "get_agent_profile",
        "agent_id": body.agent_id,
        "result": agent_profile or "(empty)",
        "ms": int((_time.time() - t1) * 1000),
    })

    # ── Phase 1c: PreTurn — 搜相关记忆 ──
    t2 = _time.time()
    try:
        retriever = MemoryRetriever()
        scopes = ["global"]
        if body.agent_id:
            scopes.append(f"agent:{body.agent_id}")
        memories = await retriever.search(
            user_id=body.user_id, query=body.message,
            top_k=5, tenant_id=body.tenant_id, scopes=scopes,
        )
        trace.append({
            "phase": "pre_turn", "action": "search_memories",
            "query": body.message,
            "results": [{"content": m.content, "score": round(m.score, 3)} for m in memories],
            "ms": int((_time.time() - t2) * 1000),
        })
    except Exception as e:
        trace.append({
            "phase": "pre_turn", "action": "search_memories",
            "error": str(e),
            "ms": int((_time.time() - t2) * 1000),
        })

    # ── Phase 2: SessionEnd — Extract memories ──
    t3 = _time.time()
    session_id = f"debug-{int(_time.time())}"

    from langchain_core.messages import AIMessage, HumanMessage

    from lea_rag.memory.extractor import extract_memories as do_extract

    lc_messages = [
        HumanMessage(content=body.message),
        AIMessage(content=body.assistant_reply),
    ]
    try:
        extracted = await do_extract(
            lc_messages, body.user_id, body.tenant_id, session_id,
            agent_id=body.agent_id,
        )
        trace.append({
            "phase": "session_end", "action": "extract_memories",
            "extracted": extracted,
            "count": len(extracted),
            "ms": int((_time.time() - t3) * 1000),
        })
    except Exception as e:
        extracted = []
        trace.append({
            "phase": "session_end", "action": "extract_memories",
            "error": str(e),
            "ms": int((_time.time() - t3) * 1000),
        })

    # ── Phase 3: SessionEnd — Merge profiles ──
    t4 = _time.time()
    global_new = await merge_profile(body.user_id, "", body.tenant_id, session_id)
    trace.append({
        "phase": "session_end", "action": "merge_global_profile",
        "result": global_new or "(no change)",
        "ms": int((_time.time() - t4) * 1000),
    })

    if body.agent_id:
        t5 = _time.time()
        agent_new = await merge_profile(body.user_id, body.agent_id, body.tenant_id, session_id)
        trace.append({
            "phase": "session_end", "action": "merge_agent_profile",
            "agent_id": body.agent_id,
            "result": agent_new or "(no change)",
            "ms": int((_time.time() - t5) * 1000),
        })

    return {"trace": trace}
