"""MemoryService — 记忆 CRUD + Milvus 向量同步。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import structlog

from lea_rag.memory.config import get_memory_settings

logger = structlog.get_logger()


@dataclass
class MemoryItem:
    id: int
    content: str
    category: str
    importance: int
    access_count: int
    created_at: datetime
    score: float = 0.0
    scope: str = "global"


class MemoryService:
    """记忆服务 — 管理用户长期记忆的存取和向量同步。"""

    def __init__(self):
        self._settings = get_memory_settings()
        self._embed_model = None

    def _get_db(self):
        from lea_rag.db import SessionLocal
        return SessionLocal()

    async def _get_embedding(self, text: str) -> list[float]:
        """获取文本的 embedding 向量。"""
        from lea_rag.embeddings.embedding_client import EmbeddingClient

        api_base = self._settings.get_effective_embedding_api_base()
        model = self._settings.get_effective_embedding_model()
        embedder = EmbeddingClient(api_base=api_base, model=model)
        return await embedder.aembed_text(text)

    async def _get_embeddings_batch(self, texts: list[str]) -> list[list[float]]:
        """批量获取 embedding。"""
        from lea_rag.embeddings.embedding_client import EmbeddingClient

        api_base = self._settings.get_effective_embedding_api_base()
        model = self._settings.get_effective_embedding_model()
        embedder = EmbeddingClient(api_base=api_base, model=model)
        return await embedder.aembed_texts(texts)

    async def add(
        self,
        user_id: str,
        content: str,
        *,
        category: str = "fact",
        importance: int = 5,
        tenant_id: str = "default",
        source_session_id: str | None = None,
        scope: str = "global",
    ) -> int:
        """添加一条记忆，同步写入 MySQL。向量异步同步。"""
        from lea_rag.db.models import UserMemory

        db = self._get_db()
        try:
            memory = UserMemory(
                tenant_id=tenant_id,
                user_id=user_id,
                content=content,
                category=category,
                importance=importance,
                source_session_id=source_session_id or "",
                scope=scope,
                embedding_synced=False,
            )
            db.add(memory)
            db.commit()
            db.refresh(memory)
            memory_id = memory.id
        finally:
            db.close()

        try:
            await self._sync_to_milvus(memory_id, content, user_id, tenant_id, category, scope)
        except Exception as e:
            logger.warning("memory_vector_sync_failed", memory_id=memory_id, error=str(e))

        return memory_id

    async def _sync_to_milvus(
        self, memory_id: int, content: str, user_id: str, tenant_id: str, category: str, scope: str = "global"
    ) -> None:
        """将单条记忆同步到 Milvus，并标记 embedding_synced。"""
        from pymilvus import Collection, connections, utility

        vec = await self._get_embedding(content)
        settings = self._settings
        collection_name = settings.milvus_collection

        self._ensure_collection(collection_name, len(vec))

        conn_alias = "memory_service"
        import os
        milvus_uri = os.getenv("LEA_MILVUS_URI", "http://localhost:19530")
        connections.connect(alias=conn_alias, uri=milvus_uri)

        col = Collection(collection_name, using=conn_alias)
        col.insert([{
            "memory_id": memory_id,
            "user_id": user_id,
            "tenant_id": tenant_id,
            "category": category,
            "scope": scope,
            "content": content,
            "embedding": vec,
        }])
        col.flush()
        connections.disconnect(conn_alias)

        db = self._get_db()
        try:
            from lea_rag.db.models import UserMemory
            db.query(UserMemory).filter(UserMemory.id == memory_id).update(
                {"embedding_synced": True}
            )
            db.commit()
        finally:
            db.close()

    def _ensure_collection(self, name: str, dim: int) -> None:
        """确保 Milvus collection 存在。"""
        import os

        from pymilvus import (
            Collection,
            CollectionSchema,
            DataType,
            FieldSchema,
            connections,
            utility,
        )

        conn_alias = "memory_ensure"
        milvus_uri = os.getenv("LEA_MILVUS_URI", "http://localhost:19530")
        connections.connect(alias=conn_alias, uri=milvus_uri)

        if utility.has_collection(name, using=conn_alias):
            connections.disconnect(conn_alias)
            return

        fields = [
            FieldSchema("id", DataType.INT64, is_primary=True, auto_id=True),
            FieldSchema("memory_id", DataType.INT64),
            FieldSchema("user_id", DataType.VARCHAR, max_length=64),
            FieldSchema("tenant_id", DataType.VARCHAR, max_length=64),
            FieldSchema("category", DataType.VARCHAR, max_length=32),
            FieldSchema("scope", DataType.VARCHAR, max_length=100),
            FieldSchema("content", DataType.VARCHAR, max_length=2048),
            FieldSchema("embedding", DataType.FLOAT_VECTOR, dim=dim),
        ]
        schema = CollectionSchema(fields, description="User long-term memories")
        col = Collection(name, schema, using=conn_alias)
        col.create_index(
            "embedding",
            {"index_type": "IVF_FLAT", "metric_type": "COSINE", "params": {"nlist": 128}},
        )
        col.load()
        connections.disconnect(conn_alias)
        logger.info("milvus_memory_collection_created", name=name, dim=dim)

    async def get_all(
        self, user_id: str, tenant_id: str = "default", category: str | None = None
    ) -> list[MemoryItem]:
        """获取用户所有记忆（支持 category 过滤）。"""
        from lea_rag.db.models import UserMemory

        db = self._get_db()
        try:
            q = db.query(UserMemory).filter(
                UserMemory.user_id == user_id,
                UserMemory.tenant_id == tenant_id,
            )
            if category:
                q = q.filter(UserMemory.category == category)
            rows = q.order_by(UserMemory.created_at.desc()).all()
            return [
                MemoryItem(
                    id=r.id, content=r.content, category=r.category or "fact",
                    importance=r.importance or 5, access_count=r.access_count or 0,
                    created_at=r.created_at, score=0.0,
                )
                for r in rows
            ]
        finally:
            db.close()

    async def update(self, memory_id: int, content: str | None = None, category: str | None = None) -> bool:
        """更新记忆内容或类别。内容变更时重新同步向量。"""
        from lea_rag.db.models import UserMemory

        db = self._get_db()
        try:
            mem = db.query(UserMemory).filter(UserMemory.id == memory_id).first()
            if not mem:
                return False
            if content is not None:
                mem.content = content
                mem.embedding_synced = False
            if category is not None:
                mem.category = category
            db.commit()

            if content is not None:
                try:
                    await self._sync_to_milvus(
                        mem.id, mem.content, mem.user_id, mem.tenant_id or "default",
                        mem.category or "fact", getattr(mem, "scope", "global"),
                    )
                except Exception as e:
                    logger.warning("memory_resync_failed", id=memory_id, error=str(e))
            return True
        finally:
            db.close()

    async def delete(self, memory_id: int) -> bool:
        """删除单条记忆（MySQL + Milvus）。"""
        from lea_rag.db.models import UserMemory

        db = self._get_db()
        try:
            mem = db.query(UserMemory).filter(UserMemory.id == memory_id).first()
            if not mem:
                return False
            db.delete(mem)
            db.commit()
        finally:
            db.close()

        try:
            self._delete_from_milvus(memory_id)
        except Exception as e:
            logger.warning("memory_milvus_delete_failed", id=memory_id, error=str(e))
        return True

    async def delete_all(self, user_id: str, tenant_id: str = "default") -> int:
        """清空用户所有记忆。"""
        from lea_rag.db.models import UserMemory

        db = self._get_db()
        try:
            count = db.query(UserMemory).filter(
                UserMemory.user_id == user_id,
                UserMemory.tenant_id == tenant_id,
            ).delete()
            db.commit()
        finally:
            db.close()

        try:
            self._delete_from_milvus_by_user(user_id, tenant_id)
        except Exception as e:
            logger.warning("memory_milvus_delete_all_failed", user_id=user_id, error=str(e))
        return count

    def _delete_from_milvus(self, memory_id: int) -> None:
        import os
        from pymilvus import Collection, connections

        conn_alias = "memory_del"
        milvus_uri = os.getenv("LEA_MILVUS_URI", "http://localhost:19530")
        connections.connect(alias=conn_alias, uri=milvus_uri)
        col = Collection(self._settings.milvus_collection, using=conn_alias)
        col.delete(f"memory_id == {memory_id}")
        connections.disconnect(conn_alias)

    def _delete_from_milvus_by_user(self, user_id: str, tenant_id: str) -> None:
        import os
        from pymilvus import Collection, connections

        conn_alias = "memory_del_all"
        milvus_uri = os.getenv("LEA_MILVUS_URI", "http://localhost:19530")
        connections.connect(alias=conn_alias, uri=milvus_uri)
        col = Collection(self._settings.milvus_collection, using=conn_alias)
        col.delete(f'user_id == "{user_id}" and tenant_id == "{tenant_id}"')
        connections.disconnect(conn_alias)
