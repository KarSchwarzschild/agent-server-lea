"""Celery 异步任务队列 — 综述生成、批量导入等长任务。"""

from lea_server.worker.celery_app import celery_app

__all__ = ["celery_app"]
