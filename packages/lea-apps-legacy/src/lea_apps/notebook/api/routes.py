"""Notebook API — 文档管理 CRUD + 上传 + RAG 对话。

文档统一存储在平台知识库系统中（用户个人库）。
"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from lea_apps.notebook.api.schemas import (
    ChatRequest,
    MessageOut,
    SessionOut,
    SourceDetail,
    SourceOut,
    SourceStatus,
)
from lea_server.core.deps import get_current_tenant, get_current_user
from lea_server.core.tenant import TenantContext
from lea_server.database import get_db
from lea_server.models.knowledge import Document
from lea_server.models.models import User
from lea_server.services.kb_service import KBService

logger = structlog.get_logger()

router = APIRouter(tags=["notebook"])


@router.post("/sources/upload", response_model=SourceOut)
async def upload_source(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    tenant: TenantContext = Depends(get_current_tenant),
):
    """上传文档到用户个人库，触发异步解析。"""
    ext = (file.filename or "unknown").rsplit(".", 1)[-1].lower()
    if ext not in ("pdf", "txt", "md", "docx"):
        raise HTTPException(status_code=400, detail=f"不支持的文件类型: {ext}")

    content = await file.read()
    if len(content) > 50 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="文件大小不能超过 50MB")

    svc = KBService(db)
    personal_kb = svc.get_or_create_personal_kb(user.user_id, tenant.tenant_id)

    from lea_server.services.object_storage import get_object_storage

    file_key = f"kb/{personal_kb.id}/{uuid.uuid4().hex}_{file.filename}"
    content_type = file.content_type or "application/octet-stream"
    get_object_storage().put_object(file_key, content, content_type)

    doc = svc.create_document(personal_kb.id, {
        "filename": file.filename or "unknown",
        "file_key": file_key,
        "file_type": ext,
        "file_size": len(content),
    }, tenant_id=tenant.tenant_id)
    db.commit()

    try:
        from lea_server.tasks.celery_app import app as celery_app
        celery_app.send_task("lea_server.tasks.kb_process", args=[doc.id])
    except Exception as exc:
        logger.warning("celery_dispatch_failed", error=str(exc))
        svc.update_document_status(doc.id, "error", error_message="任务调度失败")
        db.commit()

    logger.info("source_uploaded", doc_id=doc.id, filename=doc.filename)
    return _doc_to_source(doc)


@router.get("/sources", response_model=list[SourceOut])
async def list_sources(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    tenant: TenantContext = Depends(get_current_tenant),
):
    """列出用户个人库的文档。"""
    svc = KBService(db)
    personal_kb = svc.get_or_create_personal_kb(user.user_id, tenant.tenant_id)
    docs, _ = svc.list_documents(personal_kb.id, page=1, size=100)
    db.commit()
    return [_doc_to_source(d) for d in docs]


@router.get("/sources/{source_id}", response_model=SourceDetail)
async def get_source(
    source_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    tenant: TenantContext = Depends(get_current_tenant),
):
    doc = db.query(Document).filter(
        Document.id == source_id,
        Document.tenant_id == tenant.tenant_id,
    ).first()
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    return _doc_to_source_detail(doc)


@router.get("/sources/{source_id}/status", response_model=SourceStatus)
async def get_source_status(
    source_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    tenant: TenantContext = Depends(get_current_tenant),
):
    doc = db.query(Document).filter(
        Document.id == source_id,
        Document.tenant_id == tenant.tenant_id,
    ).first()
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    return {"id": doc.id, "status": doc.status, "celery_task_id": doc.celery_task_id or ""}


@router.delete("/sources/{source_id}")
async def delete_source(
    source_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    tenant: TenantContext = Depends(get_current_tenant),
):
    svc = KBService(db)
    doc = svc.get_document(source_id)
    if not doc or doc.tenant_id != tenant.tenant_id:
        raise HTTPException(status_code=404, detail="文档不存在")

    try:
        from lea_rag.index.vector_index import MilvusRAGIndex
        index = MilvusRAGIndex(collection_name="lea_knowledge")
        index.delete_by_metadata("document_id", str(source_id))
    except Exception as exc:
        logger.warning("milvus_delete_failed", doc_id=source_id, error=str(exc))

    if doc.file_key:
        try:
            from lea_server.services.object_storage import get_object_storage
            get_object_storage().delete_object(doc.file_key)
        except Exception as exc:
            logger.warning("minio_delete_failed", key=doc.file_key, error=str(exc))

    svc.delete_document(source_id)
    db.commit()
    logger.info("source_deleted", doc_id=source_id, filename=doc.filename)
    return {"status": "deleted"}


# ── Chat ──────────────────────────────────────────────────────


@router.post("/chat/stream")
async def notebook_chat_stream(
    req: ChatRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    tenant: TenantContext = Depends(get_current_tenant),
):
    """文档级 RAG 对话（SSE 流式）。"""
    from lea_apps.notebook.models.models import NotebookMessage, NotebookSession
    from lea_apps.notebook.services.chat import chat_stream

    session_id = req.session_id
    if not session_id:
        session = NotebookSession(
            tenant_id=tenant.tenant_id,
            user_id=user.user_id,
            title=req.question[:50],
            source_ids=req.source_ids or [],
        )
        db.add(session)
        db.commit()
        db.refresh(session)
        session_id = session.id

    history: list[dict[str, str]] = []
    msgs = (
        db.query(NotebookMessage)
        .filter(NotebookMessage.session_id == session_id)
        .order_by(NotebookMessage.created_at.desc())
        .limit(10)
        .all()
    )
    for m in reversed(msgs):
        history.append({"role": m.role, "content": m.content})

    user_msg = NotebookMessage(session_id=session_id, role="user", content=req.question)
    db.add(user_msg)
    db.commit()

    async def _stream():
        from lea_graph.streaming import _sse

        yield _sse("session", {"session_id": session_id})

        full_response = ""
        sources_data: list[dict] = []

        async for event in chat_stream(
            req.question,
            source_ids=req.source_ids,
            history=history,
            kb_ids=req.kb_ids,
            document_ids=req.document_ids,
        ):
            yield _sse(event.type, event.data)
            if event.type == "content":
                full_response += event.data.get("text", "")
            elif event.type == "sources":
                sources_data = event.data.get("items", [])

        ai_msg = NotebookMessage(
            session_id=session_id, role="assistant",
            content=full_response, sources=sources_data,
        )
        db.add(ai_msg)
        db.commit()

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/sessions", response_model=list[SessionOut])
async def list_sessions(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    tenant: TenantContext = Depends(get_current_tenant),
):
    from lea_apps.notebook.models.models import NotebookSession

    return (
        db.query(NotebookSession)
        .filter(
            NotebookSession.tenant_id == tenant.tenant_id,
            NotebookSession.user_id == user.user_id,
        )
        .order_by(NotebookSession.updated_at.desc())
        .limit(50)
        .all()
    )


@router.get("/sessions/{session_id}/messages", response_model=list[MessageOut])
async def get_session_messages(
    session_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    tenant: TenantContext = Depends(get_current_tenant),
):
    from lea_apps.notebook.models.models import NotebookMessage, NotebookSession

    session = (
        db.query(NotebookSession)
        .filter(
            NotebookSession.id == session_id,
            NotebookSession.tenant_id == tenant.tenant_id,
        )
        .first()
    )
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    return (
        db.query(NotebookMessage)
        .filter(NotebookMessage.session_id == session_id)
        .order_by(NotebookMessage.created_at.asc())
        .all()
    )


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    tenant: TenantContext = Depends(get_current_tenant),
):
    from lea_apps.notebook.models.models import NotebookMessage, NotebookSession

    session = (
        db.query(NotebookSession)
        .filter(
            NotebookSession.id == session_id,
            NotebookSession.tenant_id == tenant.tenant_id,
        )
        .first()
    )
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    db.query(NotebookMessage).filter(NotebookMessage.session_id == session_id).delete()
    db.delete(session)
    db.commit()
    return {"status": "deleted"}


# ── Helpers ──────────────────────────────────────────────────


def _doc_to_source(doc: Document) -> dict:
    return {
        "id": doc.id,
        "filename": doc.filename,
        "file_type": doc.file_type or "pdf",
        "file_size": doc.file_size or 0,
        "total_pages": doc.total_pages or 0,
        "status": doc.status or "processing",
        "summary": doc.summary or "",
        "key_topics": doc.key_topics or [],
        "created_at": doc.created_at,
    }


def _doc_to_source_detail(doc: Document) -> dict:
    out = _doc_to_source(doc)
    out["parsed_content"] = doc.parsed_content or ""
    return out
