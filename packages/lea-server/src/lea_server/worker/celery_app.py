"""
Celery 应用配置。

Broker 和 Backend 都使用 Redis，支持任务状态持久化和断线恢复。
提供两个优先级队列：high（用户在线请求）、low（后台批量任务）。
"""

from __future__ import annotations

from celery import Celery

from lea_server.config import get_settings

settings = get_settings()

celery_app = Celery(
    "lea_worker",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    result_expires=86400,
    task_default_queue="high",
    task_queues={
        "high": {"exchange": "high", "routing_key": "high"},
        "low": {"exchange": "low", "routing_key": "low"},
    },
    task_routes={
        "lea_server.worker.tasks.generate_review": {"queue": "high"},
        "lea_server.worker.tasks.batch_import": {"queue": "low"},
        "lea_server.worker.tasks.run_evaluation": {"queue": "low"},
    },
    beat_schedule={},
)

celery_app.autodiscover_tasks(["lea_server.worker"])
