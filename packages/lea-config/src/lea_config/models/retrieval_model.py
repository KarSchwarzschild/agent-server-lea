"""RetrievalModel — embedding / reranker / vlm / asr 模型注册表。"""

from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, Integer, JSON, String, func

from lea_config.database import Base


class RetrievalModel(Base):
    __tablename__ = "retrieval_models"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, unique=True, index=True)
    model_type = Column(String(20), nullable=False, index=True)  # embedding|reranker|vlm|asr
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
