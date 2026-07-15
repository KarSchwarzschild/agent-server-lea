"""记忆服务独立入口 — 可单独 uvicorn 启动。

启动方式：
  uvicorn lea_graph.memory.server:app --host 0.0.0.0 --port 8100

或通过 docker-compose 以不同 entrypoint 部署。
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Depends, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from lea_graph.memory.config import get_memory_settings

logger = structlog.get_logger()
settings = get_memory_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("memory_service_starting", collection=settings.milvus_collection)
    yield
    logger.info("memory_service_shutdown")


app = FastAPI(
    title="LEA Memory Service",
    description="长期记忆服务 — 向量化存储 + 语义检索",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Schemas ---

class MemoryAddRequest(BaseModel):
    user_id: str
    content: str
    category: str = "fact"
    importance: int = 5
    tenant_id: str = "default"
    source_session_id: str = ""


class MemorySearchRequest(BaseModel):
    user_id: str
    query: str
    top_k: int = 10
    tenant_id: str = "default"


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
    created_at: str | None = None


class ExtractRequest(BaseModel):
    user_id: str
    messages: list[dict]
    tenant_id: str = "default"
    session_id: str = ""


# --- Endpoints ---

@app.get("/health")
async def health():
    return {"status": "ok", "service": "memory"}


@app.post("/memory/add")
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
    )
    return {"id": memory_id}


@app.post("/memory/search", response_model=list[MemoryOut])
async def search_memories(body: MemorySearchRequest):
    """语义检索用户记忆。"""
    from lea_graph.memory.retriever import MemoryRetriever

    retriever = MemoryRetriever()
    results = await retriever.search(
        user_id=body.user_id,
        query=body.query,
        top_k=body.top_k,
        tenant_id=body.tenant_id,
    )
    return [
        MemoryOut(
            id=m.id, content=m.content, category=m.category,
            importance=m.importance, access_count=m.access_count,
            score=m.score,
            created_at=m.created_at.isoformat() if m.created_at else None,
        )
        for m in results
    ]


@app.get("/memory/list", response_model=list[MemoryOut])
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
            created_at=m.created_at.isoformat() if m.created_at else None,
        )
        for m in results
    ]


@app.put("/memory/{memory_id}")
async def update_memory(memory_id: int, body: MemoryUpdateRequest):
    """更新记忆。"""
    from lea_graph.memory.service import MemoryService

    svc = MemoryService()
    ok = await svc.update(memory_id, content=body.content, category=body.category)
    if not ok:
        raise HTTPException(404, "Memory not found")
    return {"ok": True}


@app.delete("/memory/{memory_id}")
async def delete_memory(memory_id: int):
    """删除记忆。"""
    from lea_graph.memory.service import MemoryService

    svc = MemoryService()
    ok = await svc.delete(memory_id)
    if not ok:
        raise HTTPException(404, "Memory not found")
    return {"ok": True}


@app.delete("/memory/user/{user_id}")
async def delete_all_memories(user_id: str, tenant_id: str = Query("default")):
    """清空用户所有记忆。"""
    from lea_graph.memory.service import MemoryService

    svc = MemoryService()
    count = await svc.delete_all(user_id, tenant_id)
    return {"deleted": count}


@app.post("/memory/extract")
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

    results = await do_extract(messages, body.user_id, body.tenant_id, body.session_id)
    return {"extracted": len(results), "memories": results}
