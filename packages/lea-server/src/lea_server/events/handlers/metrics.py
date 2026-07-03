"""指标采集 Consumer — token 用量、延迟、错误率。"""

from __future__ import annotations

from typing import Any

import structlog

from lea_server.events.consumer import BaseConsumer
from lea_server.events.topics import Topic

logger = structlog.get_logger()


class MetricsConsumer(BaseConsumer):
    topic = Topic.METRICS
    group_id = "lea-metrics-consumer"

    def handle_message(self, message: dict[str, Any]) -> None:
        logger.info(
            "metric_event",
            metric=message.get("metric"),
            value=message.get("value"),
            tenant_id=message.get("tenant_id"),
        )
        # TODO: 写入 metrics 时序表或推送到 Prometheus
