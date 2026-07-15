"""知识库检索服务 — 统一检索入口。

使用 Jina v5 Omni dense + Milvus BM25 双路混合检索。
支持 remote 模式（走统一检索服务 HTTP）和 embedded 模式（直接 import）。
"""

from __future__ import annotations

from typing import Any

import structlog

logger = structlog.get_logger()


async def search_knowledge_base(
    query: str,
    limit: int = 5,
    *,
    kb_ids: list[int] | None = None,
    document_ids: list[int] | None = None,
    tenant_id: str = "",
    user_id: str = "",
) -> list[dict[str, Any]]:
    """检索知识库文档。

    Args:
        query: 搜索关键词
        limit: 返回结果数量上限
        kb_ids: 指定知识库 ID 列表。None 表示搜索所有可见知识库。
        document_ids: 指定文档 ID 列表。精确到文档级过滤。
        tenant_id: 租户 ID（用于自动过滤可见知识库）
        user_id: 用户 ID（用于自动过滤可见知识库）
    """
    if not query.strip():
        return []

    from lea_graph.services.retrieval_client import is_remote_mode, kb_search

    if is_remote_mode():
        try:
            return await kb_search(
                query, limit,
                kb_ids=kb_ids, document_ids=document_ids,
                tenant_id=tenant_id, user_id=user_id,
            )
        except Exception as exc:
            logger.error("remote_kb_search_error", error=str(exc))
            return []

    effective_kb_ids = _resolve_kb_ids(kb_ids, document_ids, tenant_id, user_id)

    try:
        results = await _search_v2(query, limit, effective_kb_ids, document_ids, tenant_id)
        return results
    except Exception as exc:
        logger.error("kb_search_error", error=str(exc))
        return []


async def _search_v2(
    query: str,
    limit: int,
    kb_ids: list[int] | None,
    document_ids: list[int] | None,
    tenant_id: str,
) -> list[dict[str, Any]]:
    """使用 HybridRetrieverV2 检索。"""
    try:
        from lea_rag.retrieval.hybrid_v2 import HybridRetrieverV2
    except ImportError:
        logger.warning("lea_rag_not_available")
        return []

    retriever = HybridRetrieverV2()

    hits = await retriever.search(
        query,
        top_k=limit,
        kb_ids=kb_ids,
        document_ids=document_ids,
        tenant_id=tenant_id,
    )

    output = []
    for hit in hits[:limit]:
        output.append({
            "content": hit.content,
            "title": hit.source_filename,
            "score": hit.score,
            "kb_id": str(hit.kb_id),
            "document_id": str(hit.document_id),
            "section_id": hit.section_id or 0,
            "page": hit.page,
            "chunk_type": hit.chunk_type,
            "source_url": hit.source_url,
        })

    if output:
        _enrich_section_titles(output)

    return output


def _enrich_section_titles(results: list[dict[str, Any]]) -> None:
    """补充 section_title 字段。"""
    section_ids = [r["section_id"] for r in results if r.get("section_id")]
    if not section_ids:
        return
    try:
        from lea_server.database import SessionLocal
        from lea_server.models.knowledge import DocumentSection

        db = SessionLocal()
        try:
            sections = (
                db.query(DocumentSection.id, DocumentSection.title)
                .filter(DocumentSection.id.in_(section_ids))
                .all()
            )
            title_map = {s.id: s.title for s in sections}
        finally:
            db.close()

        for r in results:
            r["section_title"] = title_map.get(r.get("section_id", 0), "")
    except Exception:
        for r in results:
            r.setdefault("section_title", "")


def _resolve_kb_ids(
    kb_ids: list[int] | None,
    document_ids: list[int] | None,
    tenant_id: str,
    user_id: str,
) -> list[int] | None:
    """解析用户可见的知识库 ID 列表。"""
    if document_ids or kb_ids:
        return kb_ids

    if not tenant_id and not user_id:
        return None

    try:
        from lea_server.database import SessionLocal
        from lea_server.services.kb_service import KBService

        db = SessionLocal()
        try:
            svc = KBService(db)
            visible_ids = svc.get_visible_kb_ids(user_id, tenant_id)
            return visible_ids if visible_ids else None
        finally:
            db.close()
    except Exception as exc:
        logger.warning("kb_filter_fallback", error=str(exc))
        return None
