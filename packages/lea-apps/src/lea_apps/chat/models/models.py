"""Chat App数据库模型 — 会话和消息。"""

from __future__ import annotations

import uuid

from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, Text, func

from lea_server.database import Base


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(Integer, ForeignKey("users.id"))
    title = Column(String(200), default="新对话")
    app_name = Column(String(50), default="chat")
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class ChatMessage(Base):
    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(36), ForeignKey("chat_sessions.id", ondelete="CASCADE"))
    role = Column(String(20))
    content = Column(Text)
    tool_calls = Column(JSON, nullable=True)
    token_count = Column(Integer, nullable=True)
    created_at = Column(DateTime, default=func.now())
