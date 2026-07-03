"""用户反馈 Consumer — 数据飞轮入口。

收到差评（rating <= 2）时触发 LLM-as-Judge 自动评测。
"""

from __future__ import annotations

from typing import Any

import structlog

from lea_server.events.consumer import BaseConsumer
from lea_server.events.topics import Topic

logger = structlog.get_logger()

POOR_RATING_THRESHOLD = 2


class FeedbackConsumer(BaseConsumer):
    topic = Topic.FEEDBACK
    group_id = "lea-feedback-consumer"

    def handle_message(self, message: dict[str, Any]) -> None:
        rating = message.get("rating", 0)
        session_id = message.get("session_id", "")
        tenant_id = message.get("tenant_id", "")

        logger.info("feedback_received", rating=rating, session_id=session_id, tenant_id=tenant_id)

        if rating <= POOR_RATING_THRESHOLD:
            logger.warning("poor_feedback_detected", session_id=session_id, rating=rating)
            # TODO: Phase 3 接入 lea-data 的 LLM-as-Judge 评测
