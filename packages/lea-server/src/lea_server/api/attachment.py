"""聊天附件 API — 上传文件供对话使用。"""

from __future__ import annotations

import uuid

import structlog
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session

from lea_server.core.deps import get_current_user, get_current_tenant
from lea_server.core.tenant import TenantContext
from lea_server.database import get_db
from lea_server.models.attachment import ChatAttachment
from lea_server.models.models import User

logger = structlog.get_logger()

router = APIRouter(prefix="/chat/attachments", tags=["chat-attachments"])

MAX_FILE_SIZE = 30 * 1024 * 1024  # 30MB


@router.post("/upload")
async def upload_attachment(
    file: UploadFile = File(...),
    session_id: str = "",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    tenant: TenantContext = Depends(get_current_tenant),
):
    """上传对话附件。提取文本内容后存储，供后续对话消息引用。"""
    from lea_graph.attachments import process_attachment
    from lea_server.services.object_storage import get_object_storage

    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="文件大小不能超过 30MB")

    filename = file.filename or "unknown"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    try:
        result = await process_attachment(content, filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    file_key = f"attachments/{user.user_id}/{uuid.uuid4().hex}_{filename}"
    content_type = file.content_type or "application/octet-stream"
    get_object_storage().put_object(file_key, content, content_type)

    attachment = ChatAttachment(
        tenant_id=tenant.tenant_id,
        user_id=user.user_id,
        session_id=session_id,
        filename=filename,
        file_key=file_key,
        file_type=ext,
        file_size=len(content),
        media_type=result.media_type,
        extracted_text=result.text,
    )
    db.add(attachment)
    db.commit()
    db.refresh(attachment)

    logger.info("attachment_uploaded", id=attachment.id, filename=filename, media_type=result.media_type)

    return {
        "id": attachment.id,
        "filename": filename,
        "file_type": ext,
        "file_size": len(content),
        "media_type": result.media_type,
        "text_preview": result.text[:200] if result.text else "",
        "truncated": result.truncated,
    }


@router.get("")
async def list_attachments(
    session_id: str = "",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    tenant: TenantContext = Depends(get_current_tenant),
):
    """获取用户的附件列表。"""
    query = db.query(ChatAttachment).filter(
        ChatAttachment.tenant_id == tenant.tenant_id,
        ChatAttachment.user_id == user.user_id,
    )
    if session_id:
        query = query.filter(ChatAttachment.session_id == session_id)
    attachments = query.order_by(ChatAttachment.created_at.desc()).limit(50).all()
    return [
        {
            "id": a.id,
            "filename": a.filename,
            "file_type": a.file_type,
            "file_size": a.file_size,
            "media_type": a.media_type,
            "created_at": a.created_at,
        }
        for a in attachments
    ]
