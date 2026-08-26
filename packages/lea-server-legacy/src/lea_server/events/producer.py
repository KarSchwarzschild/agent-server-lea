"""
Kafka Producer 封装。

支持同步/异步发送，自动 JSON 序列化，失败重试。
开发环境 Kafka 不可用时降级为日志输出（不阻塞业务）。
"""

from __future__ import annotations

import json
import time
from functools import lru_cache
from typing import Any

import structlog
from pydantic import Field
from pydantic_settings import BaseSettings

from lea_server.events.topics import Topic

logger = structlog.get_logger()


class KafkaSettings(BaseSettings):
    kafka_bootstrap_servers: str = Field(default="localhost:9092", alias="KAFKA_BOOTSTRAP_SERVERS")
    kafka_enabled: bool = Field(default=False, alias="KAFKA_ENABLED")

    model_config = {"extra": "ignore"}


@lru_cache
def _get_kafka_settings() -> KafkaSettings:
    return KafkaSettings()


class EventProducer:
    """Kafka 事件生产者。

    Kafka 不可用时自动降级为 structlog 日志，不影响业务主流程。
    """

    def __init__(self):
        self._producer = None
        self._settings = _get_kafka_settings()

        if self._settings.kafka_enabled:
            try:
                from kafka import KafkaProducer

                self._producer = KafkaProducer(
                    bootstrap_servers=self._settings.kafka_bootstrap_servers.split(","),
                    value_serializer=lambda v: json.dumps(v, ensure_ascii=False, default=str).encode(),
                    key_serializer=lambda k: k.encode() if k else None,
                    retries=3,
                    acks="all",
                    max_block_ms=5000,
                )
                logger.info("kafka_producer_connected", servers=self._settings.kafka_bootstrap_servers)
            except Exception as e:
                logger.warning("kafka_producer_fallback", error=str(e))
                self._producer = None

    def send(self, topic: Topic, value: dict[str, Any], key: str | None = None) -> None:
        event = {
            **value,
            "_topic": topic.value,
            "_timestamp": time.time(),
        }

        if self._producer is not None:
            try:
                self._producer.send(topic.value, value=event, key=key)
                return
            except Exception as e:
                logger.warning("kafka_send_failed", topic=topic.value, error=str(e))

        logger.info("event_emitted", topic=topic.value, payload=event)

    def send_audit(self, action: str, tenant_id: str, user_id: str, **details) -> None:
        self.send(
            Topic.AUDIT,
            {"action": action, "tenant_id": tenant_id, "user_id": user_id, **details},
            key=tenant_id,
        )

    def send_metrics(self, metric_name: str, value: float, tenant_id: str, **tags) -> None:
        self.send(
            Topic.METRICS,
            {"metric": metric_name, "value": value, "tenant_id": tenant_id, **tags},
            key=tenant_id,
        )

    def send_feedback(self, tenant_id: str, session_id: str, rating: int, **details) -> None:
        self.send(
            Topic.FEEDBACK,
            {"tenant_id": tenant_id, "session_id": session_id, "rating": rating, **details},
            key=tenant_id,
        )

    def close(self):
        if self._producer is not None:
            self._producer.flush()
            self._producer.close()


_producer_instance: EventProducer | None = None


def get_producer() -> EventProducer:
    global _producer_instance
    if _producer_instance is None:
        _producer_instance = EventProducer()
    return _producer_instance
