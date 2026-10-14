"""用户反馈收集 — 点赞/踩 → 存储 + 触发质量评测。

数据飞轮入口：
  用户操作 → FeedbackCollector.submit()
    → 写 MySQL（持久化）
    → 发 Kafka lea.feedback（异步触发评测 + 分析）
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from enum import Enum
from typing import Any

import structlog
from pydantic import BaseModel, Field
from sqlalchemy import Column, DateTime, Float, Integer, String, Text, func
from sqlalchemy.orm import DeclarativeBase

logger = structlog.get_logger(__name__)


# ── Data Models ──────────────────────────────────────────


class FeedbackType(str, Enum):
    LIKE = "like"
    DISLIKE = "dislike"


class FeedbackSubmission(BaseModel):
    conversation_id: str
    message_id: str
    feedback_type: FeedbackType
    tenant_id: str = "default"
    user_id: str = ""
    comment: str = ""
    tags: list[str] = Field(default_factory=list)


class FeedbackRecord(BaseModel):
    id: int = 0
    conversation_id: str
    message_id: str
    feedback_type: FeedbackType
    tenant_id: str
    user_id: str = ""
    comment: str = ""
    tags: list[str] = Field(default_factory=list)
    score: float | None = None
    evaluated: bool = False
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


# ── SQLAlchemy Model ─────────────────────────────────────


class FeedbackBase(DeclarativeBase):
    pass


class FeedbackModel(FeedbackBase):
    __tablename__ = "feedback"

    id = Column(Integer, primary_key=True, autoincrement=True)
    conversation_id = Column(String(100), nullable=False, index=True)
    message_id = Column(String(100), nullable=False, index=True)
    feedback_type = Column(String(20), nullable=False)
    tenant_id = Column(String(50), nullable=False, index=True)
    user_id = Column(String(100), default="")
    comment = Column(Text, default="")
    tags = Column(Text, default="[]")
    score = Column(Float, nullable=True)
    evaluated = Column(Integer, default=0)
    created_at = Column(DateTime, default=func.now())


# ── Collector ────────────────────────────────────────────


class FeedbackCollector:
    """反馈收集器 — 写 DB + 发 Kafka 事件。"""

    def __init__(self, db_session_factory=None, event_producer=None):
        self._db_factory = db_session_factory
        self._producer = event_producer

    async def submit(self, feedback: FeedbackSubmission) -> FeedbackRecord:
        """提交一条用户反馈。"""
        record = FeedbackRecord(
            conversation_id=feedback.conversation_id,
            message_id=feedback.message_id,
            feedback_type=feedback.feedback_type,
            tenant_id=feedback.tenant_id,
            user_id=feedback.user_id,
            comment=feedback.comment,
            tags=feedback.tags,
        )

        if self._db_factory:
            record = await self._save_to_db(record)

        if self._producer:
            await self._send_event(record)

        logger.info(
            "feedback_submitted",
            message_id=feedback.message_id,
            type=feedback.feedback_type.value,
            tenant=feedback.tenant_id,
        )
        return record

    async def _save_to_db(self, record: FeedbackRecord) -> FeedbackRecord:
        import json
        session = self._db_factory()
        try:
            model = FeedbackModel(
                conversation_id=record.conversation_id,
                message_id=record.message_id,
                feedback_type=record.feedback_type.value,
                tenant_id=record.tenant_id,
                user_id=record.user_id,
                comment=record.comment,
                tags=json.dumps(record.tags, ensure_ascii=False),
            )
            session.add(model)
            session.commit()
            record.id = model.id
        finally:
            session.close()
        return record

    async def _send_event(self, record: FeedbackRecord) -> None:
        try:
            await self._producer.send(
                topic="lea.feedback",
                value={
                    "feedback_id": record.id,
                    "conversation_id": record.conversation_id,
                    "message_id": record.message_id,
                    "feedback_type": record.feedback_type.value,
                    "tenant_id": record.tenant_id,
                    "timestamp": time.time(),
                },
            )
        except Exception:
            logger.exception("feedback_event_send_failed")

    async def get_stats(
        self, tenant_id: str, days: int = 7
    ) -> dict[str, Any]:
        """获取租户反馈统计。"""
        if not self._db_factory:
            return {}
        session = self._db_factory()
        try:
            from datetime import timedelta
            since = datetime.now(UTC) - timedelta(days=days)
            likes = (
                session.query(func.count(FeedbackModel.id))
                .filter(
                    FeedbackModel.tenant_id == tenant_id,
                    FeedbackModel.feedback_type == "like",
                    FeedbackModel.created_at >= since,
                )
                .scalar()
            )
            dislikes = (
                session.query(func.count(FeedbackModel.id))
                .filter(
                    FeedbackModel.tenant_id == tenant_id,
                    FeedbackModel.feedback_type == "dislike",
                    FeedbackModel.created_at >= since,
                )
                .scalar()
            )
            total = likes + dislikes
            return {
                "likes": likes,
                "dislikes": dislikes,
                "total": total,
                "satisfaction_rate": likes / total if total else 0.0,
                "period_days": days,
            }
        finally:
            session.close()
