"""记忆检索器 — dense-only + 多信号融合排序。

与知识库检索不同：
- 不用 sparse/BM25（短 fact 文本稀疏向量无区分度）
- 不用 reranker（fact 本身是原子级，无噪声段落）
- 多信号融合：semantic × 0.5 + recency × 0.2 + importance × 0.2 + frequency × 0.1
"""

from __future__ import annotations

import math
from datetime import datetime

import structlog

from lea_rag.memory.config import get_memory_settings
from lea_rag.memory.service import MemoryItem

logger = structlog.get_logger()


def _recency_score(created_at: datetime, half_life_days: float = 30.0) -> float:
    """时效性衰减：半衰期 30 天，越新越高。"""
    if not created_at:
        return 0.0
    now = datetime.now()
    age_days = (now - created_at).total_seconds() / 86400.0
    return math.exp(-0.693 * age_days / half_life_days)


def _frequency_score(access_count: int, cap: int = 50) -> float:
    """使用频率归一化：log 平滑，cap 避免过高权重。"""
    if access_count <= 0:
        return 0.0
    return min(math.log1p(access_count) / math.log1p(cap), 1.0)


class MemoryRetriever:
    """记忆检索器：Milvus dense search + 多信号融合。"""

    def __init__(self):
        self._settings = get_memory_settings()

    async def search(
        self,
        user_id: str,
        query: str,
        *,
        top_k: int = 10,
        tenant_id: str = "default",
        scopes: list[str] | None = None,
    ) -> list[MemoryItem]:
        """语义检索用户记忆，返回多信号融合排序后的 top-k。

        scopes: 过滤记忆作用域，如 ["global", "agent:tutor-001"]。
                None 时返回所有 scope 的记忆。
        """
        from lea_rag.memory.service import MemoryService

        svc = MemoryService()
        query_vec = await svc._get_embedding(query)

        candidates = self._milvus_search(query_vec, user_id, tenant_id, scopes=scopes, top_k=20)
        if not candidates:
            return []

        enriched = self._enrich_with_db_meta(candidates)
        ranked = self._fuse_scores(enriched)

        self._update_access_counts([m["memory_id"] for m in ranked[:top_k]])

        return [
            MemoryItem(
                id=m["memory_id"],
                content=m["content"],
                category=m.get("category", "fact"),
                importance=m.get("importance", 5),
                access_count=m.get("access_count", 0),
                created_at=m.get("created_at", datetime.now()),
                score=m["final_score"],
                scope=m.get("scope", "global"),
            )
            for m in ranked[:top_k]
        ]

    def _milvus_search(
        self, query_vec: list[float], user_id: str, tenant_id: str,
        *, scopes: list[str] | None = None, top_k: int = 20,
    ) -> list[dict]:
        """Milvus dense-only 向量检索。"""
        import os

        from pymilvus import Collection, connections, utility

        conn_alias = "memory_search"
        milvus_uri = os.getenv("LEA_MILVUS_URI", "http://localhost:19530")
        connections.connect(alias=conn_alias, uri=milvus_uri)

        col_name = self._settings.milvus_collection
        if not utility.has_collection(col_name, using=conn_alias):
            connections.disconnect(conn_alias)
            return []

        col = Collection(col_name, using=conn_alias)
        col.load()

        filter_parts = [
            f'user_id == "{user_id}"',
            f'tenant_id == "{tenant_id}"',
        ]
        if scopes:
            scope_list = ", ".join(f'"{s}"' for s in scopes)
            filter_parts.append(f"scope in [{scope_list}]")
        filter_expr = " and ".join(filter_parts)

        results = col.search(
            data=[query_vec],
            anns_field="embedding",
            param={"metric_type": "COSINE", "params": {"nprobe": 16}},
            limit=top_k,
            expr=filter_expr,
            output_fields=["memory_id", "category", "content", "scope"],
        )
        connections.disconnect(conn_alias)

        candidates = []
        for hits in results:
            for hit in hits:
                candidates.append({
                    "memory_id": hit.entity.get("memory_id"),
                    "category": hit.entity.get("category", "fact"),
                    "content": hit.entity.get("content", ""),
                    "scope": hit.entity.get("scope", "global"),
                    "semantic_score": hit.score,
                })
        return candidates

    def _enrich_with_db_meta(self, candidates: list[dict]) -> list[dict]:
        """从 MySQL 补充元数据。"""
        from lea_rag.db import SessionLocal
        from lea_rag.db.models import UserMemory

        memory_ids = [c["memory_id"] for c in candidates if c.get("memory_id")]
        if not memory_ids:
            return candidates

        db = SessionLocal()
        try:
            rows = db.query(UserMemory).filter(UserMemory.id.in_(memory_ids)).all()
            meta_map = {r.id: r for r in rows}
        finally:
            db.close()

        for c in candidates:
            meta = meta_map.get(c["memory_id"])
            if meta:
                c["importance"] = meta.importance or 5
                c["access_count"] = meta.access_count or 0
                c["created_at"] = meta.created_at
        return candidates

    def _fuse_scores(self, candidates: list[dict]) -> list[dict]:
        """多信号融合排序。"""
        for c in candidates:
            semantic = c.get("semantic_score", 0.0)
            recency = _recency_score(c.get("created_at"))
            importance = (c.get("importance", 5) / 10.0)
            frequency = _frequency_score(c.get("access_count", 0))

            c["final_score"] = (
                semantic * 0.5
                + recency * 0.2
                + importance * 0.2
                + frequency * 0.1
            )
        return sorted(candidates, key=lambda x: x["final_score"], reverse=True)

    def _update_access_counts(self, memory_ids: list[int]) -> None:
        """批量更新被检索到的记忆的 access_count 和 last_accessed_at。"""
        if not memory_ids:
            return
        from lea_rag.db import SessionLocal
        from lea_rag.db.models import UserMemory

        db = SessionLocal()
        try:
            for mid in memory_ids:
                db.query(UserMemory).filter(UserMemory.id == mid).update({
                    "access_count": UserMemory.access_count + 1,
                    "last_accessed_at": datetime.now(),
                })
            db.commit()
        except Exception as e:
            logger.warning("memory_access_update_failed", error=str(e))
        finally:
            db.close()
