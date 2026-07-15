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


@router.post("/add")
async def add_memory(body: MemoryAddRequest):
    """添加一条记忆。"""
    from lea_graph.memory.service import MemoryService

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
    from lea_graph.memory.retriever import MemoryRetriever

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
    from lea_graph.memory.service import MemoryService

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
    from lea_graph.memory.service import MemoryService

    svc = MemoryService()
    ok = await svc.update(memory_id, content=body.content, category=body.category)
    if not ok:
        raise HTTPException(404, "Memory not found")
    return {"ok": True}


@router.delete("/{memory_id}")
async def delete_memory(memory_id: int):
    """删除记忆。"""
    from lea_graph.memory.service import MemoryService

    svc = MemoryService()
    ok = await svc.delete(memory_id)
    if not ok:
        raise HTTPException(404, "Memory not found")
    return {"ok": True}


@router.delete("/user/{user_id}")
async def delete_all_memories(user_id: str, tenant_id: str = Query("default")):
    """清空用户所有记忆。"""
    from lea_graph.memory.service import MemoryService

    svc = MemoryService()
    count = await svc.delete_all(user_id, tenant_id)
    return {"deleted": count}


@router.post("/extract")
async def extract_memories(body: ExtractRequest):
    """从对话中提取记忆。"""
    from langchain_core.messages import AIMessage, HumanMessage
    from lea_graph.memory.extractor import extract_memories as do_extract

    messages = []
    for m in body.messages:
        role = m.get("role", "")
        content = m.get("content", "")
        if role == "user":
            messages.append(HumanMessage(content=content))
        elif role == "assistant":
            messages.append(AIMessage(content=content))

    results = await do_extract(
        messages, body.user_id, body.tenant_id, body.session_id
    )
    return {"extracted": len(results), "memories": results}
