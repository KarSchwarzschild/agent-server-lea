"""`POST /internal/mcp/discover` —— 只做出网那一跳。

## 它为什么单独存在

sync 是三件事，各自该归谁不一样（设计见 docs/02-技术方案/MCP_USER_SERVERS.md §4.6）：

    判断有没有资格同步    lea-config   它有 actor
    打远端问 tools/list   **本服务**   它有 egress 和 MCP 客户端
    写 tools 行           lea-config   那张表归它

以前三件都在 lea-tools 的 `/admin/mcp-servers/{id}/sync`，授权就是看一眼
`X-User-Role: admin` —— 那判断不了"这是不是你的 server"。放开用户私有 server 之后
实际状态变成「用户建得了自己的 server 但 sync 不了」。

## 这一组盯的是什么

这个端点**不碰库**（拿的是 endpoint 而不是 server_id），所以它没有归属可判 ——
它唯一的责任是"按给的地址出网，并且不让它指向内网"。于是用例集中在两件事：

    url_guard 真的接上了      不能因为"lea-config 那边校验过"就跳过
    连不通不是异常            返 200 + ok:false，让调用方决定怎么表达
"""

from __future__ import annotations

import socket

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from lea_tools.api.internal_mcp import router


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(router)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _public_dns(monkeypatch):
    """默认把 DNS 解到一个公网地址。

    否则每条用例都会撞在 url_guard 的 fail-closed 上（假域名解析不了 → 拒绝）。
    想验"解析到内网"的用例自己再 patch 一次。
    """
    def fake(host, port, *a, **kw):
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "",
                 ("93.184.216.34", port or 443))]

    monkeypatch.setattr(socket, "getaddrinfo", fake)


def post(client, **over):
    body = {"endpoint": "https://notion.example/mcp", "server_id": 7}
    body.update(over)
    return client.post("/internal/mcp/discover", json=body)


def fake_list_tools(monkeypatch, tools=None, *, boom=None):
    from lea_tools.executor.mcp_proxy import MCPSession

    async def _fake(self):
        if boom:
            raise boom
        return tools if tools is not None else [{"name": "search"}]

    monkeypatch.setattr(MCPSession, "list_tools", _fake)


# ── 正常路径 ──────────────────────────────────────────────────────────────

def test_returns_the_remote_tool_list_verbatim(client, monkeypatch):
    """原样返回，不过滤也不改名。

    tool_filter 是策略、前缀是命名规则 —— 两者都归调用方。这里做了的话，"哪些留下、
    叫什么名字"就有两处决定它。
    """
    remote = [
        {"name": "search", "description": "搜", "inputSchema": {"type": "object"}},
        {"name": "create", "description": "建", "inputSchema": {"type": "object"}},
    ]
    fake_list_tools(monkeypatch, remote)

    body = post(client).json()
    assert body["ok"] is True
    assert body["tools"] == remote


def test_empty_tool_list_is_fine(client, monkeypatch):
    """远端一个工具都不暴露 —— 合法，不是错误。"""
    fake_list_tools(monkeypatch, [])
    body = post(client).json()
    assert body["ok"] is True and body["tools"] == []


# ── 连不通 ────────────────────────────────────────────────────────────────

def test_unreachable_remote_is_200_with_ok_false(client, monkeypatch):
    """**请求本身成功了** —— 我们成功地测出了"连不通"。

    502 会让调用方分不清「发现这个动作失败了」和「发现结果是连不通」，而那两种情况
    调用方要做的事不一样（重试 vs 把错误写到那一行上给用户看）。
    """
    fake_list_tools(monkeypatch, boom=ConnectionError("connection refused"))

    r = post(client)
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "connection refused" in body["error"]
    assert body["tools"] == []


def test_error_names_the_exception_type(client, monkeypatch):
    """错误信息要带异常类型 —— 区分"连不上"和"对方返回了垃圾"靠它。"""
    fake_list_tools(monkeypatch, boom=ValueError("not json"))
    assert "ValueError" in post(client).json()["error"]


# ── url_guard 接上了 ──────────────────────────────────────────────────────
#
# ⚠️ 这一组是这个端点的**安全边界**。它按调用方给的地址出网，所以不能因为 lea-config
# 那边校验过就跳过检查 —— 那边只挡 scheme，而且 DNS 可以在写入之后才改指向内网。


def test_endpoint_resolving_to_internal_is_rejected(client, monkeypatch):
    """域名解析到内网 → 400，而且**不发请求**。"""
    def internal(host, port, *a, **kw):
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "",
                 ("169.254.169.254", port or 443))]

    monkeypatch.setattr(socket, "getaddrinfo", internal)

    called = []

    async def _spy(self):
        called.append(1)
        return []

    from lea_tools.executor.mcp_proxy import MCPSession
    monkeypatch.setattr(MCPSession, "list_tools", _spy)

    r = post(client, endpoint="https://innocent.example.com/mcp")
    assert r.status_code == 400
    assert "169.254.169.254" in r.json()["detail"]
    assert called == [], "被拒了却已经去连了"


@pytest.mark.parametrize("endpoint", [
    "http://127.0.0.1:6379/",
    "http://10.0.0.5/mcp",
    "http://localhost:3306/",
    "file:///etc/passwd",
])
def test_obviously_internal_endpoints_are_rejected(client, endpoint):
    assert post(client, endpoint=endpoint).status_code == 400


def test_dead_transport_is_rejected(client, monkeypatch):
    """stdio = 在网关进程里 fork 子进程。库层面关死了，这里再挡一道 ——
    库约束防的是新写入，代码防的是存量脏数据。
    """
    fake_list_tools(monkeypatch)
    r = post(client, transport="stdio")
    assert r.status_code == 400
    assert "stdio" in r.json()["detail"]


def test_unset_transport_is_accepted(client, monkeypatch):
    """None 不当成非法 —— 老行上这一列可能是 NULL。"""
    fake_list_tools(monkeypatch)
    assert post(client, transport=None).status_code == 200


# ── 不碰库 ────────────────────────────────────────────────────────────────

def test_works_without_a_server_row(client, monkeypatch):
    """server_id 给 0 也能跑。

    这个端点**不查库** —— 它拿的是 endpoint 而不是 server_id，所以那一行存不存在跟它
    无关。这条钉的就是这件事：哪天有人在这里加一次 db.get()，它会红。

    不查库是它比 /sync 安全的地方：没有"调用方自选租户"的余地，因为它压根不知道租户
    是什么。
    """
    fake_list_tools(monkeypatch)
    assert post(client, server_id=0).status_code == 200


def test_auth_config_is_passed_to_the_session(client, monkeypatch):
    """凭证要真的用上 —— 传了却不用的话私有 server 全部 401。

    这是拆两跳的代价（auth_config 在服务间传一次），所以至少要确认它没白传。
    """
    from lea_tools.executor.mcp_proxy import MCPSession

    seen = {}

    async def _fake(self):
        seen["auth"] = self.auth_config
        return []

    monkeypatch.setattr(MCPSession, "list_tools", _fake)
    post(client, auth_config={"bearer_token": "tok-abc"})
    assert seen["auth"] == {"bearer_token": "tok-abc"}


# ── 契约 ──────────────────────────────────────────────────────────────────

def test_endpoint_is_required(client):
    r = client.post("/internal/mcp/discover", json={"server_id": 1})
    assert r.status_code == 422
