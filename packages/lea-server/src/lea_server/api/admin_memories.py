"""Admin 记忆管理 API — 查看用户记忆 + 配置管理。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from lea_server.core.deps import admin_required, get_db

router = APIRouter(prefix="/admin", tags=["admin-memories"])


class MemoryConfigCreate(BaseModel):
    agent_id: int | None = None
    enabled: bool = True
    max_memories: int = 200
    extract_enabled: bool = True
    categories: list[str] = ["fact", "preference", "identity", "progress"]


class MemoryConfigUpdate(BaseModel):
    enabled: bool | None = None
    max_memories: int | None = None
    extract_enabled: bool | None = None
    categories: list[str] | None = None


@router.get("/memories", dependencies=[Depends(admin_required)])
async def admin_list_memories(
    user_id: str = Query(...),
    db=Depends(get_db),
):
    """Admin 查看指定用户的记忆。"""
    from lea_server.models.chat_session import UserMemory

    rows = db.query(UserMemory).filter(
        UserMemory.user_id == user_id,
    ).order_by(UserMemory.created_at.desc()).all()
    return [
        {
            "id": r.id, "content": r.content, "category": r.category,
            "importance": r.importance, "access_count": r.access_count,
            "embedding_synced": r.embedding_synced,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in rows
    ]


@router.delete("/memories/{memory_id}", dependencies=[Depends(admin_required)])
async def admin_delete_memory(memory_id: int, db=Depends(get_db)):
    """Admin 删除指定记忆。"""
    from lea_server.models.chat_session import UserMemory

    mem = db.query(UserMemory).filter(UserMemory.id == memory_id).first()
    if not mem:
        raise HTTPException(404, "Memory not found")
    db.delete(mem)
    db.commit()
    return {"ok": True}


@router.get("/memory-configs", dependencies=[Depends(admin_required)])
async def list_memory_configs(db=Depends(get_db)):
    """列出记忆配置。"""
    from lea_server.models.memory_config import MemoryConfig

    rows = db.query(MemoryConfig).all()
    return [
        {
            "id": r.id, "tenant_id": r.tenant_id, "agent_id": r.agent_id,
            "enabled": r.enabled, "max_memories": r.max_memories,
            "extract_enabled": r.extract_enabled, "categories": r.categories,
        }
        for r in rows
    ]


@router.post("/memory-configs", dependencies=[Depends(admin_required)])
async def create_memory_config(body: MemoryConfigCreate, db=Depends(get_db)):
    """创建记忆配置。"""
    from lea_server.models.memory_config import MemoryConfig

    config = MemoryConfig(
        agent_id=body.agent_id,
        enabled=body.enabled,
        max_memories=body.max_memories,
        extract_enabled=body.extract_enabled,
        categories=body.categories,
    )
    db.add(config)
    db.commit()
    db.refresh(config)
    return {"id": config.id}


@router.put("/memory-configs/{config_id}", dependencies=[Depends(admin_required)])
async def update_memory_config(config_id: int, body: MemoryConfigUpdate, db=Depends(get_db)):
    """更新记忆配置。"""
    from lea_server.models.memory_config import MemoryConfig

    config = db.query(MemoryConfig).filter(MemoryConfig.id == config_id).first()
    if not config:
        raise HTTPException(404, "Config not found")
    if body.enabled is not None:
        config.enabled = body.enabled
    if body.max_memories is not None:
        config.max_memories = body.max_memories
    if body.extract_enabled is not None:
        config.extract_enabled = body.extract_enabled
    if body.categories is not None:
        config.categories = body.categories
    db.commit()
    return {"ok": True}
