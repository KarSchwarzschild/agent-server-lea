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
            sections = self._extract_sections(doc, kb, parse_result)
            parse_result["sections"] = sections
            chunks = self._save_chunks(doc, kb, parse_result)
            self._index_vectors(doc, kb, chunks)
            self._index_images(doc, kb, parse_result)
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

    def _extract_sections(self, doc: Document, kb: KnowledgeBase, parse_result: dict) -> list:
        """从 MinerU 解析结果提取章节结构，写入 DB。"""
        from lea_server.models.knowledge import DocumentSection

        content_list = parse_result.get("content_list", [])
        if not content_list:
            return []

        sections = []
        stack: list[tuple[int, Any]] = []

        for block in content_list:
            block_type = block.get("type", "")
            if block_type not in ("heading", "title"):
                continue

            level = block.get("level", 1)
            title = block.get("content", "").strip()
            if not title:
                continue

            while stack and stack[-1][0] >= level:
                stack.pop()

            parent_id = stack[-1][1].id if stack else None
            section = DocumentSection(
                document_id=doc.id,
                kb_id=kb.id,
                parent_id=parent_id,
                level=level,
                title=title,
                page_start=block.get("page", 0),
                position=len(sections),
            )
            self.db.add(section)
            self.db.flush()
            sections.append(section)
            stack.append((level, section))

        if sections:
            logger.info("sections_extracted", document_id=doc.id, count=len(sections))
        return sections

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
        """保存分块到数据库，包含章节关联。"""
        from lea_server.services.kb_service import KBService

        text_chunks = parse_result.get("text_chunks", [])
        sections = parse_result.get("sections", [])
        section_map = self._build_section_map(sections, parse_result.get("content_list", []))

        chunk_dicts = []
        for chunk in text_chunks:
            page = chunk.get("page", 0)
            section_id = self._find_section_for_page(section_map, page)
            chunk_dicts.append({
                "content": chunk["content"],
                "chunk_type": chunk.get("type", "text"),
                "window_content": chunk.get("window_content", ""),
                "section_id": section_id,
                "metadata": {
                    "page": page,
                    "kb_id": kb.id,
                    "document_id": doc.id,
                    "section_id": section_id or 0,
                },
            })

        KBService(self.db).save_chunks(doc.id, kb.id, chunk_dicts)
        return chunk_dicts

    def _build_section_map(self, sections: list, content_list: list[dict]) -> list[tuple[int, int, int]]:
        """构建 (page_start, page_end, section_id) 映射。"""
        if not sections:
            return []
        result = []
        for i, sec in enumerate(sections):
            page_start = sec.page_start
            page_end = sections[i + 1].page_start - 1 if i + 1 < len(sections) else 9999
            result.append((page_start, page_end, sec.id))
        return result

    def _find_section_for_page(self, section_map: list[tuple[int, int, int]], page: int) -> int | None:
        for start, end, sid in section_map:
            if start <= page <= end:
                return sid
        return None

    def _index_vectors(self, doc: Document, kb: KnowledgeBase, chunks: list[dict]) -> None:
        """将分块向量化并索引到 Milvus v2 (dense + BM25)。"""
        try:
            from lea_rag.index.milvus_index import MilvusKBIndex

            embedder = self._get_embedder(kb)
            index = MilvusKBIndex()

            text_contents = [c.get("window_content") or c["content"] for c in chunks]
            if not text_contents:
                return

            vectors = embedder.embed_texts(text_contents)

            rows = []
            for i, chunk in enumerate(chunks):
                rows.append({
                    "content": chunk.get("window_content") or chunk["content"],
                    "dense_vector": vectors[i],
                    "kb_id": kb.id,
                    "document_id": doc.id,
                    "section_id": chunk.get("section_id") or 0,
                    "tenant_id": kb.tenant_id,
                    "chunk_type": chunk.get("chunk_type", "text"),
                    "source_filename": doc.filename,
                    "source_url": doc.file_key,
                    "page": chunk.get("metadata", {}).get("page", 0),
                })

            index.insert(rows)
            logger.info("vectors_indexed_v2", document_id=doc.id, count=len(rows))

        except Exception as exc:
            logger.warning("vector_index_failed", document_id=doc.id, error=str(exc))

    def _index_images(self, doc: Document, kb: KnowledgeBase, parse_result: dict) -> None:
        """处理图片：VLM 生成描述（vlm_enabled）+ 原生图片 embed（multimodal）。"""
        import base64

        image_items = parse_result.get("image_items", [])
        if not image_items:
            return

        if not getattr(kb, "vlm_enabled", False) and not getattr(kb, "multimodal", False):
            return

        try:
            from lea_rag.index.milvus_index import MilvusKBIndex
            index = MilvusKBIndex()
            embedder = self._get_embedder(kb)
            rows = []

            for item in image_items:
                b64 = item.get("base64", "")
                if not b64:
                    continue
                img_bytes = base64.b64decode(b64)
                page = item.get("page_idx", item.get("page", 0))
                caption = item.get("caption", "")

                if getattr(kb, "vlm_enabled", False) and not caption:
                    caption = self._vlm_caption(kb, img_bytes)

                if getattr(kb, "multimodal", False):
                    try:
                        vec = embedder.embed_images([img_bytes])[0]
                        rows.append({
                            "content": caption or f"[图片 p{page}]",
                            "dense_vector": vec,
                            "kb_id": kb.id,
                            "document_id": doc.id,
                            "section_id": 0,
                            "tenant_id": kb.tenant_id,
                            "chunk_type": "image",
                            "source_filename": doc.filename,
                            "source_url": f"{doc.file_key}#page={page}",
                            "page": page,
                        })
                    except Exception as exc:
                        logger.warning("image_embed_skip", page=page, error=str(exc))
                elif caption:
                    vec = embedder.embed_texts([caption])[0]
                    rows.append({
                        "content": caption,
                        "dense_vector": vec,
                        "kb_id": kb.id,
                        "document_id": doc.id,
                        "section_id": 0,
                        "tenant_id": kb.tenant_id,
                        "chunk_type": "image_caption",
                        "source_filename": doc.filename,
                        "source_url": f"{doc.file_key}#page={page}",
                        "page": page,
                    })

            if rows:
                index.insert(rows)
                logger.info("images_indexed", document_id=doc.id, count=len(rows))

        except Exception as exc:
            logger.warning("image_index_failed", document_id=doc.id, error=str(exc))

    def _update_success(self, doc: Document, parse_result: dict, chunk_count: int) -> None:
        """更新文档状态为完成。"""
        md_content = parse_result.get("md_content", "")
        doc.status = "ready"
        doc.parsed_content = md_content[:50000]
        doc.chunk_count = chunk_count
        doc.word_count = len(md_content)
        doc.total_pages = md_content.count("\n\n") + 1
        self.db.flush()

    def _get_embedder(self, kb: KnowledgeBase):
        """根据 KB 配置从模型注册表获取 embedding 客户端。"""
        from lea_rag.embeddings.embedding_client import EmbeddingClient
        from lea_server.models.knowledge import RetrievalModel

        model_name = kb.embedding_model
        if model_name:
            m = self.db.query(RetrievalModel).filter(
                RetrievalModel.name == model_name,
                RetrievalModel.enabled == True,
            ).first()
            if m:
                return EmbeddingClient(api_base=m.api_base, model=m.model_name or m.name, api_key=m.api_key)

        m = self.db.query(RetrievalModel).filter(
            RetrievalModel.model_type == "embedding",
            RetrievalModel.is_default == True,
            RetrievalModel.enabled == True,
        ).first()
        if m:
            return EmbeddingClient(api_base=m.api_base, model=m.model_name or m.name, api_key=m.api_key)

        return EmbeddingClient()

    def _vlm_caption(self, kb: KnowledgeBase, img_bytes: bytes) -> str:
        """调用 VLM 模型生成图片描述。"""
        import base64
        import httpx
        from lea_server.models.knowledge import RetrievalModel

        vlm_name = getattr(kb, "vlm_model", "") or ""
        if not vlm_name:
            m = self.db.query(RetrievalModel).filter(
                RetrievalModel.model_type == "vlm",
                RetrievalModel.is_default == True,
                RetrievalModel.enabled == True,
            ).first()
        else:
            m = self.db.query(RetrievalModel).filter(
                RetrievalModel.name == vlm_name,
                RetrievalModel.enabled == True,
            ).first()

        if not m:
            return ""

        b64_img = base64.b64encode(img_bytes).decode()
        try:
            headers = {"Content-Type": "application/json"}
            if m.api_key:
                headers["Authorization"] = f"Bearer {m.api_key}"
            resp = httpx.post(
                f"{m.api_base.rstrip('/')}/v1/chat/completions",
                headers=headers,
                json={
                    "model": m.model_name or m.name,
                    "messages": [{
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "请详细描述这张图片的内容，用于文档检索。"},
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64_img}"}},
                        ],
                    }],
                    "max_tokens": 300,
                },
                timeout=30.0,
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except Exception as exc:
            logger.warning("vlm_caption_failed", error=str(exc))
            return ""
