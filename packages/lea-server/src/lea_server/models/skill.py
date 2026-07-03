"""Skill 数据库模型 — 平台级技能定义持久化。"""

from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, Integer, JSON, String, Text, func

from lea_server.database import Base


class Skill(Base):
    __tablename__ = "skills"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), unique=True, nullable=False, index=True)
    display_name = Column(String(200), nullable=False)
    description = Column(Text, default="")
    version = Column(String(20), default="1.0.0")
    category = Column(String(50), default="general")

    execution_mode = Column(String(20), default="react")
    pipeline_class = Column(String(200), default="")
    context = Column(String(20), default="fork")
    model_tier = Column(String(20), default="mid")
    allowed_tools = Column(JSON, default=list)
    trigger_keywords = Column(JSON, default=list)
    system_prompt = Column(Text, default="")
    resources = Column(JSON, default=dict)

    scope = Column(String(20), default="private", index=True)
    is_builtin = Column(Boolean, default=False)
    enabled = Column(Boolean, default=True)
    created_by = Column(String(100), nullable=True)

    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    def __repr__(self) -> str:
        return f"<Skill {self.name} scope={self.scope} mode={self.execution_mode}>"
