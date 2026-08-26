"""用户视角 KnowledgeBase 路由 —— 方案 Y：从 lea-server 迁过来。

前端直连 :8300/knowledge-bases/*。契约与 lea-server 原 knowledge.py 兼容。
Auth 走 lea-authkit 的 require_actor：缺头 401，无服务间鉴权 503。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from lea_authkit import READ, UPDATE, DELETE, Actor, require, require_actor, scoped_filter
from lea_rag.db.database import get_db
from lea_rag.db.models import Document, KnowledgeBase
from lea_rag.services.kb_service import KBService

router = APIRouter(prefix="/knowledge-bases", tags=["user-knowledge"])


# ── Schemas ────────────────────────────────────────────────

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


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    limit: int = 5


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


# ── KB CRUD ────────────────────────────────────────────────

@router.get("")
def list_knowledge_bases(
    page: int = 1,
    size: int = 50,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    """列出当前用户可见的 KB：自己的 + public。"""
    q = db.query(KnowledgeBase).filter(
        KnowledgeBase.enabled == True,  # noqa: E712
        scoped_filter(actor, KnowledgeBase),
    )
    total = q.count()
    kbs = q.order_by(KnowledgeBase.id.desc()).offset((page - 1) * size).limit(size).all()
    return {
        "knowledge_bases": [_kb_to_dict(kb) for kb in kbs],
        "total": total,
        "page": page,
        "size": size,
    }


@router.post("")
def create_knowledge_base(
    body: KBCreateRequest,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    svc = KBService(db)
    kb = svc.create_kb(body.model_dump(), owner=actor.user_id, tenant_id=actor.tenant_id)
    db.commit()
    return _kb_to_dict(kb)


@router.get("/{kb_id}")
def get_knowledge_base(
    kb_id: int,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    kb = KBService(db).get_kb(kb_id)
    if kb is None:
        raise HTTPException(404, "知识库不存在")
    require(actor, READ, kb)
    return _kb_to_dict(kb)


@router.put("/{kb_id}")
def update_knowledge_base(
    kb_id: int,
    body: KBUpdateRequest,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    svc = KBService(db)
    kb = svc.get_kb(kb_id)
    if kb is None:
        raise HTTPException(404, "知识库不存在")
    require(actor, UPDATE, kb)
    kb = svc.update_kb(kb_id, body.model_dump(exclude_none=True))
    db.commit()
    return _kb_to_dict(kb)


@router.delete("/{kb_id}")
def delete_knowledge_base(
    kb_id: int,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    svc = KBService(db)
    kb = svc.get_kb(kb_id)
    if kb is None:
        raise HTTPException(404, "知识库不存在")
    require(actor, DELETE, kb)
    svc.delete_kb(kb_id)
    db.commit()
    return {"message": "已删除"}


# ── Documents ──────────────────────────────────────────────

@router.get("/{kb_id}/documents")
def list_documents(
    kb_id: int,
    page: int = 1,
    size: int = 50,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    svc = KBService(db)
    kb = svc.get_kb(kb_id)
    if kb is None:
        raise HTTPException(404, "知识库不存在")
    require(actor, READ, kb)
    docs, total = svc.list_documents(kb_id, page=page, size=size)
    return {
        "documents": [_doc_to_dict(d) for d in docs],
        "total": total,
        "page": page,
        "size": size,
    }


@router.post("/{kb_id}/documents")
async def upload_document(
    kb_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    """上传文档到 KB，走 celery 后台处理。"""
    from lea_rag.services.object_storage import ObjectStorage

    svc = KBService(db)
    kb = svc.get_kb(kb_id)
    if kb is None:
        raise HTTPException(404, "知识库不存在")
    require(actor, UPDATE, kb)

    file_ext = (file.filename or "file").rsplit(".", 1)[-1].lower()
    allowed = {"pdf", "txt", "md", "docx", "pptx", "png", "jpg", "jpeg", "webp"}
    if file_ext not in allowed:
        raise HTTPException(400, f"不支持的文件类型: {file_ext}")

    content = await file.read()
    file_key = f"kb/{kb_id}/{file.filename}"

    try:
        storage = ObjectStorage()
        storage.put_object(
            file_key, content,
            content_type=file.content_type or "application/octet-stream",
        )
    except Exception as exc:
        raise HTTPException(500, f"文件上传失败: {exc}")

    doc = svc.create_document(
        kb_id,
        {
            "filename": file.filename or "unknown",
            "file_key": file_key,
            "file_type": file_ext,
            "file_size": len(content),
        },
        tenant_id=actor.tenant_id,
    )
    db.commit()

    try:
        from lea_rag.tasks import celery_app
        celery_app.send_task(
            "lea_rag.tasks.process_document",
            args=[doc.id],
            queue="retrieval",
        )
    except Exception as exc:
        svc.update_document_status(doc.id, "error", error_message=f"任务提交失败: {exc}")
        db.commit()

    return _doc_to_dict(doc)


@router.get("/{kb_id}/documents/{doc_id}")
def get_document(
    kb_id: int,
    doc_id: int,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    kb = KBService(db).get_kb(kb_id)
    if kb is None:
        raise HTTPException(404, "知识库不存在")
    require(actor, READ, kb)
    doc = KBService(db).get_document(doc_id)
    if doc is None or doc.kb_id != kb_id:
        raise HTTPException(404, "文档不存在")
    return _doc_to_dict(doc)


@router.delete("/{kb_id}/documents/{doc_id}")
def delete_document(
    kb_id: int,
    doc_id: int,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    kb = KBService(db).get_kb(kb_id)
    if kb is None:
        raise HTTPException(404, "知识库不存在")
    require(actor, UPDATE, kb)
    svc = KBService(db)
    doc = svc.get_document(doc_id)
    if doc is None or doc.kb_id != kb_id:
        raise HTTPException(404, "文档不存在")
    svc.delete_document(doc_id)
    db.commit()
    return {"message": "已删除"}


# ── Search ─────────────────────────────────────────────────

@router.post("/{kb_id}/search")
async def search_knowledge(
    kb_id: int,
    body: SearchRequest,
    actor: Actor = Depends(require_actor),
):
    """在指定 KB 中检索，复用 hybrid_v2 检索器（无 rerank，前端测试用）。"""
    from lea_rag.retrieval.hybrid_v2 import HybridRetrieverV2

    from lea_rag.db.database import SessionLocal
    db = SessionLocal()
    try:
        kb = KBService(db).get_kb(kb_id)
        if kb is None:
            raise HTTPException(404, "知识库不存在")
        require(actor, READ, kb)
    finally:
        db.close()

    retriever = HybridRetrieverV2()
    hits = await retriever.search(
        query=body.query,
        top_k=body.limit,
        kb_ids=[kb_id],
        tenant_id=actor.tenant_id,
    )
    results = [
        {
            "content": h.content,
            "score": h.score,
            "kb_id": h.kb_id,
            "document_id": h.document_id,
            "source_filename": getattr(h, "source_filename", ""),
        }
        for h in hits
    ]
    return {"results": results, "total": len(results)}
