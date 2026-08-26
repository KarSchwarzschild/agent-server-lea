"""`POST /agents/{id}/tools` 的可见性隔离。

跟 `bind_skill` 是同一条规则，这个文件的存在理由就是**它以前不在**：那边修了洞、
写了注释、有测试，同一个文件里隔 180 行的 `bind_tool` 原样留着裸的
`AgentToolBinding(agent_id=agent_id, **body.model_dump())`。

## 为什么"反正执行时会被拦住"不算辩护

链上确实还有一道门：lea-tools 的 `resolve_tool` 带租户 + 归属，所以绑成功了也调不动。
但泄漏发生在**下发**那一步，比执行早：工具的 name / description / schema 会进到另一个
人的对话上下文里，而 schema 里通常是业务字段名。等模型去调、被 404 挡住的时候，内部
结构已经送出去了。

所以这些用例断言的是**绑不上**（404），不是"调不通"。

## 三层可见性

    全局平台工具   (default, platform, NULL)     谁都能绑
    本租户共享的   (t1,      platform, NULL)     t1 的人能绑
    自己私有的     (t1,      user,     alice)    只有 alice 能绑
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from lea_config.database import (
    DEFAULT_TENANT_ID,
    OWNER_SCOPE_PLATFORM,
    OWNER_SCOPE_USER,
    Base,
    get_db,
)

T1, T2 = "bind-t1", "bind-t2"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("LEA_INTERNAL_TOKEN", "tok")
    monkeypatch.delenv("LEA_AUTHZ_ALLOW_UNTRUSTED_HEADERS", raising=False)

    import lea_config.models  # noqa: F401
    from lea_config.api.agents import router

    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
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


def h(user: str, tenant: str = T1) -> dict:
    return {"X-User-ID": user, "X-Tenant-ID": tenant}


def mk_agent(client, *, owner: str, tenant: str = T1) -> int:
    from lea_config.models.agent import Agent

    db = client.Session()
    try:
        a = Agent(
            tenant_id=tenant, name=f"agent-of-{owner}", model="",
            owner_scope=OWNER_SCOPE_USER, owner_user_id=owner,
            # created_by 不能省：_require_mine 校验的是**这一列**，不是 owner_user_id。
            # 少了它每个用例都是 403「只能操作自己创建的 Agent」，而那跟被测的规则
            # （工具的可见性）无关 —— 会掩盖掉真正想验的东西。
            created_by=owner,
        )
        db.add(a)
        db.commit()
        return a.id
    finally:
        db.close()


def mk_tool(client, *, tenant: str, scope: str, owner: str | None = None,
            enabled: bool = True, name: str = "t") -> int:
    from lea_config.models.tool import Tool

    db = client.Session()
    try:
        t = Tool(
            tenant_id=tenant,
            name=f"{name}-{tenant}-{owner or 'shared'}",
            execution_type="api",
            schema_json={"type": "object", "properties": {"secret_field": {"type": "string"}}},
            enabled=enabled,
            owner_scope=scope,
            owner_user_id=owner,
        )
        db.add(t)
        db.commit()
        return t.id
    finally:
        db.close()


def bind(client, agent_id: int, tool_id: int, *, user="alice", tenant=T1):
    return client.post(
        f"/agents/{agent_id}/tools",
        json={"tool_id": tool_id, "enabled": True},
        headers=h(user, tenant),
    )


# ── 该放行的 ──────────────────────────────────────────────────────────────

def test_can_bind_global_platform_tool(client):
    """全局平台工具谁都能绑 —— 这是内置工具的正常路径。"""
    agent = mk_agent(client, owner="alice")
    tool = mk_tool(client, tenant=DEFAULT_TENANT_ID, scope=OWNER_SCOPE_PLATFORM)
    assert bind(client, agent, tool).status_code == 200


def test_can_bind_own_tenant_shared_tool(client):
    agent = mk_agent(client, owner="alice")
    tool = mk_tool(client, tenant=T1, scope=OWNER_SCOPE_PLATFORM)
    assert bind(client, agent, tool).status_code == 200


def test_can_bind_own_private_tool(client):
    agent = mk_agent(client, owner="alice")
    tool = mk_tool(client, tenant=T1, scope=OWNER_SCOPE_USER, owner="alice")
    assert bind(client, agent, tool).status_code == 200


# ── 该挡住的 ──────────────────────────────────────────────────────────────

def test_cannot_bind_another_users_private_tool(client):
    """同租户里 bob 的私有工具，alice 绑不上。

    这是最容易触发的一格：同一个租户，id 连着，不用猜。
    """
    agent = mk_agent(client, owner="alice")
    tool = mk_tool(client, tenant=T1, scope=OWNER_SCOPE_USER, owner="bob")
    assert bind(client, agent, tool).status_code == 404


def test_cannot_bind_another_tenants_tool(client):
    """跨租户，连共享的也不行。"""
    agent = mk_agent(client, owner="alice")
    tool = mk_tool(client, tenant=T2, scope=OWNER_SCOPE_PLATFORM)
    assert bind(client, agent, tool).status_code == 404


def test_cannot_bind_disabled_tool(client):
    """停用的绑不上。

    绑上了也不会下发（internal.py 按 Tool.enabled 过滤），留一个绑得上但不生效的
    状态只会让人以为配错了别的地方。
    """
    agent = mk_agent(client, owner="alice")
    tool = mk_tool(client, tenant=T1, scope=OWNER_SCOPE_PLATFORM, enabled=False)
    assert bind(client, agent, tool).status_code == 404


def test_rejects_with_404_not_403(client):
    """404 而不是 403 —— 403 等于确认「这个 id 存在」。

    枚举 id 正是这个洞最省事的用法，所以拒绝的形状本身也要不漏信息。
    """
    agent = mk_agent(client, owner="alice")
    tool = mk_tool(client, tenant=T2, scope=OWNER_SCOPE_PLATFORM)
    r = bind(client, agent, tool)
    assert r.status_code == 404
    assert "not found" in r.text.lower()


def test_nonexistent_tool_looks_the_same_as_a_hidden_one(client):
    """不存在的 id 和「存在但看不见」的 id 必须长得一样。

    否则两种响应的差别本身就是一个存在性探针 —— 挡住了绑定却留下了枚举通道。
    """
    agent = mk_agent(client, owner="alice")
    hidden = mk_tool(client, tenant=T2, scope=OWNER_SCOPE_PLATFORM)
    a = bind(client, agent, hidden)
    b = bind(client, agent, 999_999)
    assert a.status_code == b.status_code == 404
    assert a.json() == b.json()


# ── 幂等 ──────────────────────────────────────────────────────────────────

def test_binding_twice_is_idempotent(client):
    """重复绑定不该 500 在唯一索引上。"""
    agent = mk_agent(client, owner="alice")
    tool = mk_tool(client, tenant=T1, scope=OWNER_SCOPE_PLATFORM)
    assert bind(client, agent, tool).status_code == 200
    r = bind(client, agent, tool)
    assert r.status_code == 200
    assert r.json()["enabled"] is True
