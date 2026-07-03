"""文档解析异步任务 — 解析 PDF → 切块 → 向量化 → 摘要生成。"""

from __future__ import annotations

import logging
import os
import tempfile

import redis

from lea_server.tasks.celery_app import app
from lea_server.config import get_settings

logger = logging.getLogger(__name__)

_redis = redis.from_url(get_settings().redis_url)

SUMMARY_PROMPT = (
    "请阅读以下文档内容，输出一段 100-200 字的中文摘要，以及 3-5 个关键主题词。\n"
    "严格以 JSON 格式返回：{{\"summary\": \"...\", \"key_topics\": [\"...\", ...]}}\n\n"
    "文档内容（截取前 3000 字）：\n{text}"
)


def _generate_summary(md_content: str) -> dict:
    """用 LLM 生成文档摘要和关键主题。"""
    import json

    from lea_graph.llm import get_chat_model

    llm = get_chat_model("fallback", streaming=False, temperature=0.3)
    truncated = md_content[:3000]
    resp = llm.invoke(SUMMARY_PROMPT.format(text=truncated))

    try:
        data = json.loads(resp.content)
        return {
            "summary": data.get("summary", ""),
            "key_topics": data.get("key_topics", []),
        }
    except (json.JSONDecodeError, AttributeError):
        return {"summary": str(resp.content)[:500], "key_topics": []}


@app.task(
    bind=True,
    max_retries=2,
    default_retry_delay=30,
    acks_late=True,
)
def process_upload(self, source_id: int, object_key: str, filename: str):
    """Celery task: 解析 → 分块 → 向量化 → 摘要。

    幂等保障：Redis 分布式锁 + DB 状态检查。
    """
    from lea_server.database import SessionLocal

    db = SessionLocal()
    lock_key = f"processing:source:{source_id}"
    local_path: str | None = None

    try:
        if not _redis.set(lock_key, "1", nx=True, ex=600):
            logger.info("source %s 正在被其他 worker 处理，跳过", source_id)
            return {"status": "skipped", "reason": "duplicate"}

        from lea_apps.notebook.models.models import NotebookChunk, NotebookSource

        source = db.query(NotebookSource).get(source_id)
        if not source:
            return {"status": "error", "reason": "not_found"}
        if source.status == "ready":
            return {"status": "skipped", "reason": "already_done"}

        from lea_server.services.object_storage import get_object_storage

        file_data = get_object_storage().get_object(object_key)
        suffix = f".{filename.rsplit('.', 1)[-1]}" if "." in filename else ""
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        tmp.write(file_data)
        tmp.close()
        local_path = tmp.name

        from lea_apps.notebook.services.parser import parse_document

        result = parse_document(local_path, parser="mineru")
        md_content = result.get("md_content", "").replace("\x00", "")
        text_chunks = result.get("text_chunks", [])

        if md_content:
            source.parsed_content = md_content
            try:
                summary_data = _generate_summary(md_content)
                source.summary = summary_data["summary"]
                source.key_topics = summary_data["key_topics"]
            except Exception as e:
                logger.warning("摘要生成失败: %s", e)
            db.commit()

        chunk_ids: list[int] = []
        chunk_contents: list[str] = []
        chunk_windows: list[str] = []
        chunk_pages: list[int] = []

        for chunk in text_chunks:
            content = chunk["content"].replace("\x00", "")
            window = (chunk.get("window_content") or "").replace("\x00", "")
            db_chunk = NotebookChunk(
                source_id=source_id,
                content=content,
                chunk_type=chunk.get("type", "text"),
                window_content=window,
                extra={
                    "parser": "mineru",
                    "page": chunk.get("page", 0),
                },
            )
            db.add(db_chunk)
            db.flush()
            chunk_ids.append(db_chunk.id)
            chunk_contents.append(content)
            chunk_windows.append(window)
            chunk_pages.append(chunk.get("page", 0))

        db.commit()

        if chunk_ids:
            from lea_apps.notebook.services.indexer import index_text_chunks
            index_text_chunks(
                chunk_ids, chunk_contents, source_id,
                windows=chunk_windows, pages=chunk_pages,
            )

        source.status = "ready"
        source.total_pages = max((c.get("page", 0) for c in text_chunks), default=0)
        db.commit()

        logger.info("[Upload] %s 完成: %d chunks", filename, len(chunk_ids))
        return {"status": "done", "text_chunks": len(chunk_ids)}

    except Exception as exc:
        logger.error("[Upload] %s 失败: %s", filename, exc)
        db.rollback()
        try:
            source = db.query(NotebookSource).filter(NotebookSource.id == source_id).first()
            if source:
                source.status = "failed"
                db.commit()
        except Exception:
            db.rollback()

        raise self.retry(exc=exc, countdown=30 * (2 ** self.request.retries))

    finally:
        if local_path and os.path.exists(local_path):
            os.remove(local_path)
        _redis.delete(lock_key)
        db.close()
