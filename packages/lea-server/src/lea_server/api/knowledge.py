"""知识库用户 API — CRUD + 文档上传 + 检索。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from lea_server.core.deps import get_current_user, get_current_tenant
from lea_server.core.tenant import TenantContext
from lea_server.database import get_db
from lea_server.models.knowledge import Document, KnowledgeBase
from lea_server.models.models import User
from lea_server.services.kb_service import KBService

router = APIRouter(prefix="/knowledge-bases", tags=["knowledge"])


def _kb_to_dict(kb: KnowledgeBase) -> dict:
    return {
        "id": kb.id,
        "name": kb.name,
        "description": kb.description or "",
        "embedding_model": kb.embedding_model or "bge-m3",
        "retrieval_mode": kb.retrieval_mode or "hybrid",
        "retrieval_top_k": kb.retrieval_top_k or 5,
        "score_threshold": kb.score_threshold or 0.5,
        "owner": kb.owner,
        "visibility": kb.visibility or "private",
        "document_count": kb.document_count or 0,
        "word_count": kb.word_count or 0,
        "enabled": kb.enabled,
        "created_at": kb.created_at.isoformat() if kb.created_at else None,
        "updated_at": kb.updated_at.isoformat() if kb.updated_at else None,
    }


def _doc_to_dict(doc: Document) -> dict:
    return {
        "id": doc.id,
        "kb_id": doc.kb_id,
        "filename": doc.filename,
        "file_type": doc.file_type or "pdf",
        "file_size": doc.file_size or 0,
        "total_pages": doc.total_pages or 0,
        "status": doc.status or "processing",
        "error_message": doc.error_message or "",
        "summary": doc.summary or "",
        "chunk_count": doc.chunk_count or 0,
        "word_count": doc.word_count or 0,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
    }


# ── 知识库 CRUD ──────────────────────────────────────


class KBCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    visibility: str = "private"
    retrieval_mode: str = "hybrid"
    retrieval_top_k: int = 5
    score_threshold: float = 0.5


class KBUpdateRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    visibility: str | None = None
    retrieval_mode: str | None = None
    retrieval_top_k: int | None = None
    score_threshold: float | None = None
    enabled: bool | None = None


@router.get("")
async def list_knowledge_bases(
    page: int = 1,
    size: int = 50,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    tenant: TenantContext = Depends(get_current_tenant),
):
    svc = KBService(db)
    kbs, total = svc.list_kbs(user_id=user.user_id, tenant_id=tenant.tenant_id, page=page, size=size)
    return {"knowledge_bases": [_kb_to_dict(kb) for kb in kbs], "total": total, "page": page, "size": size}


@router.post("")
async def create_knowledge_base(
    req: KBCreateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    tenant: TenantContext = Depends(get_current_tenant),
):
    svc = KBService(db)
    kb = svc.create_kb(req.model_dump(), owner=user.user_id, tenant_id=tenant.tenant_id)
    db.commit()
    return _kb_to_dict(kb)


@router.get("/{kb_id}")
async def get_knowledge_base(
    kb_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    kb = KBService(db).get_kb(kb_id)
    if kb is None:
        raise HTTPException(status_code=404, detail="知识库不存在")
    return _kb_to_dict(kb)


@router.put("/{kb_id}")
async def update_knowledge_base(
    kb_id: int,
    req: KBUpdateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    svc = KBService(db)
    kb = svc.get_kb(kb_id)
    if kb is None:
        raise HTTPException(status_code=404, detail="知识库不存在")
    if kb.owner != user.user_id:
        raise HTTPException(status_code=403, detail="只能修改自己创建的知识库")
    kb = svc.update_kb(kb_id, req.model_dump(exclude_none=True))
    db.commit()
    return _kb_to_dict(kb)


@router.delete("/{kb_id}")
async def delete_knowledge_base(
    kb_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    svc = KBService(db)
    kb = svc.get_kb(kb_id)
    if kb is None:
        raise HTTPException(status_code=404, detail="知识库不存在")
    if kb.owner != user.user_id:
        raise HTTPException(status_code=403, detail="只能删除自己创建的知识库")
    svc.delete_kb(kb_id)
    db.commit()
    return {"message": "已删除"}


# ── 文档管理 ──────────────────────────────────────────


@router.get("/{kb_id}/documents")
async def list_documents(
    kb_id: int,
    page: int = 1,
    size: int = 50,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    svc = KBService(db)
    if svc.get_kb(kb_id) is None:
        raise HTTPException(status_code=404, detail="知识库不存在")
    docs, total = svc.list_documents(kb_id, page=page, size=size)
    return {"documents": [_doc_to_dict(d) for d in docs], "total": total, "page": page, "size": size}


@router.post("/{kb_id}/documents")
async def upload_document(
    kb_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
    tenant: TenantContext = Depends(get_current_tenant),
):
    svc = KBService(db)
    kb = svc.get_kb(kb_id)
    if kb is None:
        raise HTTPException(status_code=404, detail="知识库不存在")

    file_ext = (file.filename or "file").rsplit(".", 1)[-1].lower()
    if file_ext not in ("pdf", "txt", "md", "docx"):
        raise HTTPException(status_code=400, detail=f"不支持的文件类型: {file_ext}")

    content = await file.read()
    file_key = f"kb/{kb_id}/{file.filename}"

    try:
        from lea_server.services.object_storage import ObjectStorage
        storage = ObjectStorage()
        storage.put_object(file_key, content, content_type=file.content_type or "application/octet-stream")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"文件上传失败: {exc}")

    doc = svc.create_document(kb_id, {
        "filename": file.filename or "unknown",
        "file_key": file_key,
        "file_type": file_ext,
        "file_size": len(content),
    }, tenant_id=tenant.tenant_id)
    db.commit()

    try:
        from lea_server.tasks.celery_app import app as celery_app
        celery_app.send_task(
            "lea_server.tasks.kb_process",
            args=[doc.id],
        )
    except Exception as exc:
        svc.update_document_status(doc.id, "error", error_message=f"任务提交失败: {exc}")
        db.commit()

    return _doc_to_dict(doc)


@router.get("/{kb_id}/documents/{doc_id}")
async def get_document(
    kb_id: int,
    doc_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    doc = KBService(db).get_document(doc_id)
    if doc is None or doc.kb_id != kb_id:
        raise HTTPException(status_code=404, detail="文档不存在")
    return _doc_to_dict(doc)


@router.delete("/{kb_id}/documents/{doc_id}")
async def delete_document(
    kb_id: int,
    doc_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    svc = KBService(db)
    doc = svc.get_document(doc_id)
    if doc is None or doc.kb_id != kb_id:
        raise HTTPException(status_code=404, detail="文档不存在")
    svc.delete_document(doc_id)
    db.commit()
    return {"message": "已删除"}


# ── 检索测试 ──────────────────────────────────────────


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    limit: int = 5


@router.post("/{kb_id}/search")
async def search_knowledge(
    kb_id: int,
    req: SearchRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """检索测试：在指定知识库中搜索。"""
    from lea_graph.services.knowledge_base import search_knowledge_base

    results = await search_knowledge_base(req.query, req.limit, kb_ids=[kb_id])
    return {"results": results, "total": len(results)}
