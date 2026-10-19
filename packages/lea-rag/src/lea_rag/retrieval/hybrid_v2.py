"""混合检索 v2 — Jina dense + Milvus BM25 双路召回 + RRF 融合。

替代旧版 LlamaIndex-based HybridRetriever。
流程: query → [QueryOptimizer] → Milvus hybrid_search(dense+BM25) → [Section Boost] → [Reranker] → top_k
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

import structlog
from pymilvus import AnnSearchRequest, RRFRanker, WeightedRanker

from lea_rag.config import RAGSettings, RerankerSettings
from lea_rag.embeddings.embedding_client import EmbeddingClient
from lea_rag.index.milvus_index import MilvusKBIndex

logger = structlog.get_logger()


@dataclass
class SearchHit:
    content: str = ""
    score: float = 0.0
    kb_id: int = 0
    document_id: int = 0
    section_id: int = 0
    tenant_id: str = ""
    chunk_type: str = "text"
    source_filename: str = ""
    source_url: str = ""
    page: int = 0


class HybridRetrieverV2:
    """Jina dense + Milvus BM25 双路召回。"""

    def __init__(
        self,
        index: MilvusKBIndex | None = None,
        embedder: EmbeddingClient | None = None,
        settings: RAGSettings | None = None,
    ):
        self._index = index or MilvusKBIndex()
        self._embedder = embedder or EmbeddingClient()
        self._settings = settings or RAGSettings()
        self._reranker = self._init_reranker()

    def _init_reranker(self):
        cfg = RerankerSettings()
        if not cfg.enabled:
            return None
        try:
            from lea_rag.retrieval.reranker import Reranker
            return Reranker(cfg)
        except Exception:
            return None

    async def search(
        self,
        query: str = "",
        top_k: int = 5,
        *,
        query_vector: list[float] | None = None,
        kb_ids: list[int] | None = None,
        document_ids: list[int] | None = None,
        tenant_id: str = "",
        optimize_query: bool | None = None,
        section_boost: bool | None = None,
        mode: str = "hybrid",
    ) -> list[SearchHit]:
        """执行检索。

        Args:
            query: 文本查询（与 query_vector 二选一）
            query_vector: 预计算的查询向量（图片 embed 等场景直传）
            mode: 检索模式
                - "hybrid": dense + BM25 双路（默认）
                - "semantic": 仅 dense 向量检索
                - "keyword": 仅 BM25 关键词检索
                - "rewrite": 先用 LLM 改写 query 再 hybrid
        """
        # 直传 vector 时强制 semantic（BM25 对图片无意义）
        if query_vector:
            mode = "semantic"

        should_optimize = optimize_query if optimize_query is not None else self._settings.query_optimize
        if mode == "rewrite":
            should_optimize = True
        should_boost = section_boost if section_boost is not None else self._settings.section_boost

        effective_query = query
        if should_optimize and query:
            effective_query = await self._optimize(query)

        filter_expr = self._build_filter(kb_ids, document_ids, tenant_id)

        if mode == "keyword":
            candidates = self._keyword_search(effective_query, filter_expr, top_k)
        else:
            vec = query_vector or await self._embedder.aembed_text(effective_query)
            if mode == "semantic":
                candidates = self._dense_search(vec, filter_expr, top_k)
            else:
                candidates = self._hybrid_search(effective_query, vec, filter_expr, top_k)

        if should_boost and candidates:
            candidates = await self._apply_section_boost(query, candidates)

        if self._reranker and candidates:
            candidates = await self._rerank(effective_query, candidates, top_k)
        else:
            candidates = candidates[:top_k]

        return candidates

    async def _optimize(self, query: str) -> str:
        try:
            from lea_rag.retrieval.query_optimizer import QueryOptimizer, Strategy
            optimizer = QueryOptimizer()
            results = await optimizer.optimize(query, strategies=[Strategy.REWRITE])
            return results[0] if results else query
        except Exception as exc:
            logger.warning("query_optimize_failed", error=str(exc))
            return query

    def _build_filter(
        self,
        kb_ids: list[int] | None,
        document_ids: list[int] | None,
        tenant_id: str,
    ) -> str:
        parts: list[str] = []
        if document_ids:
            ids_str = ", ".join(str(d) for d in document_ids)
            parts.append(f"document_id in [{ids_str}]")
        elif kb_ids:
            ids_str = ", ".join(str(k) for k in kb_ids)
            parts.append(f"kb_id in [{ids_str}]")
        if tenant_id:
            parts.append(f'tenant_id == "{tenant_id}"')
        return " and ".join(parts) if parts else ""

    def _hybrid_search(
        self,
        query_text: str,
        query_vec: list[float],
        filter_expr: str,
        top_k: int,
    ) -> list[SearchHit]:
        """用 Milvus 原生 hybrid_search 做 dense + BM25 融合。

        RRF 是默认排序器：COSINE 和 BM25 的原始分数没有可比性，直接相加会
        让权重随索引、语料和查询类型漂移。WeightedRanker 仍保留为配置项，
        便于在同一套数据上做 A/B 对比；两者都在一次 Milvus RPC 内完成，避免
        应用层串行发两次搜索。
        """
        collection = self._index.collection_name
        client = self._index.client
        candidate_limit = min(top_k * 4, 50)

        from lea_rag.metrics import record_vector_db_query

        dense_req = AnnSearchRequest(
            data=[query_vec],
            anns_field="dense_vector",
            param={"metric_type": "COSINE", "params": {"nprobe": 16}},
            limit=candidate_limit,
            filter=filter_expr or None,
        )
        bm25_req = AnnSearchRequest(
            data=[query_text],
            anns_field="sparse_vector",
            param={"metric_type": "BM25"},
            limit=candidate_limit,
            filter=filter_expr or None,
        )
        ranker_name = self._hybrid_ranker_name()
        if ranker_name == "weighted":
            ranker = WeightedRanker(
                self._settings.hybrid_dense_weight,
                self._settings.hybrid_bm25_weight,
                norm_score=True,
            )
        else:
            ranker = RRFRanker(k=self._settings.hybrid_rrf_k)

        output_fields = [
            "content", "kb_id", "document_id", "section_id",
            "tenant_id", "chunk_type", "source_filename", "source_url", "page",
        ]
        _vq_started = time.perf_counter()
        try:
            results = client.hybrid_search(
                collection_name=collection,
                reqs=[dense_req, bm25_req],
                ranker=ranker,
                limit=candidate_limit,
                output_fields=output_fields,
            )
        except Exception as exc:
            record_vector_db_query(
                f"search_hybrid_{ranker_name}_error", time.perf_counter() - _vq_started
            )
            logger.error("milvus_hybrid_search_failed", error=str(exc))
            return []
        record_vector_db_query(
            f"search_hybrid_{ranker_name}", time.perf_counter() - _vq_started
        )

        hits = self._parse_hits(results)

        logger.info(
            "hybrid_search_done",
            query=query_text[:60],
            ranker=ranker_name,
            hits=len(hits),
        )
        return hits

    def _hybrid_ranker_name(self) -> str:
        """返回受限的排序器名称；未知配置安全回退到 RRF。"""
        name = str(getattr(self._settings, "hybrid_ranker", "rrf")).strip().lower()
        if name not in {"rrf", "weighted"}:
            logger.warning("unknown_hybrid_ranker", ranker=name, fallback="rrf")
            return "rrf"
        return name

    @staticmethod
    def _parse_hits(results: Any) -> list[SearchHit]:
        hits: list[SearchHit] = []
        for result_list in results or []:
            for hit in result_list:
                entity = hit.get("entity", {}) if isinstance(hit, dict) else hit.entity
                distance = hit.get("distance", 0.0) if isinstance(hit, dict) else hit.distance
                hits.append(SearchHit(
                    content=entity.get("content", ""),
                    score=float(distance),
                    kb_id=entity.get("kb_id", 0),
                    document_id=entity.get("document_id", 0),
                    section_id=entity.get("section_id", 0),
                    tenant_id=entity.get("tenant_id", ""),
                    chunk_type=entity.get("chunk_type", "text"),
                    source_filename=entity.get("source_filename", ""),
                    source_url=entity.get("source_url", ""),
                    page=entity.get("page", 0),
                ))
        return hits

    def _dense_search(
        self, query_vec: list[float], filter_expr: str, top_k: int
    ) -> list[SearchHit]:
        """仅语义向量检索。"""
        collection = self._index.collection_name
        client = self._index.client
        candidate_limit = min(top_k * 4, 50)
        kwargs: dict[str, Any] = {
            "collection_name": collection,
            "data": [query_vec],
            "anns_field": "dense_vector",
            "search_params": {"metric_type": "COSINE", "params": {"nprobe": 16}},
            "limit": candidate_limit,
            "output_fields": [
                "content", "kb_id", "document_id", "section_id",
                "tenant_id", "chunk_type", "source_filename", "source_url", "page",
            ],
        }
        if filter_expr:
            kwargs["filter"] = filter_expr

        from lea_rag.metrics import record_vector_db_query

        _vq_started = time.perf_counter()
        try:
            results = client.search(**kwargs)
        except Exception as exc:
            record_vector_db_query("search_dense_error", time.perf_counter() - _vq_started)
            logger.error("milvus_dense_search_failed", error=str(exc))
            return []
        record_vector_db_query("search_dense", time.perf_counter() - _vq_started)
        return self._parse_hits(results)

    def _keyword_search(
        self, query_text: str, filter_expr: str, top_k: int
    ) -> list[SearchHit]:
        """仅 BM25 关键词检索（通过 sparse_vector 字段）。"""
        collection = self._index.collection_name
        client = self._index.client
        candidate_limit = min(top_k * 4, 50)

        kwargs: dict[str, Any] = {
            "collection_name": collection,
            "data": [query_text],
            "anns_field": "sparse_vector",
            "search_params": {"metric_type": "BM25"},
            "limit": candidate_limit,
            "output_fields": [
                "content", "kb_id", "document_id", "section_id",
                "tenant_id", "chunk_type", "source_filename", "source_url", "page",
            ],
        }
        if filter_expr:
            kwargs["filter"] = filter_expr

        from lea_rag.metrics import record_vector_db_query

        _vq_started = time.perf_counter()
        try:
            results = client.search(**kwargs)
        except Exception as exc:
            record_vector_db_query("search_bm25_error", time.perf_counter() - _vq_started)
            logger.error("bm25_search_failed", error=str(exc))
            return []
        record_vector_db_query("search_bm25", time.perf_counter() - _vq_started)

        hits = self._parse_hits(results)

        logger.info("keyword_search_done", query=query_text[:60], hits=len(hits))
        return hits

    async def _apply_section_boost(
        self, query: str, hits: list[SearchHit]
    ) -> list[SearchHit]:
        section_ids = {h.section_id for h in hits if h.section_id}
        if not section_ids:
            return hits

        try:
            from lea_rag.db import SessionLocal
            from lea_rag.db.models import DocumentSection

            db = SessionLocal()
            try:
                sections = (
                    db.query(DocumentSection)
                    .filter(DocumentSection.id.in_(list(section_ids)))
                    .all()
                )
                matched_ids: set[int] = set()
                query_lower = query.lower()
                for sec in sections:
                    if sec.title and sec.title.lower() in query_lower:
                        matched_ids.add(sec.id)
                    elif sec.title and any(
                        kw in query_lower for kw in sec.title.lower().split() if len(kw) > 1
                    ):
                        matched_ids.add(sec.id)

                if matched_ids:
                    # RRF 分数通常只有 0.03 左右，而 cosine/weighted 分数接近 1。
                    # 按当前候选尺度加成，避免固定 0.1 让标题命中压过融合排序。
                    boost_delta = max((h.score for h in hits), default=0.0) * 0.1
                    for h in hits:
                        if h.section_id in matched_ids:
                            h.score += boost_delta
                    hits.sort(key=lambda x: x.score, reverse=True)
            finally:
                db.close()
        except Exception as exc:
            logger.warning("section_boost_failed", error=str(exc))

        return hits

    async def _rerank(
        self, query: str, hits: list[SearchHit], top_k: int
    ) -> list[SearchHit]:
        try:
            from llama_index.core.schema import NodeWithScore, TextNode

            nodes = [
                NodeWithScore(node=TextNode(text=h.content), score=h.score)
                for h in hits
            ]
            reranked = await self._reranker.arerank(query, nodes, top_k=top_k)
            result: list[SearchHit] = []
            for node in reranked:
                text = node.get_content()
                for h in hits:
                    if h.content == text:
                        h.score = node.score or h.score
                        result.append(h)
                        break
            return result[:top_k]
        except Exception as exc:
            logger.warning("rerank_failed", error=str(exc))
            return hits[:top_k]
