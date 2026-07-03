"""知识库检索服务 — 统一检索入口。

从本地 Milvus 向量数据库检索，自动过滤用户可见的知识库。
替代旧版的智能基座外部 API 调用。
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

    filters = _build_filters(kb_ids, document_ids, tenant_id, user_id)

    try:
        results = await _search_milvus(query, limit, filters)
        return results
    except Exception as exc:
        logger.error("kb_search_error", error=str(exc))
        return []


async def _search_milvus(
    query: str,
    limit: int,
    filters: dict[str, Any],
) -> list[dict[str, Any]]:
    """从 Milvus 向量数据库检索。"""
    try:
        from lea_rag.index.vector_index import MilvusRAGIndex
        from lea_rag.retrieval.hybrid import HybridRetriever
    except ImportError:
        logger.warning("lea_rag_not_available")
        return []

    index = MilvusRAGIndex(collection_name="lea_knowledge")
    retriever = HybridRetriever(index)

    try:
        metadata_filters = None
        kb_ids = filters.get("kb_ids")
        document_ids = filters.get("document_ids")

        if kb_ids or document_ids:
            from llama_index.core.vector_stores import MetadataFilter, FilterOperator
            metadata_filters = []
            if document_ids:
                str_ids = [str(did) for did in document_ids]
                metadata_filters.append(
                    MetadataFilter(key="document_id", value=str_ids, operator=FilterOperator.IN),
                )
            elif kb_ids:
                str_ids = [str(kid) for kid in kb_ids]
                metadata_filters.append(
                    MetadataFilter(key="kb_id", value=str_ids, operator=FilterOperator.IN),
                )

        result = await retriever.aretrieve(
            query, top_k=limit, metadata_filters=metadata_filters,
        )
        nodes = result.nodes

        output = []
        for node in nodes[:limit]:
            text = node.text if hasattr(node, "text") else str(node)
            metadata = node.metadata if hasattr(node, "metadata") else {}
            score = node.score if hasattr(node, "score") else 0.0
            output.append({
                "content": text,
                "title": metadata.get("source_filename", ""),
                "score": score,
                "kb_id": metadata.get("kb_id", ""),
                "document_id": metadata.get("document_id", ""),
                "page": metadata.get("page", 0),
            })
        return output

    except Exception as exc:
        logger.error("milvus_search_error", error=str(exc))
        return []


def _build_filters(
    kb_ids: list[int] | None,
    document_ids: list[int] | None,
    tenant_id: str,
    user_id: str,
) -> dict[str, Any]:
    """构建检索过滤条件。"""
    if document_ids:
        return {"document_ids": document_ids}

    if kb_ids:
        return {"kb_ids": kb_ids}

    if not tenant_id and not user_id:
        return {}

    try:
        from lea_server.database import SessionLocal
        from lea_server.services.kb_service import KBService

        db = SessionLocal()
        try:
            svc = KBService(db)
            visible_ids = svc.get_visible_kb_ids(user_id, tenant_id)
            return {"kb_ids": visible_ids} if visible_ids else {}
        finally:
            db.close()
    except Exception as exc:
        logger.warning("kb_filter_fallback", error=str(exc))
        return {}
