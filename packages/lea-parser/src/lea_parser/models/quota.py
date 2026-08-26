"""配额管理 — 租户级 + 用户级限制。"""

from sqlalchemy import Column, DateTime, Integer, String, func

from lea_parser.database import Base, TenantMixin


class TenantQuota(TenantMixin, Base):
    __tablename__ = "parser_tenant_quotas"

    id = Column(Integer, primary_key=True, autoincrement=True)
    max_calls_per_hour = Column(Integer, default=500)
    max_file_size_mb = Column(Integer, default=200)
    max_monthly_calls = Column(Integer, default=50000)
    max_monthly_size_mb = Column(Integer, default=10000)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class UserQuota(TenantMixin, Base):
    __tablename__ = "parser_user_quotas"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String(100), nullable=False, index=True)
    max_calls_per_hour = Column(Integer, default=100)
    max_file_size_mb = Column(Integer, default=50)
    max_monthly_calls = Column(Integer, default=5000)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
