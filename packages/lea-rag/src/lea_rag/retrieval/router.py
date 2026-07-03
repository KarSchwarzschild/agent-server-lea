"""检索路由 — 多策略切换 + A/B 影子模式。

迁移自老项目 retrieval_router.py，支持：
- 多种检索策略（向量、关键词、混合）
- A/B 影子模式：同时执行两种策略，返回主策略结果，记录对比指标
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Awaitable

import structlog
from llama_index.core.schema import NodeWithScore

logger = structlog.get_logger(__name__)


class RetrievalStrategy(str, Enum):
    VECTOR = "vector"
    HYBRID = "hybrid"
    KEYWORD = "keyword"


class RouteMode(str, Enum):
    SINGLE = "single"
    SHADOW = "shadow"


@dataclass
class RouteResult:
    nodes: list[NodeWithScore]
    strategy: str
    latency_ms: float
    shadow_nodes: list[NodeWithScore] | None = None
    shadow_strategy: str | None = None
    shadow_latency_ms: float | None = None


RetrieveFn = Callable[[str, int], Awaitable[list[NodeWithScore]]]


class RetrievalRouter:
    """检索路由器 — 根据配置选择检索策略，支持 A/B 影子对比。"""

    def __init__(self):
        self._strategies: dict[str, RetrieveFn] = {}
        self._primary: str = RetrievalStrategy.HYBRID
        self._shadow: str | None = None
        self._mode: RouteMode = RouteMode.SINGLE

    def register(self, name: str, fn: RetrieveFn) -> None:
        self._strategies[name] = fn

    def set_primary(self, strategy: str) -> None:
        self._primary = strategy

    def enable_shadow(self, strategy: str) -> None:
        self._shadow = strategy
        self._mode = RouteMode.SHADOW

    def disable_shadow(self) -> None:
        self._shadow = None
        self._mode = RouteMode.SINGLE

    async def aroute(self, query: str, top_k: int = 5) -> RouteResult:
        primary_fn = self._strategies.get(self._primary)
        if not primary_fn:
            raise ValueError(f"Unknown strategy: {self._primary}")

        if self._mode == RouteMode.SHADOW and self._shadow:
            shadow_fn = self._strategies.get(self._shadow)
            if shadow_fn:
                return await self._run_shadow(query, top_k, primary_fn, shadow_fn)

        t0 = time.perf_counter()
        nodes = await primary_fn(query, top_k)
        latency = (time.perf_counter() - t0) * 1000

        return RouteResult(
            nodes=nodes,
            strategy=self._primary,
            latency_ms=latency,
        )

    async def _run_shadow(
        self,
        query: str,
        top_k: int,
        primary_fn: RetrieveFn,
        shadow_fn: RetrieveFn,
    ) -> RouteResult:
        """并发执行主策略和影子策略。"""
        t0 = time.perf_counter()

        primary_task = asyncio.create_task(primary_fn(query, top_k))
        shadow_task = asyncio.create_task(shadow_fn(query, top_k))

        primary_nodes = await primary_task
        primary_latency = (time.perf_counter() - t0) * 1000

        shadow_nodes: list[NodeWithScore] | None = None
        shadow_latency: float | None = None
        try:
            shadow_nodes = await asyncio.wait_for(shadow_task, timeout=10.0)
            shadow_latency = (time.perf_counter() - t0) * 1000
        except (asyncio.TimeoutError, Exception):
            logger.warning("shadow_retrieval_failed", shadow=self._shadow)

        if shadow_nodes is not None:
            overlap = self._compute_overlap(primary_nodes, shadow_nodes)
            logger.info(
                "shadow_comparison",
                primary=self._primary,
                shadow=self._shadow,
                primary_latency_ms=round(primary_latency, 1),
                shadow_latency_ms=round(shadow_latency, 1) if shadow_latency else None,
                overlap_ratio=round(overlap, 3),
            )

        return RouteResult(
            nodes=primary_nodes,
            strategy=self._primary,
            latency_ms=primary_latency,
            shadow_nodes=shadow_nodes,
            shadow_strategy=self._shadow,
            shadow_latency_ms=shadow_latency,
        )

    @staticmethod
    def _compute_overlap(a: list[NodeWithScore], b: list[NodeWithScore]) -> float:
        ids_a = {n.node_id for n in a}
        ids_b = {n.node_id for n in b}
        if not ids_a or not ids_b:
            return 0.0
        return len(ids_a & ids_b) / len(ids_a | ids_b)
