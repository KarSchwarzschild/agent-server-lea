"""Knowledge-base publication and document-job administration."""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from lea_authkit import Actor, internal_headers, require_actor, require_admin
from lea_rag.db.database import get_db
from lea_rag.db.models import Document, DocumentChunk, DocumentSection, KnowledgeBase
from lea_rag.tasks import celery_app

router = APIRouter(tags=["knowledge-governance"])
internal_router = APIRouter(
    prefix="/internal/publications/knowledge-bases", tags=["internal-publications"]
)
logger = structlog.get_logger()


class KnowledgePublicationBody(BaseModel):
    target_scope: str
    note: str = ""


class PublicationCancelBody(BaseModel):
    note: str = ""


class KnowledgeReviewBody(BaseModel):
    resource_version: str
    target_scope: str


class OperationReasonBody(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


def _version(kb: KnowledgeBase) -> str:
    return kb.updated_at.isoformat() if kb.updated_at else "current"


def _scope(kb: KnowledgeBase) -> str:
    if kb.owner:
        return "private"
    return "global" if kb.tenant_id == "default" else "tenant"


def _snapshot(kb: KnowledgeBase) -> dict[str, Any]:
    return {
        "name": kb.name,
        "description": kb.description or "",
        "embedding_model": kb.embedding_model or "",
        "embedding_dim": kb.embedding_dim or 0,
        "retrieval_mode": kb.retrieval_mode or "hybrid",
        "document_count": kb.document_count or 0,
        "word_count": kb.word_count or 0,
        "enabled": bool(kb.enabled),
        "configuration_sha256": hashlib.sha256(
            repr(
                (
                    kb.retrieval_top_k,
                    kb.score_threshold,
                    kb.reranker_model,
                    kb.reranker_enabled,
                    kb.vlm_model,
                    kb.vlm_enabled,
                    kb.multimodal,
                )
            ).encode("utf-8")
        ).hexdigest(),
    }


def _config_headers(actor: Actor) -> dict[str, str]:
    headers = internal_headers()
    headers.update(
        {
            "X-User-ID": actor.user_id,
            "X-Tenant-ID": actor.tenant_id,
        }
    )
    if actor.is_platform_admin:
        headers["X-User-Role"] = "platform_admin"
    elif actor.is_tenant_admin:
        headers["X-User-Role"] = "admin"
    return headers


async def _post_config(path: str, actor: Actor, payload: dict[str, Any]) -> dict:
    base = os.getenv("LEA_CONFIG_URL", "http://lea-config:8500").rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post(
                base + path, json=payload, headers=_config_headers(actor)
            )
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"配置服务不可达: {exc}") from exc
    if response.status_code >= 400:
        detail = response.text[:500]
        try:
            detail = response.json().get("detail", detail)
        except ValueError:
            pass
        raise HTTPException(response.status_code, detail)
    return response.json()


