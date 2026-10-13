from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from lea_authkit import Actor
from lea_rag.db.database import Base
from lea_rag.db.models import Document, DocumentChunk, DocumentSection, KnowledgeBase
from lea_rag.server_governance import (
    KnowledgeReviewBody,
    OperationReasonBody,
    apply_knowledge_review,
    cancel_document_job,
    retry_document_job,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def test_knowledge_review_rejects_stale_snapshot_and_applies_tenant_scope(db):
    kb = KnowledgeBase(
        tenant_id="t1", name="kb", owner="alice", visibility="private", enabled=True
    )
    db.add(kb)
    db.commit()
    db.refresh(kb)

    with pytest.raises(HTTPException) as error:
        apply_knowledge_review(
            kb.id,
            KnowledgeReviewBody(resource_version="stale", target_scope="tenant"),
            db,
        )
    assert error.value.status_code == 409

    apply_knowledge_review(
        kb.id,
        KnowledgeReviewBody(
            resource_version=kb.updated_at.isoformat(), target_scope="tenant"
        ),
        db,
    )
    db.refresh(kb)
    assert kb.owner == ""
    assert kb.visibility == "shared"


def test_document_cancel_refuses_to_kill_started_worker(db, monkeypatch):
    document = Document(
        tenant_id="t1",
        kb_id=1,
        filename="a.pdf",
        status="processing",
        celery_task_id="task-1",
    )
    db.add(document)
    db.commit()
    monkeypatch.setattr(
        "lea_rag.server_governance.celery_app.AsyncResult",
        lambda _task_id: SimpleNamespace(state="STARTED"),
    )
    audit_calls = []
    monkeypatch.setenv("LEA_CONFIG_URL", "http://config.test")
    monkeypatch.setattr(
        "lea_rag.server_governance.httpx.post",
        lambda *args, **kwargs: (
            audit_calls.append(kwargs["json"])
            or SimpleNamespace(raise_for_status=lambda: None)
        ),
    )

    with pytest.raises(HTTPException) as error:
        cancel_document_job(
            document.id,
            OperationReasonBody(reason="operator request"),
            db,
            Actor("root", "t1", ("admin",)),
        )
    assert error.value.status_code == 409
    assert audit_calls[0]["result"] == "blocked"


def test_document_retry_cleans_partial_rows_before_enqueue(db, monkeypatch):
    document = Document(
        tenant_id="t1", kb_id=1, filename="a.pdf", status="error"
    )
    db.add(document)
    db.flush()
    db.add(DocumentChunk(document_id=document.id, kb_id=1, content="old"))
    db.add(
        DocumentSection(
            document_id=document.id, kb_id=1, level=1, title="old", position=0
        )
    )
    db.commit()

    # __init__ 也得挡掉：它在构造时就去连 Milvus（connections.connect），只 patch
    # delete_by_document 的话实例化那一步先炸，报的是 grpc.FutureTimeoutError ——
    # 看上去像网络问题，其实是这条用例在干净环境里压根跑不起来。这条用例要验的是
    # "重试前把残留的 chunk/section 删干净"，跟 Milvus 实际连通性无关。
    monkeypatch.setattr(
        "lea_rag.index.milvus_index.MilvusKBIndex.__init__",
        lambda _self, *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "lea_rag.index.milvus_index.MilvusKBIndex.delete_by_document",
        lambda _self, _document_id: None,
    )
    monkeypatch.setattr(
        "lea_rag.server_governance.celery_app.send_task",
        lambda *args, **kwargs: SimpleNamespace(id="retry-task"),
    )
    audit_calls = []
    monkeypatch.setenv("LEA_CONFIG_URL", "http://config.test")
    monkeypatch.setattr(
        "lea_rag.server_governance.httpx.post",
        lambda *args, **kwargs: (
            audit_calls.append(kwargs["json"])
            or SimpleNamespace(raise_for_status=lambda: None)
        ),
    )
    result = retry_document_job(
        document.id,
        OperationReasonBody(reason="fixed parser dependency"),
        db,
        Actor("root", "t1", ("admin",)),
    )
    assert result["status"] == "processing"
    assert result["celery_task_id"] == "retry-task"
    assert db.query(DocumentChunk).count() == 0
    assert db.query(DocumentSection).count() == 0
    assert audit_calls[0]["action"] == "document_job.retry"
    assert audit_calls[0]["tenant_id"] == "t1"
