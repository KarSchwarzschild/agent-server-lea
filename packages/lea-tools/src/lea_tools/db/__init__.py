"""Database engine & session."""

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from lea_tools.config import get_database_settings


class Base(DeclarativeBase):
    pass


_settings = get_database_settings()
engine = create_engine(_settings.url, pool_pre_ping=True, pool_recycle=3600)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

import lea_tools.db.models  # noqa: E402, F401 — ensure models registered with Base


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
