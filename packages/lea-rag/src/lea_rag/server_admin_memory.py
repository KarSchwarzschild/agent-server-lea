"""Admin 记忆管理 API — lea-rag 内直接实现。

- /admin/memories        查看指定用户记忆 / 删除
- /admin/memory-configs  记忆策略 CRUD（租户/agent 粒度）

浏览器不直连这里 —— 经 lea-agent 反代（它用验过的 JWT 覆盖 X-User-ID/X-Tenant-ID）。

⚠️ 每个端点都要**两道**检查，缺一不可：

    角色门（本文件用 router 级依赖统一挂）    挡同租户里的普通用户
    filter(tenant_id == actor.tenant_id)     挡跨租户

只有后者的话，普通用户能删同租户里别人的记忆和改租户的记忆策略。
这个文件一度就是那样。
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from lea_authkit import Actor, require_actor, require_admin
from lea_rag.db.database import get_db
from lea_rag.db.models import MemoryConfig, UserMemory


async def _admin_only(actor: Actor = Depends(require_actor)) -> None:
    """router 级的角色门。

    挂在 APIRouter(dependencies=...) 上而不是逐个端点写 require_admin(actor)：
    这个 router 有 6 个端点，将来还会加，而**漏掉一个 if 不会有任何症状** ——
    那个端点照样工作，只是对普通用户也开放。router 级依赖对新端点自动生效。

    各端点仍然自己 Depends(require_actor) 拿 actor 做租户过滤。同一个依赖被求值
    两次没有代价：FastAPI 在单次请求内缓存依赖结果（use_cache 默认 True），
    所以 header 只解析一遍。
    """
    require_admin(actor)


router = APIRouter(
    prefix="/admin",
    tags=["admin-memories"],
    dependencies=[Depends(_admin_only)],
)


# ── Schemas ─────────────────────────────────────────────

class MemoryConfigCreate(BaseModel):
    agent_id: Optional[int] = None
    enabled: bool = True
    max_memories: int = 200
    extract_enabled: bool = True
    categories: list[str] = ["fact", "preference", "identity", "progress"]


class MemoryConfigUpdate(BaseModel):
    enabled: Optional[bool] = None
    max_memories: Optional[int] = None
    extract_enabled: Optional[bool] = None
    categories: Optional[list[str]] = None


def _mem_to_dict(m: UserMemory) -> dict:
    return {
        "id": m.id,
        "user_id": m.user_id,
        "tenant_id": m.tenant_id,
        "content": m.content,
        "category": m.category,
        "scope": m.scope,
        "importance": m.importance,
        "access_count": m.access_count,
        "embedding_synced": m.embedding_synced,
        "source_session_id": m.source_session_id,
        "created_at": m.created_at.isoformat() if m.created_at else None,
        "last_accessed_at": m.last_accessed_at.isoformat() if m.last_accessed_at else None,
    }


def _cfg_to_dict(c: MemoryConfig) -> dict:
    return {
        "id": c.id,
        "tenant_id": c.tenant_id,
        "agent_id": c.agent_id,
        "enabled": c.enabled,
        "max_memories": c.max_memories,
        "extract_enabled": c.extract_enabled,
        "categories": c.categories,
        "created_at": c.created_at.isoformat() if c.created_at else None,
        "updated_at": c.updated_at.isoformat() if c.updated_at else None,
    }


# ── /admin/memories ─────────────────────────────────────

@router.get("/memories")
def list_memories(
    user_id: Optional[str] = Query(None, description="不传则查本租户全部用户"),
    category: Optional[str] = None,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    actor: Actor = Depends(require_actor),
    db: Session = Depends(get_db),
):
    """Admin 查看本租户记忆。tenant_id 从 actor 取，不能跨租户。"""
    q = db.query(UserMemory).filter(UserMemory.tenant_id == actor.tenant_id)
    if user_id:
        q = q.filter(UserMemory.user_id == user_id)
    if category:
        q = q.filter(UserMemory.category == category)
    total = q.count()
    rows = q.order_by(UserMemory.created_at.desc()).offset((page - 1) * size).limit(size).all()
    return {
        "total": total,
        "page": page,
        "size": size,
        "items": [_mem_to_dict(m) for m in rows],
    }


@router.delete("/memories/{memory_id}")
def delete_memory(
    memory_id: int,
    actor: Actor = Depends(require_actor),
    db: Session = Depends(get_db),
):
    m = db.query(UserMemory).filter(
        UserMemory.id == memory_id,
        UserMemory.tenant_id == actor.tenant_id,
    ).first()
    if not m:
        raise HTTPException(404, "Memory not found")
    db.delete(m)
    db.commit()
    return {"ok": True}


# ── /admin/memory-configs ───────────────────────────────

@router.get("/memory-configs")
def list_configs(
    actor: Actor = Depends(require_actor),
    db: Session = Depends(get_db),
):
    rows = db.query(MemoryConfig).filter(
        MemoryConfig.tenant_id == actor.tenant_id,
    ).order_by(MemoryConfig.id).all()
    return {"items": [_cfg_to_dict(c) for c in rows]}


@router.post("/memory-configs", status_code=201)
def create_config(
    body: MemoryConfigCreate,
    actor: Actor = Depends(require_actor),
    db: Session = Depends(get_db),
):
    cfg = MemoryConfig(tenant_id=actor.tenant_id, **body.model_dump())
    db.add(cfg)
    db.commit()
    db.refresh(cfg)
    return _cfg_to_dict(cfg)


@router.put("/memory-configs/{config_id}")
def update_config(
    config_id: int,
    body: MemoryConfigUpdate,
    actor: Actor = Depends(require_actor),
    db: Session = Depends(get_db),
):
    cfg = db.query(MemoryConfig).filter(
        MemoryConfig.id == config_id,
        MemoryConfig.tenant_id == actor.tenant_id,
    ).first()
    if not cfg:
        raise HTTPException(404, "Config not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(cfg, k, v)
    db.commit()
    db.refresh(cfg)
    return _cfg_to_dict(cfg)


@router.delete("/memory-configs/{config_id}", status_code=204)
def delete_config(
    config_id: int,
    actor: Actor = Depends(require_actor),
    db: Session = Depends(get_db),
):
    cfg = db.query(MemoryConfig).filter(
        MemoryConfig.id == config_id,
        MemoryConfig.tenant_id == actor.tenant_id,
    ).first()
    if not cfg:
        raise HTTPException(404, "Config not found")
    db.delete(cfg)
    db.commit()
