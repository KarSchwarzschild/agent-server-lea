"""专家级 MCP server 绑定 → agent-config 展开成工具行的契约（migrations/024）。

绑一个 MCP server = 用它同步出的**全部**工具行，粒度跟会话级挂载一致。这条链只有
一处逻辑：/internal/agent-config 组装 tools 时，把绑定的 server 展开成 Tool 行、
并跟逐个 tool 绑的结果**按 tool id 去重**合并。这个文件盯的就是那处。

盯四条，都是静默失效的：
    ① 绑了 server → 它的工具行出现在下发的 tools 里
    ② 同一个工具既逐个绑又随 server 绑 → 只出现一次（去重）
    ③ server 停用 / 绑定 enabled=False → 不下发（跟 tool 两级停用同理）
    ④ server 下某个工具停用 → 那个不下发，其余照常
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from lea_config.database import OWNER_SCOPE_PLATFORM, Base, get_db

TENANT = "mcp-bind-t1"


@pytest.fixture
def client():
    import lea_config.models  # noqa: F401 — 让 Base.metadata 收齐所有表
    from lea_config.api.internal import router

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


def _mcp_tool(db, server_id, name, enabled=True):
    from lea_config.models.tool import Tool
    t = Tool(
        tenant_id=TENANT, name=name, description="", execution_type="mcp",
        schema_json={"type": "object", "properties": {}},
        enabled=enabled, owner_scope=OWNER_SCOPE_PLATFORM, mcp_server_id=server_id,
    )
    db.add(t)
    db.flush()
    return t.id


def seed_agent_with_mcp(client, *, server_enabled=True, binding_enabled=True,
                        one_tool_disabled=False):
    """一个 agent + 一个 MCP server（挂 2 个工具）+ 一条 server 绑定。返回 (agent_id, tool_names)。"""
    from lea_config.models.agent import Agent
    from lea_config.models.binding import AgentMcpBinding
    from lea_config.models.mcp_server import MCPServer

    db = client.Session()
    try:
        agent = Agent(tenant_id=TENANT, name="a", model="", owner_scope=OWNER_SCOPE_PLATFORM)
        db.add(agent)
        db.flush()

        srv = MCPServer(tenant_id=TENANT, name="github", owner_scope=OWNER_SCOPE_PLATFORM,
                        enabled=server_enabled)
        db.add(srv)
        db.flush()

        _mcp_tool(db, srv.id, "gh_search")
        _mcp_tool(db, srv.id, "gh_create_issue", enabled=not one_tool_disabled)

        db.add(AgentMcpBinding(agent_id=agent.id, mcp_server_id=srv.id, enabled=binding_enabled))
        db.commit()
        return agent.id
    finally:
        db.close()


def fetch_tool_names(client, agent_id: int) -> list[str]:
    r = client.get(f"/internal/agent-config/{agent_id}?tenant_id={TENANT}")
    assert r.status_code == 200, r.text
    return [t["name"] for t in r.json()["tools"]]


# ── ① 展开 ──────────────────────────────────────────────────────────────

def test_bound_mcp_server_expands_to_its_tools(client):
    names = fetch_tool_names(client, seed_agent_with_mcp(client))
    assert set(names) == {"gh_search", "gh_create_issue"}


# ── ② 去重 ──────────────────────────────────────────────────────────────

def test_direct_and_server_binding_dedup(client):
    """同一个工具既逐个绑又随 server 绑 → 只出现一次。"""
    from lea_config.models.agent import Agent
    from lea_config.models.binding import AgentMcpBinding, AgentToolBinding
    from lea_config.models.mcp_server import MCPServer

    db = client.Session()
    try:
        agent = Agent(tenant_id=TENANT, name="a", model="", owner_scope=OWNER_SCOPE_PLATFORM)
        db.add(agent); db.flush()
        srv = MCPServer(tenant_id=TENANT, name="github", owner_scope=OWNER_SCOPE_PLATFORM)
        db.add(srv); db.flush()
        tid = _mcp_tool(db, srv.id, "gh_search")
        # 既逐个绑这个工具，又绑整个 server
        db.add(AgentToolBinding(agent_id=agent.id, tool_id=tid, enabled=True))
        db.add(AgentMcpBinding(agent_id=agent.id, mcp_server_id=srv.id, enabled=True))
        db.commit()
        aid = agent.id
    finally:
        db.close()

    names = fetch_tool_names(client, aid)
    assert names.count("gh_search") == 1, f"去重失败：{names}"


# ── ③ 两级停用 ────────────────────────────────────────────────────────

def test_disabled_server_delivers_nothing(client):
    assert fetch_tool_names(client, seed_agent_with_mcp(client, server_enabled=False)) == []


def test_disabled_binding_delivers_nothing(client):
    assert fetch_tool_names(client, seed_agent_with_mcp(client, binding_enabled=False)) == []


# ── ④ server 下单个工具停用 ──────────────────────────────────────────

def test_disabled_tool_under_server_excluded(client):
    names = fetch_tool_names(client, seed_agent_with_mcp(client, one_tool_disabled=True))
    assert names == ["gh_search"]
