"""知识库数据模型 — 平台级知识管理。

三层结构：KnowledgeBase → Document → DocumentChunk
多租户隔离：KnowledgeBase 和 Document 继承 TenantMixin。
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean, Column, DateTime, Float, ForeignKey, Integer, JSON, String, Text, func,
)

from lea_server.core.tenant import TenantMixin
from lea_server.database import Base


class KnowledgeBase(TenantMixin, Base):
    """知识库 — 文档集合的容器。"""

    __tablename__ = "knowledge_bases"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    description = Column(Text, default="")

    embedding_model = Column(String(100), default="bge-m3")
    retrieval_mode = Column(String(20), default="hybrid")
    retrieval_top_k = Column(Integer, default=5)
    score_threshold = Column(Float, default=0.5)

    owner = Column(String(100), nullable=False, index=True)
    visibility = Column(String(20), default="private", index=True)

    document_count = Column(Integer, default=0)
    word_count = Column(Integer, default=0)
    enabled = Column(Boolean, default=True)

    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    def __repr__(self) -> str:
        return f"<KnowledgeBase {self.name} owner={self.owner} vis={self.visibility}>"


class Document(TenantMixin, Base):
    """文档 — 知识库中的单个文件。"""

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

    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    def __repr__(self) -> str:
        return f"<Document {self.filename} kb={self.kb_id} status={self.status}>"


class DocumentChunk(Base):
    """文档分块 — 向量检索的最小单元。"""

    __tablename__ = "kb_document_chunks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    document_id = Column(Integer, ForeignKey("kb_documents.id", ondelete="CASCADE"), nullable=False, index=True)
    kb_id = Column(Integer, nullable=False, index=True)

    content = Column(Text, nullable=False)
    chunk_type = Column(String(20), default="text")
    position = Column(Integer, default=0)
    window_content = Column(Text, default="")
    extra = Column(JSON, default=dict)

    created_at = Column(DateTime, default=func.now())

    def __repr__(self) -> str:
        return f"<DocumentChunk doc={self.document_id} pos={self.position}>"
