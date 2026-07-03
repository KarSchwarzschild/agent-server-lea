"""Kafka Topic 定义。"""

from __future__ import annotations

from enum import Enum


class Topic(str, Enum):
    AUDIT = "lea.audit"
    METRICS = "lea.metrics"
    FEEDBACK = "lea.feedback"
    TASK_STATUS = "lea.task_status"
