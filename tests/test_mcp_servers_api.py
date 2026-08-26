"""lea-config 的 /mcp-servers —— 写进去的列执行器要读得懂，key 不能读出来。

跟 `test_tools_api.py` 盯的是同一类回归，因为这是**同一个病的第二例**：

    这个接口以前写的            执行侧（lea-tools）读的
    ──────────────            ────────────────────
    config (JSON)              endpoint / auth_config / tool_filter

`create_all()` 只建表不改表，两个服务先后打在同一张表上，于是真实表是并集、两组列
都存在。写错一组不会报错 —— 建得出来、列得出来，**一执行就找不到连接配置**。现网
4 行全是这个状态，所以 MCP 工具行至今是 0。

migrations/016 收口到 `endpoint`。下面第一组用例就是钉这件事。

第二组盯的是凭证方向：`auth_config` 写得进去、**读不出来**。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from lea_config.database import Base, get_db

T1, T2 = "mcpapi-t1", "mcpapi-t2"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("LEA_INTERNAL_TOKEN", "tok")
    monkeypatch.delenv("LEA_AUTHZ_ALLOW_UNTRUSTED_HEADERS", raising=False)

    import lea_config.models  # noqa: F401
    from lea_config.api.mcp_servers import router

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


def h(user="admin1", tenant=T1, role="admin") -> dict:
    return {"X-User-ID": user, "X-Tenant-ID": tenant, "X-User-Role": role}


VALID = {
    "name": "notion",
    "endpoint": "https://notion.example/mcp",
}


def mk(client, **over):
    body = {**VALID, **over}
    headers = body.pop("_headers", h())
    return client.post("/mcp-servers", json=body, headers=headers)


# ── 契约：写的列执行器要读得懂 ────────────────────────────────────────────

def test_endpoint_is_required(client):
    """没有 endpoint 就建不出来。

    这是这轮修的核心。以前 endpoint 压根不在创建契约里（只有一个 config JSON），
    于是**每一行建出来都是 NULL**，而 /sync 和 mcp_call 读的都是它。
    """
    r = client.post("/mcp-servers", json={"name": "x"}, headers=h())
    assert r.status_code == 422


def test_endpoint_is_persisted_and_returned(client):
    r = mk(client)
    assert r.status_code == 201, r.text
    assert r.json()["endpoint"] == "https://notion.example/mcp"


def test_dead_config_column_is_gone(client):
    """`config` 不该再出现在响应里 —— 它是被 migrations/016 删掉的死列。

    两组列都留着看起来"更兼容"，实际是重造刚删掉的病。
    """
    assert "config" not in mk(client).json()


@pytest.mark.parametrize("bad", ["notion.example/mcp", "file:///etc/passwd", "ftp://x/y"])
def test_endpoint_must_be_absolute_http(client, bad):
    """只收 http/https 绝对地址。

    这一列的值会被**服务端**拿去发请求，所以它是一个 SSRF 面。file:// 能读本地文件，
    其余 scheme httpx 直接抛 —— 表现成一个看不懂的 500 而不是"你填错了"。

    ⚠️ 这里没挡内网地址。真 SSRF 防护要解析 DNS 查网段、还得防 TOCTOU 和重定向，
    那是「放开用户自己填 URL」的前置，不是这轮的范围。
    """
    r = mk(client, endpoint=bad)
    assert r.status_code == 400
    assert "http" in r.json()["detail"]


def test_transport_defaults_to_the_only_supported_one(client):
    """不传 transport 也能建 —— 只有一个合法值，不该逼调用方填。"""
    assert mk(client).json()["transport"] == "streamable_http"


@pytest.mark.parametrize("dead", ["stdio", "sse"])
def test_dead_transports_are_rejected(client, dead):
    """stdio = 网关进程里 fork 子进程；sse = 长连接绑副本。两条都关死。

    stdio 尤其要挡：它以前还是**默认值**，一旦实现就等于「用户能在网关进程里执行
    任意命令」。
    """
    r = mk(client, transport=dead)
    assert r.status_code == 400
    assert "transport" in r.json()["detail"]


# ── 凭证：写得进，读不出 ──────────────────────────────────────────────────

def test_auth_config_is_never_returned(client):
    """列表/详情里只给一个布尔，不给 key 本身。

    列表接口对整个租户可见。把 bearer token 放进响应等于任何能列 server 的人都能
    拿到它 —— 而这个 key 是发给外部服务的。
    """
    r = mk(client, auth_config={"bearer_token": "secret-abc123"})
    body = r.json()
    assert body["has_auth"] is True
    assert "auth_config" not in body
    assert "secret-abc123" not in r.text


def test_auth_config_is_actually_stored(client):
    """读不出来不等于没写进去 —— 执行侧要用它。"""
    from lea_config.models.mcp_server import MCPServer

    sid = mk(client, auth_config={"bearer_token": "secret-abc123"}).json()["id"]
    db = client.Session()
    try:
        assert db.get(MCPServer, sid).auth_config == {"bearer_token": "secret-abc123"}
    finally:
        db.close()


def test_no_auth_reports_false(client):
    assert mk(client).json()["has_auth"] is False


# ── 改地址等于换了一个服务 ────────────────────────────────────────────────

def test_changing_endpoint_clears_stale_connection_status(client):
    """之前那次「连通」不再代表任何东西。

    不清的话 connection_status='connected' 会一直挂在一个已经指向别处的行上，
    而那正是排查"我改了地址怎么还是老结果"时第一个会看的地方。
    """
    from lea_config.models.mcp_server import MCPServer

    sid = mk(client).json()["id"]

    db = client.Session()
    try:
        row = db.get(MCPServer, sid)
        row.connection_status = "connected"
        row.last_error = None
        db.commit()
    finally:
        db.close()

    r = client.put(f"/mcp-servers/{sid}",
                   json={"endpoint": "https://other.example/mcp"}, headers=h())
    assert r.status_code == 200
    assert r.json()["connection_status"] == "unknown"
    assert r.json()["last_connected_at"] is None


def test_changing_only_the_name_keeps_connection_status(client):
    """改名字不该清状态 —— 连接没变。"""
    from lea_config.models.mcp_server import MCPServer

    sid = mk(client).json()["id"]
    db = client.Session()
    try:
        db.get(MCPServer, sid).connection_status = "connected"
        db.commit()
    finally:
        db.close()

    r = client.put(f"/mcp-servers/{sid}", json={"name": "notion-2"}, headers=h())
    assert r.json()["connection_status"] == "connected"


# ── 删 server 连带删工具 ──────────────────────────────────────────────────

def test_delete_removes_the_synced_tool_rows(client):
    """一个 server → N 个工具，删了 server 那 N 个必须跟着走。

    不删的话留下一批 mcp_server_id 指向不存在 server 的工具：仍然 enabled、
    仍然出现在清单里、仍然能被绑定，**一执行就找不到连接配置**。
    """
    from lea_config.models.tool import Tool

    sid = mk(client).json()["id"]
    db = client.Session()
    try:
        db.add(Tool(tenant_id=T1, name=f"mcp_{sid}_search", execution_type="mcp",
                    schema_json={"type": "object"}, mcp_server_id=sid))
        # 另一个租户里一个**同样 id** 的工具行，用来钉住删除必须带租户条件。
        db.add(Tool(tenant_id=T2, name=f"mcp_{sid}_search", execution_type="mcp",
                    schema_json={"type": "object"}, mcp_server_id=sid))
        db.commit()
    finally:
        db.close()

    assert client.delete(f"/mcp-servers/{sid}", headers=h()).status_code == 204

    db = client.Session()
    try:
        assert db.query(Tool).filter_by(tenant_id=T1, mcp_server_id=sid).count() == 0
        # 别的租户那行不能被误删
        assert db.query(Tool).filter_by(tenant_id=T2, mcp_server_id=sid).count() == 1
    finally:
        db.close()


# ── 权限 ──────────────────────────────────────────────────────────────────

def test_non_admin_cannot_create(client):
    r = mk(client, _headers=h(role="user"))
    assert r.status_code == 403


def test_cross_tenant_is_404(client):
    """跨租户 404 而不是 403 —— 403 会确认这个 id 存在。"""
    sid = mk(client).json()["id"]
    r = client.get(f"/mcp-servers/{sid}", headers=h(tenant=T2))
    assert r.status_code == 404
