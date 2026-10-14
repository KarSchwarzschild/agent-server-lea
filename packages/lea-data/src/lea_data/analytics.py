"""使用分析 — Token 用量、热点话题、行为趋势。

从 Kafka lea.metrics 消费数据并聚合，为 Admin 仪表盘提供数据。
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

import structlog

logger = structlog.get_logger(__name__)


@dataclass
class UsageSnapshot:
    """单次聚合快照。"""
    tenant_id: str
    period: str
    total_requests: int = 0
    total_tokens: int = 0
    avg_latency_ms: float = 0.0
    error_count: int = 0
    top_skills: list[tuple[str, int]] = field(default_factory=list)
    top_queries: list[tuple[str, int]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "tenant_id": self.tenant_id,
            "period": self.period,
            "total_requests": self.total_requests,
            "total_tokens": self.total_tokens,
            "avg_latency_ms": round(self.avg_latency_ms, 1),
            "error_count": self.error_count,
            "error_rate": round(self.error_count / max(self.total_requests, 1), 4),
            "top_skills": [{"skill": s, "count": c} for s, c in self.top_skills],
            "top_queries": [{"query": q, "count": c} for q, c in self.top_queries],
        }


class UsageAggregator:
    """内存聚合器 — 收集 metrics 事件后按租户/时间段汇总。

    生产环境中应由 Kafka consumer 喂数据，定期 flush 到 DB。
    """

    def __init__(self):
        self._events: list[dict] = []

    def ingest(self, event: dict) -> None:
        self._events.append(event)

    def snapshot(self, tenant_id: str, period: str = "last_24h") -> UsageSnapshot:
        tenant_events = [e for e in self._events if e.get("tenant_id") == tenant_id]

        total_tokens = sum(e.get("tokens", 0) for e in tenant_events)
        latencies = [e["latency_ms"] for e in tenant_events if "latency_ms" in e]
        avg_latency = sum(latencies) / len(latencies) if latencies else 0.0
        errors = sum(1 for e in tenant_events if e.get("error"))

        skill_counter: Counter[str] = Counter()
        query_counter: Counter[str] = Counter()
        for e in tenant_events:
            if sk := e.get("skill"):
                skill_counter[sk] += 1
            if q := e.get("query"):
                short = q[:50]
                query_counter[short] += 1

        return UsageSnapshot(
            tenant_id=tenant_id,
            period=period,
            total_requests=len(tenant_events),
            total_tokens=total_tokens,
            avg_latency_ms=avg_latency,
            error_count=errors,
            top_skills=skill_counter.most_common(10),
            top_queries=query_counter.most_common(10),
        )

    def clear(self) -> None:
        self._events.clear()
