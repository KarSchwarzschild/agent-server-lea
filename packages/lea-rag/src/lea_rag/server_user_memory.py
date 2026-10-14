"""用户视角 Memories 路由 —— 方案 Y：从 lea-server 迁过来。

前端直连 :8300/memories/*。契约与 lea-server 原 memories.py 兼容。
Auth 走 lea-authkit 的 require_actor：缺头 401，无服务间鉴权 503。

UserMemory 没有 owner_scope/owner_user_id 列（用 user_id + tenant_id 做归属），
所以 scoped_filter 不适用，用手动 tenant_id + user_id 过滤。
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from lea_authkit import Actor, require_actor
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from lea_rag.db.database import get_db
from lea_rag.db.models import UserMemory

router = APIRouter(tags=["user-memories"])


class MemoryOut(BaseModel):
    id: int
    content: str
    category: str
    importance: int
    access_count: int
    scope: str
    created_at: str | None = None


MemoryCategory = Literal["fact", "preference", "identity", "progress"]


class MemoryCreate(BaseModel):
    content: str = Field(min_length=1, max_length=4000)
    category: MemoryCategory = "fact"
    importance: int = Field(default=5, ge=1, le=10)
    scope: str = Field(default="global", pattern=r"^(global|agent:[1-9]\d*)$")


class MemoryUpdate(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=4000)
    category: MemoryCategory | None = None
    importance: int | None = Field(default=None, ge=1, le=10)
    scope: str | None = Field(default=None, pattern=r"^(global|agent:[1-9]\d*)$")


@router.get("/memories", response_model=list[MemoryOut])
def list_memories(
    category: str | None = Query(None),
    limit: int = Query(200, ge=1, le=500),
    actor: Actor = Depends(require_actor),
    db: Session = Depends(get_db),
):
    """列出当前用户所有记忆。tenant_id + user_id 双约束。"""
    q = db.query(UserMemory).filter(
        UserMemory.tenant_id == actor.tenant_id,
        UserMemory.user_id == actor.user_id,
    )
    if category:
        q = q.filter(UserMemory.category == category)
    rows = q.order_by(UserMemory.created_at.desc()).limit(limit).all()
    return [
        MemoryOut(
            id=r.id,
            content=r.content,
            category=r.category or "fact",
            importance=r.importance or 5,
            access_count=r.access_count or 0,
            scope=r.scope or "global",
            created_at=r.created_at.isoformat() if r.created_at else None,
        )
        for r in rows
    ]


@router.post("/memories", status_code=status.HTTP_201_CREATED)
async def create_memory(
    body: MemoryCreate,
    actor: Actor = Depends(require_actor),
):
    """新增当前用户的记忆；身份只取可信请求头，不接受前端指定。"""
    content = body.content.strip()
    if not content:
        raise HTTPException(422, "Memory content cannot be empty")

    from lea_rag.memory.service import MemoryService

    memory_id = await MemoryService().add(
        user_id=actor.user_id,
        tenant_id=actor.tenant_id,
        content=content,
        category=body.category,
        importance=body.importance,
        scope=body.scope,
    )
    return {"id": memory_id, "scope": body.scope}


@router.put("/memories/{memory_id}")
async def update_memory(
    memory_id: int,
    body: MemoryUpdate,
    actor: Actor = Depends(require_actor),
    db: Session = Depends(get_db),
):
    mem = (
        db.query(UserMemory)
        .filter(
            UserMemory.id == memory_id,
            UserMemory.tenant_id == actor.tenant_id,
            UserMemory.user_id == actor.user_id,
        )
        .first()
    )
    if not mem:
        raise HTTPException(404, "Memory not found")

    content = body.content.strip() if body.content is not None else None
    if body.content is not None and not content:
        raise HTTPException(422, "Memory content cannot be empty")

    from lea_rag.memory.service import MemoryService

    ok = await MemoryService().update(
        memory_id,
        content=content,
        category=body.category,
        importance=body.importance,
        scope=body.scope,
        user_id=actor.user_id,
        tenant_id=actor.tenant_id,
    )
    if not ok:
        raise HTTPException(404, "Memory not found")
    return {"ok": True}


@router.delete("/memories/{memory_id}")
async def delete_memory(
    memory_id: int,
    actor: Actor = Depends(require_actor),
    db: Session = Depends(get_db),
):
    mem = (
        db.query(UserMemory)
        .filter(
            UserMemory.id == memory_id,
            UserMemory.tenant_id == actor.tenant_id,
            UserMemory.user_id == actor.user_id,
        )
        .first()
    )
    if not mem:
        raise HTTPException(404, "Memory not found")
    from lea_rag.memory.service import MemoryService

    ok = await MemoryService().delete(
        memory_id,
        user_id=actor.user_id,
        tenant_id=actor.tenant_id,
    )
    if not ok:
        raise HTTPException(404, "Memory not found")
    return {"ok": True}


@router.delete("/memories")
async def delete_all_memories(
    actor: Actor = Depends(require_actor),
):
    """清空当前用户所有记忆。"""
    from lea_rag.memory.service import MemoryService

    count = await MemoryService().delete_all(actor.user_id, actor.tenant_id)
    return {"deleted": count}
