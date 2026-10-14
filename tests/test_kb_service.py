"""KBService —— 知识库参数/文档 CRUD 的服务层单测。

背景：`create_kb()` 之前完全不读 `reranker_model`/`reranker_enabled`（不是 schema
没声明，是这两个字段写在函数体里也没有），`update_kb()` 的白名单里则一个模型/
参数字段都没有。这两个缺口是用户反馈"创建/编辑知识库有问题"的直接原因，这里
把回归钉住。

`embedding_model`/`embedding_dim` 是否允许改由 **router 层**（server_user_kb.py）
按 `document_count` 判断后再决定要不要塞进传给 `update_kb()` 的 dict —— service
本身只负责白名单里的字段"给了就写"，不重复判断，所以这里不测 document_count
门槛（那是路由层的职责，见 test_server_user_kb.py 风格的路由测试）。
"""

from __future__ import annotations

import pytest
from lea_rag.db import models  # noqa: F401 — 注册所有表到 Base.metadata
from lea_rag.db.database import Base
from lea_rag.services.kb_service import KBService
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

T1 = "tenant-a"


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture
def svc(db):
    return KBService(db)


# ── create_kb：模型/参数字段要落库 ───────────────────────────────────────

def test_create_kb_reads_reranker_fields(svc):
    """回归用例：以前 create_kb() 完全不读这两个字段，无论传不传都落成 ORM 默认值。"""
    kb = svc.create_kb(
        {"name": "kb1", "reranker_model": "bge-reranker-v2-m3", "reranker_enabled": False},
        owner="alice",
        tenant_id=T1,
    )
    assert kb.reranker_model == "bge-reranker-v2-m3"
    assert kb.reranker_enabled is False


def test_create_kb_reads_all_new_model_fields(svc):
    kb = svc.create_kb(
        {
            "name": "kb2",
            "embedding_model": "bge-m3",
            "embedding_dim": 1024,
            "vlm_model": "qwen-vl",
            "vlm_enabled": True,
            "multimodal": True,
        },
        owner="alice",
        tenant_id=T1,
    )
    assert kb.embedding_model == "bge-m3"
    assert kb.embedding_dim == 1024
    assert kb.vlm_model == "qwen-vl"
    assert kb.vlm_enabled is True
    assert kb.multimodal is True


def test_create_kb_defaults_when_fields_omitted(svc):
    """不传的话要落到跟以前一致的默认值，不能因为新增字段就变成 None/空。"""
    kb = svc.create_kb({"name": "kb3"}, owner="alice", tenant_id=T1)
    assert kb.embedding_model == "bge-m3"
    assert kb.embedding_dim == 1024
    assert kb.reranker_model == ""
    assert kb.reranker_enabled is True
    assert kb.vlm_enabled is True
    assert kb.multimodal is False


# ── update_kb：白名单要覆盖新字段 ────────────────────────────────────────

@pytest.mark.parametrize(
    "field,value",
    [
        ("reranker_model", "bge-reranker-v2-m3"),
        ("reranker_enabled", False),
        ("vlm_model", "qwen-vl"),
        ("vlm_enabled", False),
        ("multimodal", True),
        ("embedding_model", "bge-large"),
        ("embedding_dim", 768),
    ],
)
def test_update_kb_whitelist_covers_new_fields(svc, field, value):
    kb = svc.create_kb({"name": "kb"}, owner="alice", tenant_id=T1)
    updated = svc.update_kb(kb.id, {field: value})
    assert getattr(updated, field) == value


def test_update_kb_ignores_unknown_fields(svc):
    """白名单要挡住不认识的字段 —— 不能变成任意 setattr。"""
    kb = svc.create_kb({"name": "kb"}, owner="alice", tenant_id=T1)
    svc.update_kb(kb.id, {"owner": "mallory", "tenant_id": "other-tenant"})
    assert kb.owner == "alice"
    assert kb.tenant_id == T1


def test_update_kb_returns_none_for_missing_kb(svc):
    assert svc.update_kb(999999, {"name": "x"}) is None


# ── 文档：只有删除会让 document_count 回落 ──────────────────────────────

def test_delete_document_decrements_document_count(svc):
    kb = svc.create_kb({"name": "kb"}, owner="alice", tenant_id=T1)
    doc = svc.create_document(kb.id, {"filename": "a.pdf"}, tenant_id=T1)
    svc.db.flush()
    assert svc.get_kb(kb.id).document_count == 1

    assert svc.delete_document(doc.id) is True
    assert svc.get_kb(kb.id).document_count == 0
    assert svc.get_document(doc.id) is None


def test_delete_document_does_not_go_negative(svc):
    """document_count 已经是 0 时再删（比如并发/重复调用）不能变成 -1。"""
    kb = svc.create_kb({"name": "kb"}, owner="alice", tenant_id=T1)
    doc = svc.create_document(kb.id, {"filename": "a.pdf"}, tenant_id=T1)
    svc.delete_document(doc.id)
    kb.document_count = 0
    svc.db.flush()

    # 第二次没有文档可删——delete_document 返回 False，不触碰 document_count。
    assert svc.delete_document(doc.id) is False
    assert svc.get_kb(kb.id).document_count == 0
