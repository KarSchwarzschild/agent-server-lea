"""BGE-Reranker-v2-m3 重排序 — 带熔断保护。

参考 rnp 项目模式：两层保护（限流 + 熔断），
失败时优雅降级为原始排序。
"""

from __future__ import annotations

import httpx
import structlog
from pybreaker import CircuitBreaker, CircuitBreakerError
from llama_index.core.schema import NodeWithScore

from lea_rag.config import RerankerSettings

logger = structlog.get_logger(__name__)


class Reranker:
    """BGE-Reranker 重排序器，内置熔断保护。"""

    def __init__(self, settings: RerankerSettings | None = None):
        self._settings = settings or RerankerSettings()
        self._breaker = CircuitBreaker(
            fail_max=self._settings.breaker_fail_max,
            reset_timeout=self._settings.breaker_reset_timeout,
            name="reranker",
        )

    async def arerank(
        self,
        query: str,
        nodes: list[NodeWithScore],
        top_k: int | None = None,
    ) -> list[NodeWithScore]:
        """重排序节点列表。熔断或异常时退化为截断原序。"""
        k = top_k or self._settings.top_k
        if not self._settings.enabled or not nodes:
            return nodes[:k]

        try:
            scored = await self._breaker.call_async(self._call_reranker, query, nodes)
            scored.sort(key=lambda x: x[1], reverse=True)
            reranked = [ns for ns, _ in scored[:k]]
            logger.info("reranked", query=query[:60], top_k=k)
            return reranked
        except CircuitBreakerError:
            logger.warning("reranker_circuit_open", fallback="truncate")
            return nodes[:k]
        except Exception:
            logger.exception("reranker_failed", fallback="truncate")
            return nodes[:k]

    async def _call_reranker(
        self, query: str, nodes: list[NodeWithScore]
    ) -> list[tuple[NodeWithScore, float]]:
        """调用重排序服务 HTTP API。"""
        documents = [n.get_content() for n in nodes]
        payload = {"query": query, "documents": documents}

        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{self._settings.api_base}/rerank",
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()

        results: list[tuple[NodeWithScore, float]] = []
        for item in data.get("results", []):
            idx = item["index"]
            score = item["relevance_score"]
            results.append((nodes[idx], score))
        return results
