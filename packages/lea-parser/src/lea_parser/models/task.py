"""解析任务跟踪。"""

from sqlalchemy import Column, DateTime, Integer, JSON, String, Text, func

from lea_parser.database import Base, TenantMixin


class ParseTask(TenantMixin, Base):
    __tablename__ = "parse_tasks"

    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String(64), unique=True, nullable=False, index=True)
    user_id = Column(String(100), nullable=False, index=True)
    status = Column(String(20), default="pending", index=True)
    filename = Column(String(500), default="")
    file_format = Column(String(20), default="")
    file_size = Column(Integer, default=0)
    chunk_strategy = Column(String(30), default="sentence_window")
    chunk_size = Column(Integer, default=200)
    result = Column(JSON, nullable=True)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=func.now())
    completed_at = Column(DateTime, nullable=True)
