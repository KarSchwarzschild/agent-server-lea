"""知识库服务 — CRUD + 文档管理。

多租户安全：所有查询通过 TenantMixin 自动过滤 tenant_id。
用户隔离：visibility 字段控制可见性（private/shared/public）。
"""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy import or_, and_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from lea_server.models.knowledge import Document, DocumentChunk, KnowledgeBase

logger = structlog.get_logger()


class KBService:
    def __init__(self, db: Session):
        self.db = db

    # ── 知识库 CRUD ──────────────────────────────────────

    def create_kb(self, data: dict[str, Any], owner: str, tenant_id: str) -> KnowledgeBase:
        kb = KnowledgeBase(
            name=data["name"],
            description=data.get("description", ""),
            embedding_model=data.get("embedding_model", "bge-m3"),
            retrieval_mode=data.get("retrieval_mode", "hybrid"),
            retrieval_top_k=data.get("retrieval_top_k", 5),
            score_threshold=data.get("score_threshold", 0.5),
            visibility=data.get("visibility", "private"),
            vlm_enabled=data.get("vlm_enabled", True),
            vlm_model=data.get("vlm_model", ""),
            multimodal=data.get("multimodal", False),
            embedding_dim=data.get("embedding_dim", 1024),
            owner=owner,
            tenant_id=tenant_id,
        )
        self.db.add(kb)
        self.db.flush()
        self.db.refresh(kb)
        return kb

    def list_kbs(
        self,
        *,
        user_id: str,
        tenant_id: str,
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[KnowledgeBase], int]:
        """用户可见的知识库：自己的 personal/private + 同租户 shared + 全平台 public。"""
        query = self.db.query(KnowledgeBase).filter(
            KnowledgeBase.enabled == True,  # noqa: E712
            or_(
                and_(KnowledgeBase.visibility.in_(["private", "personal"]), KnowledgeBase.owner == user_id),
                and_(KnowledgeBase.visibility == "shared", KnowledgeBase.tenant_id == tenant_id),
                KnowledgeBase.visibility == "public",
            ),
        )
        total = query.count()
        kbs = query.order_by(KnowledgeBase.id.desc()).offset((page - 1) * size).limit(size).all()
        return kbs, total

    def list_all_kbs(self, *, page: int = 1, size: int = 50) -> tuple[list[KnowledgeBase], int]:
        """管理员查看所有知识库。"""
        query = self.db.query(KnowledgeBase)
        total = query.count()
        kbs = query.order_by(KnowledgeBase.id.desc()).offset((page - 1) * size).limit(size).all()
        return kbs, total

    def get_kb(self, kb_id: int) -> KnowledgeBase | None:
        return self.db.query(KnowledgeBase).filter(KnowledgeBase.id == kb_id).first()

    def update_kb(self, kb_id: int, data: dict[str, Any]) -> KnowledgeBase | None:
        kb = self.get_kb(kb_id)
        if kb is None:
            return None
        for field in ["name", "description", "retrieval_mode", "retrieval_top_k", "score_threshold", "visibility", "enabled"]:
            if field in data:
                setattr(kb, field, data[field])
        self.db.flush()
        return kb

    def delete_kb(self, kb_id: int) -> bool:
        kb = self.get_kb(kb_id)
        if kb is None:
            return False
        self.db.query(DocumentChunk).filter(DocumentChunk.kb_id == kb_id).delete()
        self.db.query(Document).filter(Document.kb_id == kb_id).delete()
        self.db.delete(kb)
        self.db.flush()
        return True

    def get_visible_kb_ids(self, user_id: str, tenant_id: str) -> list[int]:
        """获取用户可见的所有知识库 ID（用于检索过滤）。"""
        rows = (
            self.db.query(KnowledgeBase.id)
            .filter(
                KnowledgeBase.enabled == True,  # noqa: E712
                or_(
                    and_(KnowledgeBase.visibility.in_(["private", "personal"]), KnowledgeBase.owner == user_id),
                    and_(KnowledgeBase.visibility == "shared", KnowledgeBase.tenant_id == tenant_id),
                    KnowledgeBase.visibility == "public",
                ),
            )
            .all()
        )
        return [r[0] for r in rows]

    # ── 个人文档库 ──────────────────────────────────────────

    def get_or_create_personal_kb(self, user_id: str, tenant_id: str) -> KnowledgeBase:
        """获取用户的个人文档库，不存在则创建。并发安全。

        使用 Redis 分布式锁避免多节点/高并发下重复创建。
        """
        kb = self.db.query(KnowledgeBase).filter(
            KnowledgeBase.owner == user_id,
            KnowledgeBase.visibility == "personal",
        ).first()
        if kb:
            return kb

        import redis as redis_lib
        import os
        redis_url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
        r = redis_lib.from_url(redis_url)
        lock_key = f"lea:kb:personal_lock:{user_id}"
        lock = r.lock(lock_key, timeout=10, blocking_timeout=5)

        if not lock.acquire(blocking=True):
            kb = self.db.query(KnowledgeBase).filter(
                KnowledgeBase.owner == user_id,
                KnowledgeBase.visibility == "personal",
            ).first()
            if kb:
                return kb
            raise RuntimeError("Failed to acquire lock for personal KB creation")

        try:
            kb = self.db.query(KnowledgeBase).filter(
                KnowledgeBase.owner == user_id,
                KnowledgeBase.visibility == "personal",
            ).first()
            if kb:
                return kb

            kb = KnowledgeBase(
                name="我的文档",
                description="智能笔记上传的文档",
                visibility="personal",
                owner=user_id,
                tenant_id=tenant_id,
            )
            self.db.add(kb)
            self.db.flush()
            self.db.refresh(kb)
            return kb
        finally:
            lock.release()

    # ── 文档管理 ──────────────────────────────────────────

    def create_document(self, kb_id: int, data: dict[str, Any], tenant_id: str) -> Document:
        doc = Document(
            kb_id=kb_id,
            filename=data["filename"],
            file_key=data.get("file_key", ""),
            file_type=data.get("file_type", "pdf"),
            file_size=data.get("file_size", 0),
            status="processing",
            tenant_id=tenant_id,
        )
        self.db.add(doc)
        self.db.flush()
        self.db.refresh(doc)

        self.db.query(KnowledgeBase).filter(KnowledgeBase.id == kb_id).update(
            {KnowledgeBase.document_count: KnowledgeBase.document_count + 1}
        )
        self.db.flush()

        return doc

    def list_documents(self, kb_id: int, page: int = 1, size: int = 50) -> tuple[list[Document], int]:
        query = self.db.query(Document).filter(Document.kb_id == kb_id)
        total = query.count()
        docs = query.order_by(Document.id.desc()).offset((page - 1) * size).limit(size).all()
        return docs, total

    def get_document(self, doc_id: int) -> Document | None:
        return self.db.query(Document).filter(Document.id == doc_id).first()

    def update_document_status(self, doc_id: int, status: str, **kwargs) -> None:
        doc = self.get_document(doc_id)
        if doc is None:
            return
        doc.status = status
        for k, v in kwargs.items():
            if hasattr(doc, k):
                setattr(doc, k, v)
        self.db.flush()

    def delete_document(self, doc_id: int) -> bool:
        doc = self.get_document(doc_id)
        if doc is None:
            return False
        kb_id = doc.kb_id
        self.db.query(DocumentChunk).filter(DocumentChunk.document_id == doc_id).delete()
        self.db.delete(doc)

        self.db.query(KnowledgeBase).filter(
            KnowledgeBase.id == kb_id,
            KnowledgeBase.document_count > 0,
        ).update({KnowledgeBase.document_count: KnowledgeBase.document_count - 1})

        self.db.flush()
        return True

    # ── 分块管理 ──────────────────────────────────────────

    def save_chunks(self, document_id: int, kb_id: int, chunks: list[dict]) -> int:
        """批量保存文档分块。"""
        for i, chunk_data in enumerate(chunks):
            chunk = DocumentChunk(
                document_id=document_id,
                kb_id=kb_id,
                content=chunk_data["content"],
                chunk_type=chunk_data.get("chunk_type", "text"),
                position=i,
                window_content=chunk_data.get("window_content", ""),
                extra=chunk_data.get("metadata", {}),
            )
            self.db.add(chunk)
        self.db.flush()

        doc = self.get_document(document_id)
        if doc:
            doc.chunk_count = len(chunks)
            self.db.flush()

        return len(chunks)