def _record_audit(
    actor: Actor,
    *,
    tenant_id: str,
    action: str,
    resource_type: str,
    resource_id: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    reason: str,
    result: str = "success",
) -> None:
    base = os.getenv("LEA_CONFIG_URL", "").strip().rstrip("/")
    if not base:
        return
    try:
        response = httpx.post(
            base + "/internal/governance/audit-events",
            headers=_config_headers(actor),
            json={
                "tenant_id": tenant_id,
                "action": action,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "result": result,
                "before_summary": before,
                "after_summary": after,
                "reason": reason,
            },
            timeout=3,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("governance_audit_write_failed", action=action, error=str(exc))


@router.post("/knowledge-bases/{kb_id}/publication-requests", status_code=201)
async def submit_knowledge_publication(
    kb_id: int,
    body: KnowledgePublicationBody,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    if body.target_scope not in ("tenant", "global"):
        raise HTTPException(422, "target_scope 只能是 tenant 或 global")
    kb = db.get(KnowledgeBase, kb_id)
    if kb is None or (not actor.is_platform_admin and kb.tenant_id != actor.tenant_id):
        raise HTTPException(404, "知识库不存在")
    if kb.owner != actor.user_id and not actor.is_tenant_admin:
        raise HTTPException(403, "只能申请发布自己的知识库")
    current = _scope(kb)
    if current == "global" or (current == "tenant" and body.target_scope == "tenant"):
        raise HTTPException(409, "知识库已经处于目标可见范围")
    return await _post_config(
        "/internal/governance/publication-requests",
        actor,
        {
            "resource_type": "knowledge_base",
            "resource_id": str(kb.id),
            "resource_version": _version(kb),
            "tenant_id": kb.tenant_id,
            "source_scope": current,
            "target_scope": body.target_scope,
            "note": body.note,
            "risk_snapshot": {
                "document_count": kb.document_count or 0,
                "word_count": kb.word_count or 0,
            },
            "resource_snapshot": _snapshot(kb),
        },
    )


@router.post(
    "/knowledge-bases/{kb_id}/publication-requests/{request_id}/cancel"
)
async def cancel_knowledge_publication(
    kb_id: int,
    request_id: int,
    body: PublicationCancelBody,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    kb = db.get(KnowledgeBase, kb_id)
    if kb is None or (not actor.is_platform_admin and kb.tenant_id != actor.tenant_id):
        raise HTTPException(404, "知识库不存在")
    return await _post_config(
        f"/internal/governance/publication-requests/{request_id}/cancel",
        actor,
        {"note": body.note},
    )


@internal_router.post("/{kb_id}/review")
def apply_knowledge_review(
    kb_id: int,
    body: KnowledgeReviewBody,
    db: Session = Depends(get_db),
):
    kb = db.get(KnowledgeBase, kb_id)
    if kb is None:
        raise HTTPException(410, "对应知识库已被删除")
    if _version(kb) != body.resource_version:
        raise HTTPException(409, "知识库在提交审批后发生了变化，请重新提交")
    if body.target_scope == "tenant":
        kb.owner = ""
        kb.visibility = "shared"
    elif body.target_scope == "global":
        kb.owner = ""
        kb.visibility = "public"
        kb.tenant_id = "default"
    else:
        raise HTTPException(422, "无效的目标范围")
    kb.enabled = True
    db.commit()
    return {"ok": True, "scope": body.target_scope}


def _set_publication_enabled(kb_id: int, enabled: bool, db: Session) -> dict:
    kb = db.get(KnowledgeBase, kb_id)
    if kb is None:
        raise HTTPException(410, "对应知识库已被删除")
    kb.enabled = enabled
    db.commit()
    return {"ok": True, "enabled": enabled}


@internal_router.post("/{kb_id}/suspend")
def suspend_knowledge_publication(kb_id: int, db: Session = Depends(get_db)):
    return _set_publication_enabled(kb_id, False, db)


@internal_router.post("/{kb_id}/resume")
def resume_knowledge_publication(kb_id: int, db: Session = Depends(get_db)):
    return _set_publication_enabled(kb_id, True, db)


@internal_router.post("/{kb_id}/archive")
def archive_knowledge_publication(kb_id: int, db: Session = Depends(get_db)):
    return _set_publication_enabled(kb_id, False, db)


def _document_dict(doc: Document) -> dict[str, Any]:
    return {
        "id": doc.id,
        "kb_id": doc.kb_id,
        "tenant_id": doc.tenant_id,
        "filename": doc.filename,
        "status": doc.status,
        "error_message": doc.error_message or "",
        "celery_task_id": doc.celery_task_id or "",
        "file_size": doc.file_size or 0,
        "chunk_count": doc.chunk_count or 0,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
        "updated_at": doc.updated_at.isoformat() if doc.updated_at else None,
    }


@router.get("/operations/document-jobs")
def list_document_jobs(
    status: str | None = None,
    kb_id: int | None = None,
    stalled_minutes: int | None = Query(None, ge=1, le=10080),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    require_admin(actor)
    query = db.query(Document)
    if not actor.is_platform_admin:
        query = query.filter(Document.tenant_id == actor.tenant_id)
    if status:
        query = query.filter(Document.status == status)
    if kb_id:
        query = query.filter(Document.kb_id == kb_id)
    if stalled_minutes:
        cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=stalled_minutes)
        query = query.filter(Document.status == "processing", Document.updated_at < cutoff)
    total = query.count()
    rows = (
        query.order_by(Document.updated_at.desc(), Document.id.desc())
        .offset((page - 1) * size)
        .limit(size)
        .all()
    )
    return {
        "items": [_document_dict(row) for row in rows],
        "total": total,
        "page": page,
        "size": size,
    }


def _load_document_for_admin(db: Session, actor: Actor, document_id: int) -> Document:
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(404, "文档任务不存在")
    if not actor.is_platform_admin and document.tenant_id != actor.tenant_id:
        raise HTTPException(404, "文档任务不存在")
    return document


@router.get("/operations/document-jobs/{document_id}")
def get_document_job(
    document_id: int,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    require_admin(actor)
    return _document_dict(_load_document_for_admin(db, actor, document_id))


@router.post("/operations/document-jobs/{document_id}/cancel")
def cancel_document_job(
    document_id: int,
    body: OperationReasonBody,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    require_admin(actor)
    document = _load_document_for_admin(db, actor, document_id)
    before = _document_dict(document)
    if document.status != "processing":
        _record_audit(
            actor,
            tenant_id=document.tenant_id,
            action="document_job.cancel",
            resource_type="document_job",
            resource_id=str(document.id),
            before=before,
            after=None,
            reason=body.reason,
            result="blocked",
        )
        raise HTTPException(409, "只有 processing 任务可以取消")
    if not document.celery_task_id:
        _record_audit(
            actor,
            tenant_id=document.tenant_id,
            action="document_job.cancel",
            resource_type="document_job",
            resource_id=str(document.id),
            before=before,
            after=None,
            reason=body.reason,
            result="blocked",
        )
        raise HTTPException(409, "任务缺少 Celery task id，无法安全取消")
    state = celery_app.AsyncResult(document.celery_task_id).state
    if state == "STARTED":
        _record_audit(
            actor,
            tenant_id=document.tenant_id,
            action="document_job.cancel",
            resource_type="document_job",
            resource_id=str(document.id),
            before=before,
            after=None,
            reason=body.reason,
            result="blocked",
        )
        raise HTTPException(409, "任务已经开始执行，当前处理器不支持安全中断")
    celery_app.control.revoke(document.celery_task_id, terminate=False)
    document.status = "canceled"
    document.error_message = f"管理员取消任务: {body.reason}"
    db.commit()
    after = _document_dict(document)
    _record_audit(
        actor,
        tenant_id=document.tenant_id,
        action="document_job.cancel",
        resource_type="document_job",
        resource_id=str(document.id),
        before=before,
        after=after,
        reason=body.reason,
    )
    return after


@router.post("/operations/document-jobs/{document_id}/retry")
def retry_document_job(
    document_id: int,
    body: OperationReasonBody,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    require_admin(actor)
    document = _load_document_for_admin(db, actor, document_id)
    before = _document_dict(document)
    if document.status not in ("error", "failed", "canceled"):
        _record_audit(
            actor,
            tenant_id=document.tenant_id,
            action="document_job.retry",
            resource_type="document_job",
            resource_id=str(document.id),
            before=before,
            after=None,
            reason=body.reason,
            result="blocked",
        )
        raise HTTPException(409, "只有失败或已取消任务可以重试")
    db.query(DocumentChunk).filter(DocumentChunk.document_id == document.id).delete(
        synchronize_session=False
    )
    db.query(DocumentSection).filter(DocumentSection.document_id == document.id).delete(
        synchronize_session=False
    )
    try:
        from lea_rag.index.milvus_index import MilvusKBIndex

        MilvusKBIndex().delete_by_document(document.id)
    except Exception:
        db.rollback()
        _record_audit(
            actor,
            tenant_id=document.tenant_id,
            action="document_job.retry",
            resource_type="document_job",
            resource_id=str(document.id),
            before=before,
            after=None,
            reason=body.reason,
            result="failed",
        )
        raise HTTPException(502, "旧向量清理失败，未创建重试任务")
    try:
        result = celery_app.send_task(
            "lea_rag.tasks.process_document", args=[document.id], queue="retrieval"
        )
    except Exception:
        db.rollback()
        _record_audit(
            actor,
            tenant_id=document.tenant_id,
            action="document_job.retry",
            resource_type="document_job",
            resource_id=str(document.id),
            before=before,
            after=None,
            reason=body.reason,
            result="failed",
        )
        raise HTTPException(502, "重试任务提交失败，数据库变更已回滚")
    document.status = "processing"
    document.error_message = ""
    document.celery_task_id = result.id
    metadata = dict(document.extra or {})
    metadata["last_retry_reason"] = body.reason
    document.extra = metadata
    db.commit()
    after = _document_dict(document)
    _record_audit(
        actor,
        tenant_id=document.tenant_id,
        action="document_job.retry",
        resource_type="document_job",
        resource_id=str(document.id),
        before=before,
        after=after,
        reason=body.reason,
    )
    return after
