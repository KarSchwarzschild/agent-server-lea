"""审计日志 Consumer — 将 API 调用和 Agent 决策记录写入数据库。"""

from __future__ import annotations

from typing import Any

import structlog

from lea_server.events.consumer import BaseConsumer
from lea_server.events.topics import Topic

logger = structlog.get_logger()


class AuditConsumer(BaseConsumer):
    topic = Topic.AUDIT
    group_id = "lea-audit-consumer"

    def handle_message(self, message: dict[str, Any]) -> None:
        logger.info(
            "audit_event",
            action=message.get("action"),
            tenant_id=message.get("tenant_id"),
            user_id=message.get("user_id"),
        )
        # TODO: 写入 audit_logs 表
