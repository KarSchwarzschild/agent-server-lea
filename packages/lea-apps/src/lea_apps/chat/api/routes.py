"""Chat App的API — 会话管理。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from lea_server.core.deps import get_current_user
from lea_server.database import get_db
from lea_server.models.models import User

router = APIRouter(tags=["chat-sessions"])


class SessionResponse(BaseModel):
    id: str
    title: str
    app_name: str
    created_at: str | None = None
    updated_at: str | None = None

    model_config = {"from_attributes": True}


class SessionCreate(BaseModel):
    title: str = "新对话"


@router.get("/sessions")
async def list_sessions(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from lea_apps.chat.models.models import ChatSession

    sessions = (
        db.query(ChatSession)
        .filter(ChatSession.user_id == user.id)
        .order_by(ChatSession.updated_at.desc())
        .limit(50)
        .all()
    )
    return {"sessions": sessions}


@router.post("/sessions")
async def create_session(
    data: SessionCreate = SessionCreate(),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from lea_apps.chat.models.models import ChatSession

    session = ChatSession(user_id=user.id, title=data.title)
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from lea_apps.chat.models.models import ChatSession

    session = db.query(ChatSession).filter(
        ChatSession.id == session_id,
        ChatSession.user_id == user.id,
    ).first()

    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    db.delete(session)
    db.commit()
    return {"status": "deleted"}


@router.get("/sessions/{session_id}/messages")
async def get_messages(
    session_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from lea_apps.chat.models.models import ChatMessage, ChatSession

    session = db.query(ChatSession).filter(
        ChatSession.id == session_id,
        ChatSession.user_id == user.id,
    ).first()
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")

    messages = (
        db.query(ChatMessage)
        .filter(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at)
        .all()
    )
    return {"messages": messages}
