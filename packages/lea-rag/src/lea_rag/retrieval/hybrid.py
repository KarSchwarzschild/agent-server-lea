"""混合检索 — 稠密 (BGE-M3 dense) + 稀疏 (BM25) + RRF 融合。

Milvus 原生支持 hybrid_search + RRFRanker，此模块在 LlamaIndex
retriever 之上封装，提供统一的检索接口。
"""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog
from llama_index.core.schema import NodeWithScore

from lea_rag.config import RAGSettings
from lea_rag.index.vector_index import MilvusRAGIndex
from lea_rag.retrieval.reranker import Reranker

logger = structlog.get_logger(__name__)


@dataclass
class RetrievalResult:
    nodes: list[NodeWithScore] = field(default_factory=list)
    query: str = ""
    strategy: str = "hybrid"


class HybridRetriever:
    """稠密 + 稀疏混合检索，可选重排序。

    流程: query → Milvus hybrid_search (RRF) → [Reranker] → top_k
    """

    def __init__(
        self,
        index: MilvusRAGIndex,
        reranker: Reranker | None = None,
        settings: RAGSettings | None = None,
    ):
        self._index = index
        self._reranker = reranker
        s = settings or RAGSettings()
        self._retrieval_top_k = s.retrieval_top_k
        self._rerank_top_k = s.rerank_top_k

    def _build_retriever(self, source_ids: list[int] | None = None, metadata_filters: list | None = None):
        kwargs: dict = {}
        from llama_index.core.vector_stores import FilterOperator, MetadataFilter, MetadataFilters

        filters_list = []
        if source_ids:
            filters_list.append(
                MetadataFilter(key="source_id", value=source_ids, operator=FilterOperator.IN),
            )
        if metadata_filters:
            filters_list.extend(metadata_filters)

        if filters_list:
            kwargs["filters"] = MetadataFilters(filters=filters_list)
        return self._index.as_retriever(top_k=self._retrieval_top_k, **kwargs)

    async def aretrieve(
        self, query: str, top_k: int | None = None,
        source_ids: list[int] | None = None,
        metadata_filters: list | None = None,
    ) -> RetrievalResult:
        retriever = self._build_retriever(source_ids, metadata_filters)
        nodes = await retriever.aretrieve(query)
        logger.info("hybrid_retrieved", query=query[:60], candidates=len(nodes))

        final_k = top_k or self._rerank_top_k

        if self._reranker and nodes:
            nodes = await self._reranker.arerank(query, nodes, top_k=final_k)
        else:
            nodes = nodes[:final_k]

        return RetrievalResult(nodes=nodes, query=query, strategy="hybrid")

    def retrieve(
        self, query: str, top_k: int | None = None,
        source_ids: list[int] | None = None,
        metadata_filters: list | None = None,
    ) -> RetrievalResult:
        retriever = self._build_retriever(source_ids, metadata_filters)
        nodes = retriever.retrieve(query)

        final_k = top_k or self._rerank_top_k

        if self._reranker and nodes:
            import asyncio
            nodes = asyncio.get_event_loop().run_until_complete(
                self._reranker.arerank(query, nodes, top_k=final_k)
            )
        else:
            nodes = nodes[:final_k]

        return RetrievalResult(nodes=nodes, query=query, strategy="hybrid")
