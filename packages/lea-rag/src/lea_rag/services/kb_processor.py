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

from lea_rag.config import RAGSettings
from lea_rag.db.models import Document, KnowledgeBase

logger = structlog.get_logger()


class DocumentProcessor:
    """文档处理 Pipeline。"""

    def __init__(self, db: Session):
        self.db = db

    def process(self, document_id: int) -> None:
        """处理单个文档：解析 → 分块 → 向量化。"""
        from lea_rag.services.kb_service import KBService

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
            self._process_audio(doc, kb, parse_result)
            chunks = self._save_chunks(doc, kb, parse_result)
            self._index_vectors(doc, kb, chunks)
            # 把文本 chunk 传给图片索引：图片要靠同页正文补上下文（见 _image_context）
            self._index_images(doc, kb, parse_result, chunks)
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
        from lea_rag.services.object_storage import ObjectStorage

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
        from lea_rag.db.models import DocumentSection

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
        from lea_rag.parsers.parser import parse_document

        if file_type in (
            "pdf", "docx", "pptx",
            "png", "jpg", "jpeg", "webp", "gif", "bmp",
            "mp3", "wav", "m4a", "flac", "ogg",
        ):
            return parse_document(file_path, parser="mineru")

        from lea_rag.parsers.parser import _chunk_md_fallback
        with open(file_path, encoding="utf-8", errors="ignore") as f:
            content = f.read()
        chunks = _chunk_md_fallback(content)
        return {"md_content": content, "text_chunks": chunks, "image_items": []}

    def _save_chunks(self, doc: Document, kb: KnowledgeBase, parse_result: dict) -> list[dict]:
        """保存分块到数据库，包含章节关联。"""
        from lea_rag.services.kb_service import KBService

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

    @staticmethod
    def _compose_image_content(caption: str, page: int, context: str) -> str:
        """拼图片 chunk 的文本。

        格式刻意把"这是一张图"放在最前面：检索命中之后，调用方和模型都要能
        一眼看出这段是图片而不是正文，否则模型会把 VLM 的描述当成文档原文引用。
        """
        head = caption.strip() or f"[图片 p{page}]"
        if not context:
            return head
        return f"[图片 p{page}] {head}\n\n【图片所在位置的正文】\n{context}"

    @staticmethod
    def _image_page(item: dict) -> int:
        """图片的页码，统一成 1-based。

        解析器给的 page_idx 是 0-based，而文本 chunk 经 chunk_text_blocks 之后是
        page_idx + 1（parser.py:155）。两边不统一的后果有两个，都发生过：

          1. 按页码找同页正文会错位一页，拼进去的是邻页内容
          2. source_url 里的 `#page=` 指向前一页 —— PDF 阅读器的页码是 1-based，
             点引用跳转会跳错

        所以在入口处归一，下面一律按 1-based 处理。兼容两种键名（mineru 给
        page_idx，有的解析器给 page）。
        """
        if "page_idx" in item:
            return int(item.get("page_idx") or 0) + 1
        return int(item.get("page") or 0) or 1

    def _image_context(self, chunks: list[dict], page: int, limit: int) -> str:
        """取同页正文当图片的上下文，不够就往邻页借。

        为什么要往邻页借：图片常出现在页首或页尾，相关的正文（图题、引述那段）
        在上一页末或下一页初。只取同页会让这类图片拿到空上下文。

        按 limit 截断而不是全拼：整页正文可能几千字，而图片描述只有几十字 ——
        全拼进去等于让正文淹掉描述，这个 chunk 就变成了"又一份正文"，失去了
        "这是一张图"的区分度。
        """
        if limit <= 0 or not chunks:
            return ""

        # 先同页，再按页距由近到远
        def distance(c: dict) -> int:
            return abs(int(c.get("metadata", {}).get("page", 0)) - page)

        ordered = sorted(chunks, key=distance)
        parts: list[str] = []
        used = 0
        for c in ordered:
            if distance(c) > 1:  # 只借相邻一页
                break
            text = (c.get("content") or "").strip()
            if not text:
                continue
            room = limit - used
            if room <= 0:
                break
            parts.append(text[:room])
            used += min(len(text), room)
        return "\n\n".join(parts)

    def _index_images(
        self,
        doc: Document,
        kb: KnowledgeBase,
        parse_result: dict,
        chunks: list[dict] | None = None,
    ) -> None:
        """处理图片：VLM 生成描述（vlm_enabled）+ 原生图片 embed（multimodal）。

        chunks 是同一文档的文本 chunk，用来给图片补上下文（见 _image_context）。
        默认 None 是为了兼容旧调用方 —— 不传就退回"只用描述"的老行为。
        """
        import base64

        image_items = parse_result.get("image_items", [])
        if not image_items:
            return

        if not getattr(kb, "vlm_enabled", False) and not getattr(kb, "multimodal", False):
            return

        chunks = chunks or []
        settings = RAGSettings()
        ctx_limit = settings.image_context_chars
        section_map = self._build_section_map(
            parse_result.get("sections", []), parse_result.get("content_list", [])
        )

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
                page = self._image_page(item)
                caption = item.get("caption", "")

                if getattr(kb, "vlm_enabled", False) and not caption:
                    caption = self._vlm_caption(kb, img_bytes)

                # 图片继承所在页的章节，不再写死 0 —— section_boost 靠这一列加成，
                # 写死 0 的话图片 chunk 永远拿不到章节加成，而同页的正文拿得到。
                section_id = self._find_section_for_page(section_map, page) or 0
                context = self._image_context(chunks, page, ctx_limit)

                if getattr(kb, "multimodal", False):
                    try:
                        vec = embedder.embed_images([img_bytes])[0]
                        rows.append({
                            # multimodal 路径的向量来自图片本身，上下文帮不到检索，
                            # 所以只进 content 供展示和 BM25（BM25 的稀疏向量是
                            # Milvus 从 content 现算的，所以带上下文仍有收益）。
                            "content": self._compose_image_content(caption, page, context),
                            "dense_vector": vec,
                            "kb_id": kb.id,
                            "document_id": doc.id,
                            "section_id": section_id,
                            "tenant_id": kb.tenant_id,
                            "chunk_type": "image",
                            "source_filename": doc.filename,
                            "source_url": f"{doc.file_key}#page={page}",
                            "page": page,
                        })
                    except Exception as exc:
                        logger.warning("image_embed_skip", page=page, error=str(exc))
                elif caption:
                    # caption 路径：**拿"描述 + 上下文"去 embed**，而 content 存
                    # 同一份文本。和文本侧 window_content 的口径一致 —— 那边也是
                    # 宽的去 embed、窄的存 content。
                    #
                    # 这里 content 和 embed 用同一份（都带上下文）而不是像文本侧
                    # 那样分开：图片的"窄内容"就是那句描述，几十个字，单独展示
                    # 没有意义；而带上下文的版本对人也更可读（知道这张图在讲什么）。
                    enriched = self._compose_image_content(caption, page, context)
                    vec = embedder.embed_texts([enriched])[0]
                    rows.append({
                        "content": enriched,
                        "dense_vector": vec,
                        "kb_id": kb.id,
                        "document_id": doc.id,
                        "section_id": section_id,
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
        from lea_rag.db.models import RetrievalModel
        from lea_rag.embeddings.embedding_client import EmbeddingClient

        model_name = kb.embedding_model
        if model_name:
            # noqa: E712 不能删：SQLAlchemy 过滤条件里 `Column == True` 才会生成
            # `WHERE enabled = 1`，换成裸属性是把 Column 当布尔值用，条件失效。
            # 本文件其余 E712 同理。
            m = self.db.query(RetrievalModel).filter(
                RetrievalModel.name == model_name,
                RetrievalModel.enabled == True,  # noqa: E712
            ).first()
            if m:
                return EmbeddingClient(api_base=m.api_base, model=m.model_name or m.name, api_key=m.api_key)

        m = self.db.query(RetrievalModel).filter(
            RetrievalModel.model_type == "embedding",
            RetrievalModel.is_default == True,  # noqa: E712
            RetrievalModel.enabled == True,  # noqa: E712
        ).first()
        if m:
            return EmbeddingClient(api_base=m.api_base, model=m.model_name or m.name, api_key=m.api_key)

        return EmbeddingClient()

    def _vlm_caption(self, kb: KnowledgeBase, img_bytes: bytes) -> str:
        """调用 VLM 模型生成图片描述。"""
        import base64

        import httpx

        from lea_rag.db.models import RetrievalModel

        vlm_name = getattr(kb, "vlm_model", "") or ""
        if not vlm_name:
            m = self.db.query(RetrievalModel).filter(
                RetrievalModel.model_type == "vlm",
                RetrievalModel.is_default == True,  # noqa: E712
                RetrievalModel.enabled == True,  # noqa: E712
            ).first()
        else:
            m = self.db.query(RetrievalModel).filter(
                RetrievalModel.name == vlm_name,
                RetrievalModel.enabled == True,  # noqa: E712
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

    def _process_audio(self, doc: Document, kb: KnowledgeBase, parse_result: dict) -> None:
        """处理音频：调用 ASR 将语音转为文字，追加到 text_chunks。"""
        audio_items = parse_result.get("audio_items", [])
        if not audio_items:
            return

        from lea_rag.db.models import RetrievalModel

        m = self.db.query(RetrievalModel).filter(
            RetrievalModel.model_type == "asr",
            RetrievalModel.enabled == True,  # noqa: E712
        ).first()
        if not m:
            logger.warning("asr_not_configured", document_id=doc.id)
            return

        for item in audio_items:
            transcript = self._asr_transcribe(m, item.get("file_path", ""))
            if transcript:
                from lea_rag.parsers.parser import chunk_text_blocks
                blocks = [{"text": transcript, "page_idx": 0}]
                chunks = chunk_text_blocks(blocks)
                for c in chunks:
                    c["type"] = "audio_transcript"
                parse_result.setdefault("text_chunks", []).extend(chunks)
                parse_result["md_content"] = (parse_result.get("md_content", "") + "\n\n" + transcript).strip()
                logger.info("asr_transcribed", document_id=doc.id, length=len(transcript))

    def _asr_transcribe(self, model, file_path: str) -> str:
        """调用 ASR 服务转写音频。"""
        import httpx

        if not file_path or not os.path.exists(file_path):
            return ""
        try:
            headers = {}
            if model.api_key:
                headers["Authorization"] = f"Bearer {model.api_key}"
            with open(file_path, "rb") as f:
                resp = httpx.post(
                    f"{model.api_base.rstrip('/')}/v1/audio/transcriptions",
                    headers=headers,
                    files={"file": (os.path.basename(file_path), f)},
                    data={"model": model.model_name or model.name},
                    timeout=120.0,
                )
            resp.raise_for_status()
            return resp.json().get("text", "")
        except Exception as exc:
            logger.warning("asr_failed", error=str(exc))
            return ""
