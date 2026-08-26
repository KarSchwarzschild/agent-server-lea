"""Celery 应用实例 — notebook 后台任务。

启动 worker:
  celery -A lea_apps.notebook.tasks.celery_app worker --loglevel=info --pool=solo
"""

from __future__ import annotations

from celery import Celery

from lea_server.config import get_settings

_settings = get_settings()

app = Celery(
    "notebook",
    broker=_settings.redis_url,
    backend=_settings.redis_url,
)

app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)

app.autodiscover_tasks(["lea_apps.notebook.tasks"])
