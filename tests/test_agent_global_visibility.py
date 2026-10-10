"""agent 的三段式可见性（跟 tool/skill/mcp 对齐）——重点是**全局平台层**。

migration 之前 agent 用 authkit 的 scoped_filter（tenant_id == 我的，没有 default 全局逃逸），
于是放在 (default, platform) 的官方公共专家对别的租户不可见——是全平台唯一缺全局层的资源。
改成 models/agent.py::visible_to 后补齐。这个文件钉住那条全局层，别再退回去。

    全局平台专家   (default, platform)      谁都能看（跨租户）
    本租户共享     (t1,      platform)      只有 t1 能看
    自己私有       (t1,      user, alice)   只有 alice 能看
"""

from __future__ import annotations

from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from lea_config.database import OWNER_SCOPE_PLATFORM, OWNER_SCOPE_USER, Base, get_db


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("LEA_INTERNAL_TOKEN", "tok")
    monkeypatch.delenv("LEA_AUTHZ_ALLOW_UNTRUSTED_HEADERS", raising=False)
    import lea_config.models  # noqa: F401
    from lea_config.api.agents import router

    engine = create_engine("sqlite:///:memory:",
                           connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autoflush=False)

    def override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = override_get_db
    tc = TestClient(app, raise_server_exceptions=False)
    tc.Session = Session
    return tc


def mk(client, name, tenant, scope, owner=None):
    from lea_config.models.agent import Agent
    db = client.Session()
    try:
        a = Agent(tenant_id=tenant, name=name, model="", enabled=True,
                  owner_scope=scope, owner_user_id=owner, created_by=owner or "sys")
        db.add(a); db.commit(); return a.id
    finally:
        db.close()


def h(user, tenant):
    return {"X-User-ID": user, "X-Tenant-ID": tenant, "X-User-Role": "user"}


def list_ids(client, user, tenant):
    r = client.get("/agents", headers=h(user, tenant))
    assert r.status_code == 200, r.text
    return {a["id"] for a in r.json()["items"]}


def test_global_platform_agent_is_visible_cross_tenant(client):
    g = mk(client, "官方专家", "default", OWNER_SCOPE_PLATFORM)          # 全局
    t1_shared = mk(client, "t1共享", "t1", OWNER_SCOPE_PLATFORM)          # t1 内共享
    t1_priv = mk(client, "bob私有", "t1", OWNER_SCOPE_USER, owner="bob")  # bob 私有
    dflt_priv = mk(client, "default里的私有", "default", OWNER_SCOPE_USER, owner="carol")

    # 另一个租户 t2 的普通用户：只该看到全局那个
    seen = list_ids(client, "alice", "t2")
    assert g in seen, "全局平台专家对别的租户不可见 —— 全局层没生效"
    assert t1_shared not in seen, "别的租户的共享专家泄漏了"
    assert t1_priv not in seen
    assert dflt_priv not in seen, "default 里别人的私有专家被当成全局泄漏了"


def test_owner_sees_own_private_plus_global(client):
    g = mk(client, "官方专家", "default", OWNER_SCOPE_PLATFORM)
    mine = mk(client, "我的私有", "t1", OWNER_SCOPE_USER, owner="alice")
    others = mk(client, "别人的私有", "t1", OWNER_SCOPE_USER, owner="bob")

    seen = list_ids(client, "alice", "t1")
    assert g in seen and mine in seen
    assert others not in seen


def test_get_single_global_agent_cross_tenant(client):
    g = mk(client, "官方专家", "default", OWNER_SCOPE_PLATFORM)
    # t2 用户能 GET 到全局专家（召唤要用）
    r = client.get(f"/agents/{g}", headers=h("alice", "t2"))
    assert r.status_code == 200, r.text
    # 但别人 default 里的私有专家，t2 用户 GET 不到（404，不泄露存在性）
    priv = mk(client, "default私有", "default", OWNER_SCOPE_USER, owner="carol")
    assert client.get(f"/agents/{priv}", headers=h("alice", "t2")).status_code == 404


def test_platform_admin_projection_reports_visibility_and_editability(client):
    global_agent = mk(client, "global", "default", OWNER_SCOPE_PLATFORM)
    tenant_agent = mk(client, "tenant", "t1", OWNER_SCOPE_PLATFORM)
    private_agent = mk(client, "private", "t1", OWNER_SCOPE_USER, owner="bob")

    response = client.get(
        "/agents?page_size=200",
        headers={"X-User-ID": "root", "X-Tenant-ID": "t1", "X-User-Role": "platform_admin"},
    )

    assert response.status_code == 200, response.text
    items = {item["id"]: item for item in response.json()["items"]}
    assert items[global_agent]["visibility"] == "global"
    assert items[tenant_agent]["visibility"] == "tenant"
    assert items[private_agent]["visibility"] == "private"
    assert all(items[agent_id]["can_edit"] for agent_id in (global_agent, tenant_agent, private_agent))
    assert items[private_agent]["tenant_id"] == "t1"
    assert items[private_agent]["owner_user_id"] == "bob"


def test_update_rejects_stale_expected_updated_at(client):
    agent_id = mk(client, "stale", "t1", OWNER_SCOPE_USER, owner="alice")
    db = client.Session()
    try:
        from lea_config.models.agent import Agent

        agent = db.get(Agent, agent_id)
        agent.updated_at = datetime(2026, 1, 2, 3, 4, 5)
        db.commit()
    finally:
        db.close()

    response = client.put(
        f"/agents/{agent_id}",
        headers=h("alice", "t1"),
        json={"description": "stale write", "expected_updated_at": "2026-01-02T03:04:04"},
    )

    assert response.status_code == 409, response.text
    assert "已被修改" in response.json()["detail"]
