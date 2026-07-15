"""检索服务 — 知识库检索路由 + 文档上传处理。"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, HTTPException, UploadFile, File, Form
from pydantic import BaseModel, Field, model_validator

logger = structlog.get_logger()
router = APIRouter(prefix="/kb", tags=["knowledge-base"])


class KBSearchRequest(BaseModel):
    query: str = ""
    image: str | None = None
    top_k: int = 5
    kb_ids: list[int] | None = None
    document_ids: list[int] | None = None
    tenant_id: str = ""
    user_id: str = ""
    strategy: str = "hybrid"
    metadata_filter: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _check_input(self):
        if not self.query and not self.image:
            raise ValueError("query 和 image 至少提供一个")
        return self


class SearchHitOut(BaseModel):
    content: str
    score: float
    kb_id: str
    document_id: str
    section_id: int = 0
    page: int = 0
    chunk_type: str = "text"
    source_filename: str = ""
    source_url: str = ""


class SectionNode(BaseModel):
    id: int
    title: str
    level: int
    page_start: int = 0
    children: list["SectionNode"] = []


class SectionsRequest(BaseModel):
    document_id: int


@router.get("/health")
async def kb_health():
    return {"status": "ok", "module": "kb"}


@router.post("/search", response_model=list[SearchHitOut])
async def search_kb(body: KBSearchRequest):
    """知识库检索。

    strategy 可选值:
      - hybrid: 语义 + 关键词双路（默认）
      - semantic: 仅语义向量
      - keyword: 仅 BM25 关键词

    metadata_filter: 按 KB/Document 的 metadata JSON 字段过滤。
    """
    from lea_rag.retrieval.hybrid_v2 import HybridRetrieverV2

    effective_kb_ids = body.kb_ids
    effective_doc_ids = body.document_ids

    if not effective_kb_ids and not effective_doc_ids:
        effective_kb_ids = _get_visible_kb_ids(body.user_id, body.tenant_id)

    if body.metadata_filter:
        effective_kb_ids, effective_doc_ids = _apply_metadata_filter(
            effective_kb_ids, effective_doc_ids, body.metadata_filter
        )

    kb_groups = _group_kbs_by_model(effective_kb_ids)

    # 图片查询：提前 embed 得到 query_vector
    query_vector: list[float] | None = None
    if body.image:
        import base64
        img_bytes = base64.b64decode(body.image)
        first_model = next(iter(kb_groups), None)
        embedder = _get_embedder(first_model)
        vecs = await embedder.aembed_images([img_bytes])
        query_vector = vecs[0] if vecs else None

    all_hits: list = []
    for model_key, group_kb_ids in kb_groups.items():
        embedder = _get_embedder(model_key)
        retriever = HybridRetrieverV2(embedder=embedder)
        try:
            hits = await retriever.search(
                body.query,
                top_k=body.top_k,
                query_vector=query_vector,
                kb_ids=group_kb_ids,
                document_ids=effective_doc_ids,
                tenant_id=body.tenant_id,
                mode=body.strategy,
            )
            all_hits.extend(hits)
        except Exception as exc:
            logger.error("kb_search_error", model=model_key, error=str(exc))

    all_hits.sort(key=lambda h: h.score, reverse=True)
    all_hits = all_hits[:body.top_k]

    return [
        SearchHitOut(
            content=h.content,
            score=h.score,
            kb_id=str(h.kb_id),
            document_id=str(h.document_id),
            section_id=h.section_id or 0,
            page=h.page,
            chunk_type=h.chunk_type,
            source_filename=h.source_filename,
            source_url=h.source_url,
        )
        for h in all_hits
    ]


def _get_visible_kb_ids(user_id: str, tenant_id: str) -> list[int] | None:
    """三级共享过滤：private(仅owner) + shared(同tenant) + public(全平台)。"""
    if not user_id and not tenant_id:
        return None
    try:
        from lea_server.database import SessionLocal
        from lea_server.services.kb_service import KBService

        db = SessionLocal()
        try:
            svc = KBService(db)
            ids = svc.get_visible_kb_ids(user_id, tenant_id)
            return ids if ids else None
        finally:
            db.close()
    except Exception as exc:
        logger.warning("visibility_filter_fallback", error=str(exc))
        return None


def _apply_metadata_filter(
    kb_ids: list[int] | None,
    doc_ids: list[int] | None,
    metadata_filter: dict[str, Any],
) -> tuple[list[int] | None, list[int] | None]:
    """根据 metadata JSON 条件在 DB 层过滤 KB 和 Document。"""
    try:
        from sqlalchemy import and_
        from lea_server.database import SessionLocal
        from lea_server.models.knowledge import KnowledgeBase, Document

        db = SessionLocal()
        try:
            q = db.query(Document.id, Document.kb_id)
            if kb_ids:
                q = q.filter(Document.kb_id.in_(kb_ids))
            if doc_ids:
                q = q.filter(Document.id.in_(doc_ids))
            for key, val in metadata_filter.items():
                q = q.filter(
                    Document.extra[key].as_string() == str(val)
                )
            rows = q.all()
            if rows:
                return (
                    list({r.kb_id for r in rows}),
                    [r.id for r in rows],
                )
            return kb_ids, doc_ids
        finally:
            db.close()
    except Exception as exc:
        logger.warning("metadata_filter_failed", error=str(exc))
        return kb_ids, doc_ids


def _group_kbs_by_model(kb_ids: list[int] | None) -> dict[str, list[int]]:
    """按 embedding_model 分组 KB，用于跨模型混搜。"""
    if not kb_ids:
        return {"default": kb_ids or []}
    try:
        from lea_server.database import SessionLocal
        from lea_server.models.knowledge import KnowledgeBase

        db = SessionLocal()
        try:
            kbs = (
                db.query(KnowledgeBase.id, KnowledgeBase.embedding_model)
                .filter(KnowledgeBase.id.in_(kb_ids))
                .all()
            )
            groups: dict[str, list[int]] = {}
            for kb in kbs:
                model = kb.embedding_model or "default"
                groups.setdefault(model, []).append(kb.id)
            return groups if groups else {"default": kb_ids}
        finally:
            db.close()
    except Exception as exc:
        logger.warning("group_kbs_failed", error=str(exc))
        return {"default": kb_ids}


def _get_embedder(model_key: str):
    """根据模型名从 DB 查询配置，创建对应的 Embedding 客户端。"""
    from lea_rag.embeddings.embedding_client import EmbeddingClient

    if model_key == "default":
        model_key = _get_default_embedding_model()

    model_info = _get_model_info(model_key)
    if model_info:
        return EmbeddingClient(
            api_base=model_info["api_base"],
            model=model_info["model_name"] or model_key,
            api_key=model_info.get("api_key"),
        )
    return EmbeddingClient(model=model_key)


def _get_model_info(model_name: str) -> dict | None:
    """从 DB 读取模型配置。"""
    try:
        from lea_server.database import SessionLocal
        from lea_server.models.knowledge import RetrievalModel

        db = SessionLocal()
        try:
            m = db.query(RetrievalModel).filter(
                RetrievalModel.name == model_name,
                RetrievalModel.enabled == True,
            ).first()
            if m:
                return {"api_base": m.api_base, "model_name": m.model_name, "dimension": m.dimension, "api_key": m.api_key}
        finally:
            db.close()
    except Exception:
        pass
    return None


def _get_default_embedding_model() -> str:
    """获取默认 embedding 模型名。"""
    try:
        from lea_server.database import SessionLocal
        from lea_server.models.knowledge import RetrievalModel

        db = SessionLocal()
        try:
            m = db.query(RetrievalModel).filter(
                RetrievalModel.model_type == "embedding",
                RetrievalModel.is_default == True,
                RetrievalModel.enabled == True,
            ).first()
            if m:
                return m.name
        finally:
            db.close()
    except Exception:
        pass
    from lea_rag.config import EmbeddingSettings
    return EmbeddingSettings().model


@router.post("/sections", response_model=list[SectionNode])
async def get_sections(body: SectionsRequest):
    """获取文档的章节树结构。"""
    try:
        from lea_server.database import SessionLocal
        from lea_server.models.knowledge import DocumentSection

        db = SessionLocal()
        try:
            sections = (
                db.query(DocumentSection)
                .filter(DocumentSection.document_id == body.document_id)
                .order_by(DocumentSection.position)
                .all()
            )
            return _build_tree(sections)
        finally:
            db.close()
    except Exception as exc:
        logger.error("sections_error", error=str(exc))
        raise HTTPException(500, f"获取章节失败: {exc}")


def _build_tree(sections) -> list[dict[str, Any]]:
    """将平铺的 section 列表构建为嵌套树。"""
    nodes: dict[int, dict] = {}
    roots: list[dict] = []

    for sec in sections:
        node = {
            "id": sec.id,
            "title": sec.title,
            "level": sec.level,
            "page_start": sec.page_start,
            "children": [],
        }
        nodes[sec.id] = node

        if sec.parent_id and sec.parent_id in nodes:
            nodes[sec.parent_id]["children"].append(node)
        else:
            roots.append(node)

    return roots


# ── 知识库 CRUD ──────────────────────────────────────────


class KBCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    owner: str = ""
    tenant_id: str = "default"
    visibility: str = "private"
    embedding_model: str = ""
    reranker_model: str = ""
    vlm_model: str = ""
    embedding_dim: int = 1024
    vlm_enabled: bool = True
    reranker_enabled: bool = True
    multimodal: bool = False
    retrieval_mode: str = "hybrid"
    retrieval_top_k: int = 5
    score_threshold: float = 0.5
    metadata: dict[str, Any] = {}


class KBOut(BaseModel):
    id: int
    name: str
    description: str = ""
    owner: str = ""
    visibility: str = "private"
    embedding_model: str = ""
    multimodal: bool = False
    metadata: dict[str, Any] = {}
    document_count: int = 0
    enabled: bool = True


@router.post("/create", response_model=KBOut)
async def create_kb(body: KBCreateRequest):
    """创建知识库。"""
    from lea_server.database import SessionLocal
    from lea_server.services.kb_service import KBService

    db = SessionLocal()
    try:
        svc = KBService(db)
        kb = svc.create_kb(
            body.model_dump(exclude={"owner", "tenant_id"}),
            owner=body.owner,
            tenant_id=body.tenant_id,
        )
        db.commit()
        return KBOut(
            id=kb.id, name=kb.name, description=kb.description or "",
            owner=kb.owner, visibility=kb.visibility or "private",
            embedding_model=kb.embedding_model or "",
            multimodal=kb.multimodal or False,
            metadata=kb.extra or {},
            document_count=kb.document_count or 0, enabled=kb.enabled,
        )
    except Exception as exc:
        db.rollback()
        raise HTTPException(500, f"创建失败: {exc}")
    finally:
        db.close()


@router.get("/list", response_model=list[KBOut])
async def list_kbs(
    tenant_id: str = "default",
    owner: str = "",
):
    """列出知识库。"""
    from lea_server.database import SessionLocal
    from lea_server.services.kb_service import KBService

    db = SessionLocal()
    try:
        svc = KBService(db)
        kbs, _ = svc.list_kbs(
            user_id=owner, tenant_id=tenant_id, page=1, size=200
        )
        return [
            KBOut(
                id=kb.id, name=kb.name, description=kb.description or "",
                owner=kb.owner, visibility=kb.visibility or "private",
                embedding_model=kb.embedding_model or "",
                multimodal=kb.multimodal or False,
                metadata=kb.extra or {},
                document_count=kb.document_count or 0, enabled=kb.enabled,
            )
            for kb in kbs
        ]
    finally:
        db.close()


# ── 知识库配置 ──────────────────────────────────────────


class KBConfigOut(BaseModel):
    id: int
    name: str
    retrieval_mode: str = "hybrid"
    retrieval_top_k: int = 5
    score_threshold: float = 0.5
    visibility: str = "private"
    enabled: bool = True


class KBConfigUpdateRequest(BaseModel):
    retrieval_mode: str | None = None
    retrieval_top_k: int | None = None
    score_threshold: float | None = None
    visibility: str | None = None
    enabled: bool | None = None
    name: str | None = None
    description: str | None = None


@router.get("/{kb_id}/config", response_model=KBConfigOut)
async def get_kb_config(kb_id: int):
    """获取知识库检索配置。"""
    from lea_server.database import SessionLocal
    from lea_server.services.kb_service import KBService

    db = SessionLocal()
    try:
        kb = KBService(db).get_kb(kb_id)
        if kb is None:
            raise HTTPException(404, "知识库不存在")
        return KBConfigOut(
            id=kb.id, name=kb.name,
            retrieval_mode=kb.retrieval_mode or "hybrid",
            retrieval_top_k=kb.retrieval_top_k or 5,
            score_threshold=kb.score_threshold or 0.5,
            visibility=kb.visibility or "private",
            enabled=kb.enabled,
        )
    finally:
        db.close()


@router.put("/{kb_id}/config", response_model=KBConfigOut)
async def update_kb_config(kb_id: int, body: KBConfigUpdateRequest):
    """更新知识库检索配置（策略、参数、可见性）。"""
    from lea_server.database import SessionLocal
    from lea_server.services.kb_service import KBService

    db = SessionLocal()
    try:
        svc = KBService(db)
        kb = svc.update_kb(kb_id, body.model_dump(exclude_none=True))
        if kb is None:
            raise HTTPException(404, "知识库不存在")
        db.commit()
        return KBConfigOut(
            id=kb.id, name=kb.name,
            retrieval_mode=kb.retrieval_mode or "hybrid",
            retrieval_top_k=kb.retrieval_top_k or 5,
            score_threshold=kb.score_threshold or 0.5,
            visibility=kb.visibility or "private",
            enabled=kb.enabled,
        )
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(500, f"更新失败: {exc}")
    finally:
        db.close()


# ── 文档上传 + 处理 ──────────────────────────────────────


class DocumentOut(BaseModel):
    id: int
    kb_id: int
    filename: str
    file_type: str = "pdf"
    status: str = "processing"
    chunk_count: int = 0
    error_message: str = ""
    metadata: dict[str, Any] = {}


@router.post("/{kb_id}/upload", response_model=DocumentOut)
async def upload_document(
    kb_id: int,
    file: UploadFile = File(...),
    tenant_id: str = Form("default"),
    metadata: str = Form("{}"),
):
    """上传文档到知识库并启动后台处理。"""
    import json as _json
    from lea_server.database import SessionLocal
    from lea_server.services.kb_service import KBService
    from lea_server.services.object_storage import ObjectStorage

    doc_metadata = {}
    try:
        doc_metadata = _json.loads(metadata) if metadata else {}
    except Exception:
        pass

    db = SessionLocal()
    try:
        svc = KBService(db)
        kb = svc.get_kb(kb_id)
        if kb is None:
            raise HTTPException(404, "知识库不存在")

        file_ext = (file.filename or "file").rsplit(".", 1)[-1].lower()
        if file_ext not in ("pdf", "txt", "md", "docx"):
            raise HTTPException(400, f"不支持的文件类型: {file_ext}")

        content = await file.read()
        file_key = f"kb/{kb_id}/{file.filename}"

        storage = ObjectStorage()
        storage.put_object(
            file_key, content,
            content_type=file.content_type or "application/octet-stream",
        )

        doc = svc.create_document(kb_id, {
            "filename": file.filename or "unknown",
            "file_key": file_key,
            "file_type": file_ext,
            "file_size": len(content),
            "metadata": doc_metadata,
        }, tenant_id=tenant_id)
        db.commit()

        from lea_rag.tasks import celery_app
        celery_app.send_task(
            "lea_rag.tasks.process_document",
            args=[doc.id],
            queue="retrieval",
        )

        return DocumentOut(
            id=doc.id, kb_id=doc.kb_id, filename=doc.filename,
            file_type=doc.file_type or "pdf", status="processing",
            metadata=doc.extra or {},
        )
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(500, f"上传失败: {exc}")
    finally:
        db.close()


@router.get("/{kb_id}/documents", response_model=list[DocumentOut])
async def list_documents(kb_id: int):
    """列出知识库下的文档。"""
    from lea_server.database import SessionLocal
    from lea_server.services.kb_service import KBService

    db = SessionLocal()
    try:
        svc = KBService(db)
        docs, _ = svc.list_documents(kb_id, page=1, size=200)
        return [
            DocumentOut(
                id=d.id, kb_id=d.kb_id, filename=d.filename,
                file_type=d.file_type or "pdf", status=d.status or "processing",
                chunk_count=d.chunk_count or 0,
                error_message=d.error_message or "",
            )
            for d in docs
        ]
    finally:
        db.close()


@router.get("/{kb_id}/documents/{doc_id}", response_model=DocumentOut)
async def get_document(kb_id: int, doc_id: int):
    """获取单个文档状态。"""
    from lea_server.database import SessionLocal
    from lea_server.services.kb_service import KBService

    db = SessionLocal()
    try:
        doc = KBService(db).get_document(doc_id)
        if doc is None or doc.kb_id != kb_id:
            raise HTTPException(404, "文档不存在")
        return DocumentOut(
            id=doc.id, kb_id=doc.kb_id, filename=doc.filename,
            file_type=doc.file_type or "pdf", status=doc.status or "processing",
            chunk_count=doc.chunk_count or 0,
            error_message=doc.error_message or "",
        )
    finally:
        db.close()


@router.delete("/{kb_id}/documents/{doc_id}")
async def delete_document(kb_id: int, doc_id: int):
    """删除文档及其向量。"""
    from lea_server.database import SessionLocal
    from lea_server.services.kb_service import KBService

    db = SessionLocal()
    try:
        svc = KBService(db)
        doc = svc.get_document(doc_id)
        if doc is None or doc.kb_id != kb_id:
            raise HTTPException(404, "文档不存在")
        svc.delete_document(doc_id)
        db.commit()

        try:
            from lea_rag.index.milvus_index import MilvusKBIndex
            index = MilvusKBIndex()
            index.delete_by_document(doc_id)
        except Exception as exc:
            logger.warning("vector_delete_failed", doc_id=doc_id, error=str(exc))

        return {"message": "已删除"}
    except HTTPException:
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(500, f"删除失败: {exc}")
    finally:
        db.close()

