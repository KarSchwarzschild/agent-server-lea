"""Notebook 数据模型。

注意：NotebookSource / NotebookChunk / NotebookDocKnowledge 已废弃。
文档存储统一使用平台知识库系统（lea_server.models.knowledge）。
仅 NotebookSession / NotebookMessage 仍在使用。
"""

from __future__ import annotations

from sqlalchemy import Column, DateTime, Index, Integer, String, Text, func
from sqlalchemy.dialects.mysql import JSON

from lea_server.core.tenant import TenantMixin
from lea_server.database import Base


class NotebookSource(TenantMixin, Base):
    """用户上传的文档。"""

    __tablename__ = "notebook_sources"
    __table_args__ = (
        Index("idx_nb_source_tenant_user", "tenant_id", "user_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False)
    filename = Column(String(300), nullable=False)
    file_path = Column(String(500), nullable=False)
    file_type = Column(String(20), default="pdf")
    file_size = Column(Integer, default=0)
    total_pages = Column(Integer, default=0)
    parsed_content = Column(Text, default="")
    summary = Column(Text, default="")
    key_topics = Column(JSON, default=list)
    status = Column(String(20), default="processing", index=True)
    celery_task_id = Column(String(100), default="")
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class NotebookChunk(Base):
    """文档分块 — 向量检索的最小单元。"""

    __tablename__ = "notebook_chunks"
    __table_args__ = (
        Index("idx_nb_chunk_source", "source_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_id = Column(Integer, nullable=False)
    content = Column(Text, nullable=False)
    chunk_type = Column(String(20), default="text")
    window_content = Column(Text, default="")
    extra = Column(JSON, default=dict)
    created_at = Column(DateTime, default=func.now())


class NotebookSession(TenantMixin, Base):
    """对话会话 — 一组文档上的 RAG 对话。"""

    __tablename__ = "notebook_sessions"
    __table_args__ = (
        Index("idx_nb_session_tenant_user", "tenant_id", "user_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False)
    title = Column(String(200), default="新对话")
    source_ids = Column(JSON, default=list)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class NotebookMessage(Base):
    """对话消息。"""

    __tablename__ = "notebook_messages"
    __table_args__ = (
        Index("idx_nb_msg_session", "session_id"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    session_id = Column(Integer, nullable=False)
    role = Column(String(20), nullable=False)
    content = Column(Text, nullable=False)
    sources = Column(JSON, default=list)
    created_at = Column(DateTime, default=func.now())


class NotebookDocKnowledge(Base):
    """文档结构化知识缓存 — 上传时提取，所有功能共享。"""

    __tablename__ = "notebook_doc_knowledge"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source_id = Column(Integer, nullable=False, unique=True)
    topics = Column(JSON, default=list)
    concepts = Column(JSON, default=list)
    relationships = Column(JSON, default=list)
    raw_analysis = Column(JSON, default=dict)
    created_at = Column(DateTime, default=func.now())
