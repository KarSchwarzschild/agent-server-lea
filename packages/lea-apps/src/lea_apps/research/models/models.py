"""综述任务数据库模型。"""

from __future__ import annotations

from sqlalchemy import Column, DateTime, Integer, String, Text, func

from lea_server.core.tenant import TenantMixin
from lea_server.database import Base


class ReviewTask(TenantMixin, Base):
    __tablename__ = "review_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    title = Column(String(200), nullable=False)
    topic = Column(Text, nullable=False)
    requirements = Column(Text, default="")
    status = Column(String(20), default="pending", index=True)
    phase = Column(String(20), default="init")
    thread_id = Column(String(100), index=True)
    outline = Column(Text, default="")
    review_content = Column(Text, default="")
    error = Column(Text, default="")
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
