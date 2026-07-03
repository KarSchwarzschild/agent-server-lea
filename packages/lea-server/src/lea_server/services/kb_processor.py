"""文档处理器 — 解析 → 分块 → 向量化 → 入库。

复用 Notebook 的 MinerU/PyMuPDF 解析逻辑和 lea-rag 的 Milvus 索引。
设计为 Celery 任务调用。
"""

from __future__ import annotations

import os
import tempfile
from typing import Any

import structlog
from sqlalchemy.orm import Session

from lea_server.models.knowledge import Document, KnowledgeBase

logger = structlog.get_logger()


class DocumentProcessor:
    """文档处理 Pipeline。"""

    def __init__(self, db: Session):
        self.db = db

    def process(self, document_id: int) -> None:
        """处理单个文档：解析 → 分块 → 向量化。"""
        from lea_server.services.kb_service import KBService

        svc = KBService(self.db)
        doc = svc.get_document(document_id)
        if doc is None:
            logger.error("document_not_found", document_id=document_id)
            return

        kb = svc.get_kb(doc.kb_id)
        if kb is None:
            svc.update_document_status(document_id, "error", error_message="知识库不存在")
            self.db.commit()
            return

        try:
            file_path = self._download_file(doc)
            parse_result = self._parse(file_path, doc.file_type)
            chunks = self._save_chunks(doc, kb, parse_result)
            self._index_vectors(doc, kb, chunks)
            self._update_success(doc, parse_result, len(chunks))
            self.db.commit()
            logger.info("document_processed", document_id=document_id, chunks=len(chunks))

        except Exception as exc:
            self.db.rollback()
            svc.update_document_status(document_id, "error", error_message=str(exc))
            self.db.commit()
            logger.error("document_process_failed", document_id=document_id, error=str(exc))

        finally:
            if 'file_path' in locals() and os.path.exists(file_path):
                os.unlink(file_path)

    def _download_file(self, doc: Document) -> str:
        """从 MinIO 下载文件到临时路径。"""
        from lea_server.services.object_storage import ObjectStorage

        storage = ObjectStorage()
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=f".{doc.file_type}")
        tmp_path = tmp.name
        tmp.close()

        data = storage.get_object(doc.file_key)
        with open(tmp_path, "wb") as f:
            f.write(data)

        return tmp_path

    def _parse(self, file_path: str, file_type: str) -> dict:
        """解析文档，复用 Notebook 的 parser 逻辑。"""
        from lea_apps.notebook.services.parser import parse_document

        if file_type in ("pdf",):
            return parse_document(file_path, parser="mineru")

        from lea_apps.notebook.services.parser import _chunk_md_fallback
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()
        chunks = _chunk_md_fallback(content)
        return {"md_content": content, "text_chunks": chunks, "image_items": []}

    def _save_chunks(self, doc: Document, kb: KnowledgeBase, parse_result: dict) -> list[dict]:
        """保存分块到数据库。"""
        from lea_server.services.kb_service import KBService

        text_chunks = parse_result.get("text_chunks", [])
        chunk_dicts = []
        for chunk in text_chunks:
            chunk_dicts.append({
                "content": chunk["content"],
                "chunk_type": chunk.get("type", "text"),
                "window_content": chunk.get("window_content", ""),
                "metadata": {
                    "page": chunk.get("page", 0),
                    "kb_id": kb.id,
                    "document_id": doc.id,
                },
            })

        KBService(self.db).save_chunks(doc.id, kb.id, chunk_dicts)
        return chunk_dicts

    def _index_vectors(self, doc: Document, kb: KnowledgeBase, chunks: list[dict]) -> None:
        """将分块向量化并索引到 Milvus。"""
        try:
            from lea_rag.index.vector_index import MilvusRAGIndex
            from llama_index.core.schema import Document as LIDocument

            index = MilvusRAGIndex(collection_name="lea_knowledge")

            li_docs = []
            for i, chunk in enumerate(chunks):
                metadata = chunk.get("metadata", {})
                metadata["kb_id"] = str(kb.id)
                metadata["document_id"] = str(doc.id)
                metadata["tenant_id"] = kb.tenant_id
                metadata["source_filename"] = doc.filename

                li_docs.append(LIDocument(
                    text=chunk.get("window_content") or chunk["content"],
                    metadata=metadata,
                    id_=f"kb{kb.id}_doc{doc.id}_chunk{i}",
                ))

            index.insert_documents(li_docs)
            logger.info("vectors_indexed", document_id=doc.id, count=len(li_docs))

        except Exception as exc:
            logger.warning("vector_index_failed", document_id=doc.id, error=str(exc))

    def _update_success(self, doc: Document, parse_result: dict, chunk_count: int) -> None:
        """更新文档状态为完成。"""
        md_content = parse_result.get("md_content", "")
        doc.status = "ready"
        doc.parsed_content = md_content[:50000]
        doc.chunk_count = chunk_count
        doc.word_count = len(md_content)
        doc.total_pages = md_content.count("\n\n") + 1
        self.db.flush()
