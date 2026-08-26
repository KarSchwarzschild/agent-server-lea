"""KnowledgeBase 模型。"""

from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String, Text, UniqueConstraint, func

from lea_config.database import Base, TenantMixin


class KnowledgeBase(TenantMixin, Base):
    __tablename__ = "knowledge_bases"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_kb_tenant_name"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, index=True)
    description = Column(Text, default="")
    embedding_model = Column(String(100), default="bge-m3")
    retrieval_mode = Column(String(30), default="hybrid")
    retrieval_top_k = Column(Integer, default=5)
    score_threshold = Column(Float, default=0.5)
    owner = Column(String(100), nullable=True)
    visibility = Column(String(20), default="private")
    document_count = Column(Integer, default=0)
    enabled = Column(Boolean, default=True, index=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
