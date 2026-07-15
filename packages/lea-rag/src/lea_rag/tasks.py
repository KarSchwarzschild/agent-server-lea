"""检索服务 Celery App — 文档处理异步任务队列。

broker 和 result backend 都走 Redis（复用平台已有的 Redis 实例）。
独立于主后端的 Celery，用不同的 task queue 隔离。

启动 worker：
  celery -A lea_rag.tasks worker --loglevel=info --pool=solo -Q retrieval
"""

from __future__ import annotations

import os

from celery import Celery

_redis_url = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "lea_retrieval",
    broker=_redis_url,
    backend=_redis_url,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_default_queue="retrieval",
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_track_started=True,
)


@celery_app.task(name="lea_rag.tasks.process_document", bind=True, max_retries=2)
def process_document(self, document_id: int) -> dict:
    """处理单个文档：解析 → 分块 → 向量化 → 入库。"""
    import structlog
    logger = structlog.get_logger()

    try:
        from lea_server.database import SessionLocal
        from lea_server.services.kb_processor import DocumentProcessor

        db = SessionLocal()
        try:
            processor = DocumentProcessor(db)
            processor.process(document_id)
            return {"status": "done", "document_id": document_id}
        finally:
            db.close()

    except Exception as exc:
        logger.error("task_process_failed", document_id=document_id, error=str(exc))
        raise self.retry(exc=exc, countdown=10)
