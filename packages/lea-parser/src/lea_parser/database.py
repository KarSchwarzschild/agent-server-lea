"""数据库引擎 + 会话工厂。"""

from sqlalchemy import Column, String, create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from lea_parser.config import get_db_settings


class TenantMixin:
    tenant_id = Column(String(50), nullable=False, index=True, default="default")


class Base(DeclarativeBase):
    pass


_settings = get_db_settings()
engine = create_engine(_settings.url, pool_pre_ping=True, pool_recycle=3600)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
