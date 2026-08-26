"""
Kafka Consumer 基类。

每个 handler（audit、metrics、feedback）继承 BaseConsumer，
实现 handle_message()。通过 CLI 或独立进程启动消费。
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any

import structlog

from lea_server.events.topics import Topic

logger = structlog.get_logger()


class BaseConsumer(ABC):
    """Kafka Consumer 基类。"""

    topic: Topic
    group_id: str

    def __init__(self):
        self._consumer = None

    def start(self):
        try:
            from kafka import KafkaConsumer

            settings_mod = __import__("lea_server.events.producer", fromlist=["_get_kafka_settings"])
            settings = settings_mod._get_kafka_settings()

            self._consumer = KafkaConsumer(
                self.topic.value,
                bootstrap_servers=settings.kafka_bootstrap_servers.split(","),
                group_id=self.group_id,
                value_deserializer=lambda m: json.loads(m.decode()),
                auto_offset_reset="earliest",
                enable_auto_commit=True,
                auto_commit_interval_ms=5000,
            )
            logger.info("consumer_started", topic=self.topic.value, group=self.group_id)

            for message in self._consumer:
                try:
                    self.handle_message(message.value)
                except Exception as e:
                    logger.error(
                        "consumer_handle_error",
                        topic=self.topic.value,
                        error=str(e),
                        offset=message.offset,
                    )

        except KeyboardInterrupt:
            logger.info("consumer_stopped", topic=self.topic.value)
        finally:
            if self._consumer:
                self._consumer.close()

    @abstractmethod
    def handle_message(self, message: dict[str, Any]) -> None:
        ...
