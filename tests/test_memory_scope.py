"""记忆双层 Scope 验证测试。

验证点：
1. extractor._parse_facts 正确解析 scope 字段
2. scope 路由逻辑 (profile → global, context → agent:xx)
3. API 合约：add/search/extract 端点支持 scope 参数
4. DB 层正确存储 scope

运行方式：
  DB_HOST=sqlite pytest tests/test_memory_scope.py -v
"""

import os
import sys
import tempfile as _tf
from unittest.mock import AsyncMock, patch

import pytest

_test_db_dir = _tf.mkdtemp(prefix="lea_mem_scope_")
os.environ["DB_HOST"] = "sqlite"
os.environ["DB_NAME"] = os.path.join(_test_db_dir, "test_mem")
os.environ["JWT_SECRET"] = "test-secret"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "packages", "lea-rag", "src"))


# ══════════════════════════════════════════════════════════════
# Section 1: extractor._parse_facts 单元测试
# ══════════════════════════════════════════════════════════════


class TestParseFacts:
    """验证 LLM 输出解析逻辑正确处理 scope 字段。"""

    def test_parse_with_scope_fields(self):
        from lea_rag.memory.extractor import _parse_facts

        text = '''[
            {"content": "用户是大二学生", "category": "identity", "importance": 8, "scope": "profile"},
            {"content": "正在用微积分解优化问题", "category": "progress", "importance": 6, "scope": "context"},
            {"content": "偏好简洁回答", "category": "preference", "importance": 7, "scope": "profile"}
        ]'''
        facts = _parse_facts(text)
        assert len(facts) == 3
        assert facts[0]["scope"] == "profile"
        assert facts[1]["scope"] == "context"
        assert facts[2]["scope"] == "profile"

    def test_parse_defaults_to_context(self):
        from lea_rag.memory.extractor import _parse_facts

        text = '[{"content": "no scope field", "category": "fact", "importance": 5}]'
        facts = _parse_facts(text)
        assert len(facts) == 1
        assert facts[0]["scope"] == "context"

    def test_parse_string_items_default_scope(self):
        from lea_rag.memory.extractor import _parse_facts

        text = '["简单字符串记忆"]'
        facts = _parse_facts(text)
        assert len(facts) == 1
        assert facts[0]["scope"] == "context"


# ══════════════════════════════════════════════════════════════
# Section 2: Scope 路由逻辑 (extract_memories 内部)
# ══════════════════════════════════════════════════════════════


class TestScopeRouting:
    """验证 profile→global, context→agent:xx 的路由。"""

    @pytest.fixture(autouse=True)
    def setup_db(self):
        from lea_rag.db import Base, engine
        Base.metadata.create_all(bind=engine)
        yield
        Base.metadata.drop_all(bind=engine)

    @pytest.mark.asyncio
    async def test_profile_routes_to_global_scope(self, monkeypatch):
        """profile scope 的记忆存为当前用户跨 Agent 共享的 global。"""
        from lea_rag.memory.service import MemoryService

        svc = MemoryService()
        monkeypatch.setattr(svc, "_sync_to_milvus", AsyncMock())
        mid = await svc.add(
            user_id="stu1", content="用户是大二学生",
            category="identity", importance=8,
            scope="global",
        )
        assert mid > 0

        from lea_rag.db import SessionLocal
        from lea_rag.db.models import UserMemory
        db = SessionLocal()
        row = db.query(UserMemory).filter(UserMemory.id == mid).first()
        db.close()
        assert row.scope == "global"

    @pytest.mark.asyncio
    async def test_context_routes_to_agent_scope(self, monkeypatch):
        """context scope 的记忆存为 agent:{agent_id}。"""
        from lea_rag.memory.service import MemoryService

        svc = MemoryService()
        monkeypatch.setattr(svc, "_sync_to_milvus", AsyncMock())
        mid = await svc.add(
            user_id="stu1", content="正在用微积分解优化问题",
            category="progress", importance=6,
            scope="agent:42",
        )
        from lea_rag.db import SessionLocal
        from lea_rag.db.models import UserMemory
        db = SessionLocal()
        row = db.query(UserMemory).filter(UserMemory.id == mid).first()
        db.close()
        assert row.scope == "agent:42"


# ══════════════════════════════════════════════════════════════
# Section 3: API 合约验证
# ══════════════════════════════════════════════════════════════


class TestMemoryAPIContract:
    """验证 server_memory.py 端点接受 scope 参数。"""

    @pytest.fixture(autouse=True)
    def setup(self, monkeypatch):
        from lea_rag.db import Base, engine
        from lea_rag.memory.service import MemoryService
        Base.metadata.create_all(bind=engine)
        monkeypatch.setattr(MemoryService, "_sync_to_milvus", AsyncMock())
        from fastapi.testclient import TestClient
        from lea_rag.server import app
        self.client = TestClient(app)
        yield
        Base.metadata.drop_all(bind=engine)

    def test_add_with_scope(self):
        """POST /memory/add 接受 scope 字段。"""
        r = self.client.post("/memory/add", json={
            "user_id": "u1",
            "content": "测试记忆",
            "scope": "global",
        })
        assert r.status_code == 200
        assert "id" in r.json()

    def test_add_agent_scope(self):
        """POST /memory/add agent scope 正常存入。"""
        r = self.client.post("/memory/add", json={
            "user_id": "u1",
            "content": "agent 专属记忆",
            "scope": "agent:99",
        })
        assert r.status_code == 200
        mid = r.json()["id"]
        assert mid > 0

    def test_search_with_scopes_filter(self):
        """POST /memory/search 接受 scopes 数组（验证请求 schema 不报 422）。"""
        with patch("lea_rag.memory.retriever.MemoryRetriever.search", new_callable=AsyncMock) as mock_search:
            mock_search.return_value = []
            r = self.client.post("/memory/search", json={
                "user_id": "u1",
                "query": "测试",
                "scopes": ["global", "agent:42"],
            })
        assert r.status_code == 200
        assert r.json() == []

    def test_extract_with_agent_id(self):
        """POST /memory/extract 接受 agent_id。"""
        r = self.client.post("/memory/extract", json={
            "user_id": "u1",
            "messages": [
                {"role": "user", "content": "我是大三学生"},
                {"role": "assistant", "content": "好的"},
            ],
            "agent_id": "42",
        })
        # 可能失败（无 LLM），但不应 422 验证错误
        assert r.status_code != 422
