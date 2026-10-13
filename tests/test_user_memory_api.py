from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from lea_authkit import Actor, require_actor
from lea_rag.db.database import Base, get_db
from lea_rag.db.models import UserMemory
from lea_rag.memory.service import MemoryService
from lea_rag.server_user_memory import router


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, autoflush=False)

    def override_db():
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[require_actor] = lambda: Actor("alice", "tenant-a")

    test_client = TestClient(app)
    test_client.Session = session_factory
    return test_client


def seed_memory(
    client, *, user_id="alice", tenant_id="tenant-a",
    content="偏好简洁回答", scope="global",
) -> int:
    db = client.Session()
    try:
        item = UserMemory(
            user_id=user_id,
            tenant_id=tenant_id,
            content=content,
            category="preference",
            importance=7,
            scope=scope,
        )
        db.add(item)
        db.commit()
        db.refresh(item)
        return item.id
    finally:
        db.close()


def test_list_only_returns_current_users_memories(client):
    own_id = seed_memory(client, scope="agent:12")
    seed_memory(client, user_id="bob", content="不应看到")
    seed_memory(client, tenant_id="tenant-b", content="跨租户也不应看到")

    response = client.get("/memories")

    assert response.status_code == 200
    assert [item["id"] for item in response.json()] == [own_id]
    assert response.json()[0]["content"] == "偏好简洁回答"
    assert response.json()[0]["scope"] == "agent:12"


def test_create_uses_authenticated_identity(client, monkeypatch):
    add = AsyncMock(return_value=42)
    monkeypatch.setattr(MemoryService, "add", add)

    response = client.post(
        "/memories",
        json={
            "content": "  用户正在准备期末考试  ",
            "category": "progress",
            "importance": 8,
            "scope": "agent:12",
            "user_id": "mallory",
            "tenant_id": "tenant-b",
        },
    )

    assert response.status_code == 201
    assert response.json() == {"id": 42, "scope": "agent:12"}
    add.assert_awaited_once_with(
        user_id="alice",
        tenant_id="tenant-a",
        content="用户正在准备期末考试",
        category="progress",
        importance=8,
        scope="agent:12",
    )


def test_update_is_scoped_to_current_user(client, monkeypatch):
    own_id = seed_memory(client)
    other_id = seed_memory(client, user_id="bob")
    update = AsyncMock(return_value=True)
    monkeypatch.setattr(MemoryService, "update", update)

    response = client.put(
        f"/memories/{own_id}",
        json={"content": "  更偏好要点式回答  ", "importance": 9, "scope": "agent:19"},
    )
    assert response.status_code == 200
    update.assert_awaited_once_with(
        own_id,
        content="更偏好要点式回答",
        category=None,
        importance=9,
        scope="agent:19",
        user_id="alice",
        tenant_id="tenant-a",
    )

    response = client.put(f"/memories/{other_id}", json={"content": "越权修改"})
    assert response.status_code == 404
    assert update.await_count == 1


@pytest.mark.parametrize("scope", ["user:alice", "agent:0", "agent:-1", "agent:abc", "tenant"])
def test_create_rejects_invalid_scope(client, scope):
    response = client.post("/memories", json={"content": "测试", "scope": scope})
    assert response.status_code == 422


def test_update_rejects_invalid_scope(client):
    memory_id = seed_memory(client)
    response = client.put(f"/memories/{memory_id}", json={"scope": "agent:other"})
    assert response.status_code == 422


def test_delete_is_scoped_to_current_user(client, monkeypatch):
    own_id = seed_memory(client)
    other_id = seed_memory(client, tenant_id="tenant-b")
    delete = AsyncMock(return_value=True)
    monkeypatch.setattr(MemoryService, "delete", delete)

    response = client.delete(f"/memories/{own_id}")
    assert response.status_code == 200
    delete.assert_awaited_once_with(own_id, user_id="alice", tenant_id="tenant-a")

    response = client.delete(f"/memories/{other_id}")
    assert response.status_code == 404
    assert delete.await_count == 1


@pytest.mark.asyncio
async def test_service_mutations_keep_identity_filter(client, monkeypatch):
    other_id = seed_memory(client, user_id="bob")
    service = MemoryService()
    monkeypatch.setattr(service, "_get_db", client.Session)

    assert await service.update(
        other_id,
        content="越权修改",
        user_id="alice",
        tenant_id="tenant-a",
    ) is False
    assert await service.delete(
        other_id,
        user_id="alice",
        tenant_id="tenant-a",
    ) is False


@pytest.mark.asyncio
async def test_service_scope_change_rebuilds_vector(client, monkeypatch):
    memory_id = seed_memory(client, scope="global")
    service = MemoryService()
    sync = AsyncMock()
    monkeypatch.setattr(service, "_get_db", client.Session)
    monkeypatch.setattr(service, "_delete_from_milvus", lambda _: None)
    monkeypatch.setattr(service, "_sync_to_milvus", sync)

    assert await service.update(
        memory_id,
        scope="agent:12",
        user_id="alice",
        tenant_id="tenant-a",
    ) is True

    db = client.Session()
    try:
        memory = db.query(UserMemory).filter(UserMemory.id == memory_id).one()
        assert memory.scope == "agent:12"
        assert memory.embedding_synced is False
    finally:
        db.close()
    sync.assert_awaited_once_with(
        memory_id,
        "偏好简洁回答",
        "alice",
        "tenant-a",
        "preference",
        "agent:12",
    )
