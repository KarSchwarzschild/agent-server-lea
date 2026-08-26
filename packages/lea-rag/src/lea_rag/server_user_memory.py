"""用户视角 Memories 路由 —— 方案 Y：从 lea-server 迁过来。

前端直连 :8300/memories/*。契约与 lea-server 原 memories.py 兼容。
Auth 走 lea-authkit 的 require_actor：缺头 401，无服务间鉴权 503。

UserMemory 没有 owner_scope/owner_user_id 列（用 user_id + tenant_id 做归属），
所以 scoped_filter 不适用，用手动 tenant_id + user_id 过滤。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from lea_authkit import Actor, require_actor
from lea_rag.db.database import get_db
from lea_rag.db.models import UserMemory

router = APIRouter(tags=["user-memories"])


class MemoryOut(BaseModel):
    id: int
    content: str
    category: str
    importance: int
    access_count: int
    created_at: str | None = None


class MemoryUpdate(BaseModel):
    content: str | None = None
    category: str | None = None


@router.get("/memories", response_model=list[MemoryOut])
def list_memories(
    category: str | None = Query(None),
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
    rows = q.order_by(UserMemory.created_at.desc()).all()
    return [
        MemoryOut(
            id=r.id,
            content=r.content,
            category=r.category or "fact",
            importance=r.importance or 5,
            access_count=r.access_count or 0,
            created_at=r.created_at.isoformat() if r.created_at else None,
        )
        for r in rows
    ]


@router.put("/memories/{memory_id}")
def update_memory(
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

    if body.content is not None:
        mem.content = body.content
        mem.embedding_synced = False
    if body.category is not None:
        mem.category = body.category
    db.commit()
    return {"ok": True}


@router.delete("/memories/{memory_id}")
def delete_memory(
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
    db.delete(mem)
    db.commit()
    return {"ok": True}


@router.delete("/memories")
def delete_all_memories(
    actor: Actor = Depends(require_actor),
    db: Session = Depends(get_db),
):
    """清空当前用户所有记忆。"""
    count = (
        db.query(UserMemory)
        .filter(
            UserMemory.tenant_id == actor.tenant_id,
            UserMemory.user_id == actor.user_id,
        )
        .delete()
    )
    db.commit()
    return {"deleted": count}
