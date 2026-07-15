"""用户端记忆 API — 查看/编辑/删除自己的长期记忆。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from lea_server.core.deps import get_current_user, get_db

router = APIRouter(tags=["memories"])


class MemoryOut(BaseModel):
    id: int
    content: str
    category: str
    importance: int
    access_count: int
    created_at: str | None = None

    class Config:
        from_attributes = True


class MemoryUpdate(BaseModel):
    content: str | None = None
    category: str | None = None


@router.get("/memories", response_model=list[MemoryOut])
async def list_memories(
    category: str | None = Query(None),
    user=Depends(get_current_user),
    db=Depends(get_db),
):
    """列出当前用户的所有记忆。"""
    from lea_server.models.chat_session import UserMemory

    q = db.query(UserMemory).filter(UserMemory.user_id == user.user_id)
    if category:
        q = q.filter(UserMemory.category == category)
    rows = q.order_by(UserMemory.created_at.desc()).all()
    return [
        MemoryOut(
            id=r.id, content=r.content, category=r.category or "fact",
            importance=r.importance or 5, access_count=r.access_count or 0,
            created_at=r.created_at.isoformat() if r.created_at else None,
        )
        for r in rows
    ]


@router.put("/memories/{memory_id}")
async def update_memory(
    memory_id: int,
    body: MemoryUpdate,
    user=Depends(get_current_user),
    db=Depends(get_db),
):
    """编辑一条记忆。"""
    from lea_server.models.chat_session import UserMemory

    mem = db.query(UserMemory).filter(
        UserMemory.id == memory_id,
        UserMemory.user_id == user.user_id,
    ).first()
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
async def delete_memory(
    memory_id: int,
    user=Depends(get_current_user),
    db=Depends(get_db),
):
    """删除一条记忆。"""
    from lea_server.models.chat_session import UserMemory

    mem = db.query(UserMemory).filter(
        UserMemory.id == memory_id,
        UserMemory.user_id == user.user_id,
    ).first()
    if not mem:
        raise HTTPException(404, "Memory not found")
    db.delete(mem)
    db.commit()
    return {"ok": True}


@router.delete("/memories")
async def delete_all_memories(
    user=Depends(get_current_user),
    db=Depends(get_db),
):
    """清空当前用户所有记忆。"""
    from lea_server.models.chat_session import UserMemory

    count = db.query(UserMemory).filter(
        UserMemory.user_id == user.user_id,
    ).delete()
    db.commit()
    return {"deleted": count}
