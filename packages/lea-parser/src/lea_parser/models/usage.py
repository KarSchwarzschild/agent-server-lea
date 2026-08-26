"""用量记录 — 每次解析调用产生一条。"""

from sqlalchemy import Column, DateTime, Float, Integer, String, func

from lea_parser.database import Base, TenantMixin


class UsageRecord(TenantMixin, Base):
    __tablename__ = "parser_usage_records"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(100), nullable=False, index=True)
    task_id = Column(String(64), nullable=False, index=True)
    filename = Column(String(500), default="")
    file_format = Column(String(20), default="")
    file_size = Column(Integer, default=0)
    chunks_produced = Column(Integer, default=0)
    duration_ms = Column(Integer, default=0)
    cost_units = Column(Float, default=0.0)
    created_at = Column(DateTime, default=func.now())
