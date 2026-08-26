"""数据库连接和会话管理 — lea-rag 自包含版。"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from lea_rag.config import get_database_settings


class Base(DeclarativeBase):
    pass


_settings = get_database_settings()

_engine_kwargs: dict = {"echo": False}
if _settings.url.startswith("sqlite"):
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
else:
    _engine_kwargs.update(
        pool_pre_ping=True, pool_recycle=3600, pool_size=10, max_overflow=20
    )

engine = create_engine(_settings.url, **_engine_kwargs)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
