"""会话管理模型 — 对话 Session 持久化（平台级）。"""

from __future__ import annotations

import uuid

from sqlalchemy import JSON, Boolean, Column, DateTime, Integer, String, Text, func

from lea_server.core.tenant import TenantMixin
from lea_server.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class ChatSession(TenantMixin, Base):
    """对话会话 — 每个对话有独立上下文。

    id 即 session_id，UUID string，前端/LangGraph thread_id 都用它。
    """

    __tablename__ = "chat_sessions"

    id = Column(String(64), primary_key=True, default=_uuid)
    user_id = Column(String(64), index=True, nullable=False)
    title = Column(String(200), default="新对话")
    app_name = Column(String(50), default="chat")
    created_at = Column(DateTime, server_default=func.now())
    last_active_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    message_count = Column(Integer, default=0)
    is_archived = Column(Boolean, default=False)


class ChatMessage(Base):
    """对话消息记录。"""

    __tablename__ = "chat_messages"

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(String(64), index=True, nullable=False)
    role = Column(String(20))
    content = Column(Text)
    token_count = Column(Integer, nullable=True)
    # 关联的 artifact（如 slides / markdown 文档）—— 前端气泡渲染卡片入口
    artifact_id = Column(String(80), nullable=True, index=True)
    # 可视化附加字段（intent/skill/tool_calls/references/plan/parallel_subtasks/...）—— 前端 switchSession 恢复用
    extra = Column(JSON, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class UserMemory(Base):
    """用户长期记忆 — 跨 session 的持久事实。"""

    __tablename__ = "user_memories"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(64), index=True, nullable=False)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, server_default=func.now())
