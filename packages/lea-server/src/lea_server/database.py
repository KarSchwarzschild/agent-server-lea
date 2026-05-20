"""数据库连接和会话管理。"""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from lea_server.config import get_settings


class Base(DeclarativeBase):
    """所有ORM模型的基类。App模型和平台模型都继承自此。"""

    pass


_settings = get_settings()

_engine_kwargs: dict = {"echo": False}
if _settings.database_url.startswith("sqlite"):
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    _engine_kwargs.update(pool_pre_ping=True, pool_recycle=3600, pool_size=10, max_overflow=20)

engine = create_engine(_settings.database_url, **_engine_kwargs)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Session:
    """FastAPI依赖：获取数据库会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
