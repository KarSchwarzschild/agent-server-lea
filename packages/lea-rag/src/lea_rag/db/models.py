"""检索服务数据模型 — 自包含，不依赖 lea-server。"""

from __future__ import annotations

from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey,
    Integer, JSON, String, Text, func,
)

from lea_rag.db.database import Base


class TenantMixin:
    tenant_id = Column(String(50), nullable=False, index=True, default="default")


class RetrievalModel(Base):
    __tablename__ = "retrieval_models"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, unique=True, index=True)
    model_type = Column(String(20), nullable=False, index=True)
    api_base = Column(String(500), nullable=False)
    api_key = Column(String(200), default="")
    model_name = Column(String(200), default="")
    dimension = Column(Integer, nullable=True)
    supports = Column(JSON, default=list)
    tags = Column(JSON, default=list)
    enabled = Column(Boolean, default=True)
    is_default = Column(Boolean, default=False)
    config = Column(JSON, default=dict)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class KnowledgeBase(TenantMixin, Base):
    __tablename__ = "knowledge_bases"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    description = Column(Text, default="")
    embedding_model = Column(String(100), default="")
    embedding_dim = Column(Integer, default=1024)
    reranker_model = Column(String(100), default="")
    vlm_model = Column(String(100), default="")
    vlm_enabled = Column(Boolean, default=False)
    reranker_enabled = Column(Boolean, default=True)
    multimodal = Column(Boolean, default=False)
    retrieval_mode = Column(String(20), default="hybrid")
    retrieval_top_k = Column(Integer, default=5)
    score_threshold = Column(Float, default=0.5)
    extra = Column("metadata", JSON, default=dict)
    owner = Column(String(100), nullable=False, index=True)
    visibility = Column(String(20), default="private", index=True)
    document_count = Column(Integer, default=0)
    word_count = Column(Integer, default=0)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class Document(TenantMixin, Base):
    __tablename__ = "kb_documents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    kb_id = Column(Integer, ForeignKey("knowledge_bases.id", ondelete="CASCADE"), nullable=False, index=True)
    filename = Column(String(300), nullable=False)
    file_key = Column(String(500), default="")
    file_type = Column(String(20), default="pdf")
    file_size = Column(Integer, default=0)
    total_pages = Column(Integer, default=0)
    parsed_content = Column(Text, default="")
    summary = Column(Text, default="")
    key_topics = Column(JSON, default=list)
    status = Column(String(20), default="processing", index=True)
    error_message = Column(Text, default="")
    celery_task_id = Column(String(100), default="")
    chunk_count = Column(Integer, default=0)
    word_count = Column(Integer, default=0)
    extra = Column("metadata", JSON, default=dict)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class DocumentChunk(Base):
    __tablename__ = "kb_document_chunks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    document_id = Column(Integer, ForeignKey("kb_documents.id", ondelete="CASCADE"), nullable=False, index=True)
    kb_id = Column(Integer, nullable=False, index=True)
    section_id = Column(Integer, ForeignKey("kb_document_sections.id", ondelete="SET NULL"), nullable=True, index=True)
    content = Column(Text, nullable=False)
    chunk_type = Column(String(20), default="text")
    position = Column(Integer, default=0)
    window_content = Column(Text, default="")
    extra = Column(JSON, default=dict)
    created_at = Column(DateTime, default=func.now())


class DocumentSection(Base):
    __tablename__ = "kb_document_sections"

    id = Column(Integer, primary_key=True, autoincrement=True)
    document_id = Column(Integer, ForeignKey("kb_documents.id", ondelete="CASCADE"), nullable=False, index=True)
    kb_id = Column(Integer, nullable=False, index=True)
    parent_id = Column(Integer, ForeignKey("kb_document_sections.id"), nullable=True)
    level = Column(Integer, default=1)
    title = Column(String(500), nullable=False)
    page_start = Column(Integer, default=0)
    page_end = Column(Integer, default=0)
    position = Column(Integer, default=0)
    summary = Column(Text, default="")
    created_at = Column(DateTime, default=func.now())


class UserMemory(Base):
    __tablename__ = "user_memories"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(String(64), default="default", index=True)
    user_id = Column(String(64), index=True, nullable=False)
    content = Column(Text, nullable=False)
    category = Column(String(32), default="fact")
    scope = Column(String(100), default="global", index=True)
    importance = Column(Integer, default=5)
    access_count = Column(Integer, default=0)
    last_accessed_at = Column(DateTime, nullable=True)
    source_session_id = Column(String(64), nullable=True)
    embedding_synced = Column(Boolean, default=False)
    created_at = Column(DateTime, server_default=func.now())


class UserProfile(Base):
    __tablename__ = "user_profiles"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(String(64), default="default", index=True)
    user_id = Column(String(64), nullable=False, index=True)
    agent_id = Column(String(64), default="", index=True)
    profile_text = Column(Text, default="")
    fact_count = Column(Integer, default=0)
    version = Column(Integer, default=1)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())


class MemoryConfig(TenantMixin, Base):
    __tablename__ = "memory_configs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(Integer, nullable=True, index=True)
    enabled = Column(Boolean, default=True)
    max_memories = Column(Integer, default=200)
    extract_enabled = Column(Boolean, default=True)
    categories = Column(JSON, default=["fact", "preference", "identity", "progress"])
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
