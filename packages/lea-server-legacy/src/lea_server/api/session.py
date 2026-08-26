"""会话管理 API — 对话列表、创建、归档、消息加载。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import desc
from sqlalchemy.orm import Session

from lea_server.core.deps import get_current_user
from lea_server.database import get_db
from lea_server.models.chat_session import ChatSession
from lea_server.models.models import User

router = APIRouter(prefix="/chat/sessions", tags=["sessions"])


class SessionCreate(BaseModel):
    title: str = "新对话"


class SessionUpdate(BaseModel):
    title: str | None = None


@router.get("")
def list_sessions(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = (
        db.query(ChatSession)
        .filter(ChatSession.user_id == user.user_id, ChatSession.is_archived == False)
        .order_by(desc(ChatSession.last_active_at))
    )
    total = query.count()
    sessions = query.offset((page - 1) * size).limit(size).all()
    return {
        "sessions": [
            {
                "session_id": s.id,
                "title": s.title,
                "created_at": s.created_at.isoformat() if s.created_at else "",
                "last_active_at": s.last_active_at.isoformat() if s.last_active_at else "",
                "message_count": s.message_count or 0,
            }
            for s in sessions
        ],
        "total": total,
    }


@router.post("")
def create_session(
    body: SessionCreate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    session = ChatSession(user_id=user.user_id, title=body.title)
    db.add(session)
    db.commit()
    db.refresh(session)
    return {"session_id": session.id, "title": session.title}


@router.patch("/{session_id}")
def update_session(
    session_id: str,
    body: SessionUpdate,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    session = (
        db.query(ChatSession)
        .filter(ChatSession.id == session_id, ChatSession.user_id == user.user_id)
        .first()
    )
    if not session:
        return {"error": "session not found"}, 404
    if body.title is not None:
        session.title = body.title
    db.commit()
    return {"ok": True}


@router.delete("/{session_id}")
def archive_session(
    session_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    session = (
        db.query(ChatSession)
        .filter(ChatSession.id == session_id, ChatSession.user_id == user.user_id)
        .first()
    )
    if not session:
        return {"error": "session not found"}, 404
    session.is_archived = True
    db.commit()
    return {"ok": True}


@router.get("/{session_id}/messages")
def get_session_messages(
    session_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """从 MySQL 加载 session 的消息历史。"""
    from lea_server.models.chat_session import ChatMessage

    msgs = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at)
        .all()
    )
    return {
        "messages": [
            {"role": m.role, "content": m.content}
            for m in msgs if m.content
        ]
    }
