"""Admin 知识库管理 API — 管理员查看/管理所有知识库。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from lea_server.core.deps import admin_required
from lea_server.database import get_db
from lea_server.models.knowledge import KnowledgeBase
from lea_server.models.models import User
from lea_server.services.kb_service import KBService

router = APIRouter(prefix="/admin/knowledge-bases", tags=["admin-knowledge"])


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
        "created_at": kb.created_at.isoformat() if kb.created_at else None,
    }


@router.get("")
async def list_all_knowledge_bases(
    page: int = 1,
    size: int = 50,
    db: Session = Depends(get_db),
    _: User = Depends(admin_required),
):
    """管理员查看所有知识库。"""
    svc = KBService(db)
    kbs, total = svc.list_all_kbs(page=page, size=size)
    return {"knowledge_bases": [_kb_to_dict(kb) for kb in kbs], "total": total, "page": page, "size": size}


@router.put("/{kb_id}")
async def admin_update_kb(
    kb_id: int,
    data: dict,
    db: Session = Depends(get_db),
    _: User = Depends(admin_required),
):
    """管理员修改知识库配置（含 enabled/visibility）。"""
    svc = KBService(db)
    kb = svc.get_kb(kb_id)
    if kb is None:
        raise HTTPException(status_code=404, detail="知识库不存在")
    kb = svc.update_kb(kb_id, data)
    db.commit()
    return _kb_to_dict(kb)


@router.delete("/{kb_id}")
async def admin_delete_kb(
    kb_id: int,
    db: Session = Depends(get_db),
    _: User = Depends(admin_required),
):
    """管理员删除知识库（级联删除文档+分块+向量）。"""
    svc = KBService(db)
    if not svc.delete_kb(kb_id):
        raise HTTPException(status_code=404, detail="知识库不存在")
    db.commit()
    return {"message": "已删除"}
