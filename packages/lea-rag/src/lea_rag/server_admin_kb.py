"""Admin 知识库管理 API — lea-rag 内直接实现。

浏览器不直连这里 —— 经 lea-agent 反代（它用验过的 JWT 覆盖 X-User-ID/X-Tenant-ID）。

⚠️ 每个端点都要**两道**检查，缺一不可：

    require_admin(actor)                       挡同租户里的普通用户
    filter(tenant_id == actor.tenant_id)       挡跨租户

只有后者的话，普通用户 alice 可以 DELETE 掉同租户里 bob 的知识库 —— 这个文件
一度就是那样（import 了 ROLE_ADMIN 但一次没用）。
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from lea_authkit import Actor, require_actor, require_admin
from lea_rag.db.database import get_db
from lea_rag.db.models import KnowledgeBase
from lea_rag.services.kb_service import KBService

router = APIRouter(prefix="/admin/knowledge-bases", tags=["admin-knowledge"])


class KBUpdateRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    enabled: Optional[bool] = None
    visibility: Optional[str] = None
    retrieval_mode: Optional[str] = None
    retrieval_top_k: Optional[int] = None
    score_threshold: Optional[float] = None
    embedding_model: Optional[str] = None
    reranker_model: Optional[str] = None
    reranker_enabled: Optional[bool] = None
    vlm_model: Optional[str] = None
    vlm_enabled: Optional[bool] = None
    multimodal: Optional[bool] = None


def _kb_to_dict(kb: KnowledgeBase) -> dict:
    return {
        "id": kb.id,
        "name": kb.name,
        "description": kb.description or "",
        "owner": kb.owner,
        "tenant_id": kb.tenant_id,
        "visibility": kb.visibility or "private",
        "document_count": kb.document_count or 0,
        "word_count": kb.word_count or 0,
        "enabled": kb.enabled,
        "retrieval_mode": kb.retrieval_mode or "hybrid",
        "retrieval_top_k": kb.retrieval_top_k or 5,
        "score_threshold": kb.score_threshold or 0.5,
        "embedding_model": kb.embedding_model or "",
        "reranker_model": kb.reranker_model or "",
        "reranker_enabled": kb.reranker_enabled,
        "vlm_model": kb.vlm_model or "",
        "vlm_enabled": kb.vlm_enabled,
        "multimodal": kb.multimodal,
        "created_at": kb.created_at.isoformat() if kb.created_at else None,
    }


@router.get("")
def list_all_kbs(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    enabled: Optional[bool] = None,
    search: Optional[str] = None,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    """管理员查看本租户所有 KB。tenant_id 从 actor 取，不能跨租户查。"""
    require_admin(actor)
    q = db.query(KnowledgeBase).filter(KnowledgeBase.tenant_id == actor.tenant_id)
    if enabled is not None:
        q = q.filter(KnowledgeBase.enabled == enabled)
    if search:
        like = f"%{search}%"
        q = q.filter((KnowledgeBase.name.ilike(like)) | (KnowledgeBase.description.ilike(like)))
    total = q.count()
    rows = q.order_by(KnowledgeBase.id.desc()).offset((page - 1) * size).limit(size).all()
    return {
        "total": total,
        "page": page,
        "size": size,
        "items": [_kb_to_dict(kb) for kb in rows],
    }


@router.get("/{kb_id}")
def get_kb(kb_id: int, actor: Actor = Depends(require_actor), db: Session = Depends(get_db)):
    require_admin(actor)
    kb = db.query(KnowledgeBase).filter(
        KnowledgeBase.id == kb_id,
        KnowledgeBase.tenant_id == actor.tenant_id,
    ).first()
    if not kb:
        raise HTTPException(404, "Knowledge base not found")
    return _kb_to_dict(kb)


@router.put("/{kb_id}")
def update_kb(kb_id: int, body: KBUpdateRequest, actor: Actor = Depends(require_actor), db: Session = Depends(get_db)):
    require_admin(actor)
    kb = db.query(KnowledgeBase).filter(
        KnowledgeBase.id == kb_id,
        KnowledgeBase.tenant_id == actor.tenant_id,
    ).first()
    if not kb:
        raise HTTPException(404, "Knowledge base not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(kb, k, v)
    db.commit()
    db.refresh(kb)
    return _kb_to_dict(kb)


@router.delete("/{kb_id}")
def delete_kb(kb_id: int, actor: Actor = Depends(require_actor), db: Session = Depends(get_db)):
    """管理员删除本租户 KB（级联删文档 + 分块 + Milvus 向量）。"""
    require_admin(actor)
    kb = db.query(KnowledgeBase).filter(
        KnowledgeBase.id == kb_id,
        KnowledgeBase.tenant_id == actor.tenant_id,
    ).first()
    if not kb:
        raise HTTPException(404, "Knowledge base not found")
    svc = KBService(db)
    svc.delete_kb(kb_id)
    db.commit()
    return {"message": "deleted", "kb_id": kb_id}
