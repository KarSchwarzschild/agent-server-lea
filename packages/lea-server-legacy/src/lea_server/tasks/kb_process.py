"""知识库文档处理任务 — 解析 → 分块 → 向量化。"""

from __future__ import annotations

import logging

from lea_server.tasks.celery_app import app

logger = logging.getLogger(__name__)


@app.task(name="lea_server.tasks.kb_process", bind=True, max_retries=2)
def process_document(self, document_id: int) -> dict:
    """异步处理文档：解析 + 分块 + 向量索引。"""
    from lea_server.database import SessionLocal
    from lea_server.services.kb_processor import DocumentProcessor

    logger.info(f"Processing document {document_id}")

    db = SessionLocal()
    try:
        processor = DocumentProcessor(db)
        processor.process(document_id)
        return {"status": "ok", "document_id": document_id}
    except Exception as exc:
        logger.error(f"Document {document_id} processing failed: {exc}")
        raise self.retry(exc=exc, countdown=30)
    finally:
        db.close()
