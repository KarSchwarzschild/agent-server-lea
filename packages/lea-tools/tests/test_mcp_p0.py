"""MCP P0：模型收口 + 传输白名单 + 无进程内缓存 + 幂等 sync。

## 背景：这条链以前从头断到尾

`mcp_servers` 被 lea-config 和 lea-tools **各定义一遍**，前者写 `config` (JSON)，
后者读 `endpoint`。于是经 lea-config 建出来的行 `endpoint` 永远是 NULL，而 `/sync`
和 `mcp_call` 读的都是它 —— **修一处等于没修**。现网 4 行全是这个状态，所以
`execution_type='mcp'` 的工具行至今是 0：同步从来没成功过一次。

migrations/016 收口到 `endpoint`。下面的用例盯的是收口之后每一环的行为，以及三个
之前只存在于注释里的性质：传输白名单、凭证不被缓存、sync 幂等。
"""

from __future__ import annotations

import json
import socket

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from lea_tools.db import Base
from lea_tools.db.models import OWNER_SCOPE_PLATFORM, MCPServer, Tool
from lea_tools.executor.mcp_proxy import MCPSession, check_transport, remote_tool_name
from lea_tools.executor.router import ToolCall

TENANT = "mcp-t1"


@pytest.fixture(autouse=True)
def _fake_dns(monkeypatch):
    """把 DNS 解析换成固定的公网地址。

    url_guard 是 fail-closed 的：解析不了就拒绝。而本文件的 endpoint 是
    `https://notion.example/mcp` —— 一个不存在的域名，真去解析必然失败，于是每条
    用例都会撞在"endpoint 不允许"上。

    **不能因此放宽 url_guard 的判据。** 那道门正是放开用户私有 MCP server 的前置：
    任何登录用户都能让服务端去打任意 URL（169.254 拿云凭证、10.x 打内部服务、
    localhost:6379 是 Redis）。让单测依赖真 DNS 也不对 —— 断网时变红，而那种红跟
    代码无关，更糟的是会有人为了让它变绿而去改判据。

    所以在这里注入一个假解析器。想验 url_guard 本身的行为看 test_url_guard.py，
    那边不打这个 patch。
    """
    def fake_getaddrinfo(host, port, *a, **kw):
        # ⚠️ 别用 203.0.113.x（RFC 5737 文档地址块）—— Python 的 ipaddress 把它算作
        # is_private=True，注进来会让每条用例都撞在"非公网地址"上。第一版就踩了这个。
        # 93.184.216.34 是 example.com 的历史地址，is_global=True。
        return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "",
                 ("93.184.216.34", port or 443))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def mk_server(db, **kw) -> MCPServer:
    defaults = dict(
        tenant_id=TENANT, name="notion", transport="streamable_http",
        endpoint="https://notion.example/mcp", enabled=True,
    )
    s = MCPServer(**{**defaults, **kw})
    db.add(s)
    db.commit()
    db.refresh(s)
    return s


# ── 传输白名单 ────────────────────────────────────────────────────────────

def test_streamable_http_is_allowed():
    assert check_transport("streamable_http") is None


@pytest.mark.parametrize("transport", ["stdio", "sse"])
def test_dead_transports_are_rejected(transport):
    """stdio 和 sse 都关死，理由不同但结论一样。

    stdio = 在网关进程里 fork 子进程。它以前还是**默认值** —— 一旦实现，
            「用户能加 MCP」就等于「用户能在网关进程里执行任意命令」。
    sse   = 长连接绑在某一个副本上，多副本下会飘。

    库层面 migrations/016 把 ENUM 收成一个值了，这里再挡一道：库约束防的是新写入，
    代码防的是**存量脏数据**（迁移跑之前建的行还在进程里被读）。
    """
    reason = check_transport(transport)
    assert reason and transport in reason


def test_unset_transport_is_not_rejected():
    """None 不当成非法。

    这一列在老行上可能是 NULL。把它当非法会让「修复历史数据」变成「先得能读到它」
    的死循环，而 endpoint 那道检查已经能挡住真正不可用的行。
    """
    assert check_transport(None) is None


# ── 发给远端的是远端认识的名字 ────────────────────────────────────────────
#
# 这一组是**真链路验收抓出来的**，不是设计时想到的。前缀 `mcp_{id}_` 是我们加的
# 消歧手段，远端只认识自己那个名字；以前直接把带前缀的名字发了出去，远端回一句
# "unknown tool 'mcp_27_lookup_ticket'"。
#
# 为什么单测原本挡不住：下面所有用例都 mock 掉了 call_tool，正好跳过这一跳。所以
# 这里必须**同时**测纯函数和 mcp_call 真的用了它 —— 只测前者的话，有人把调用点改
# 回 call.tool_name 一样是绿的。

def test_prefix_is_stripped_for_the_remote():
    assert remote_tool_name("mcp_27_lookup_ticket", 27) == "lookup_ticket"


def test_only_this_servers_prefix_is_stripped():
    """别的 server 的前缀不能误剥。

    server 3 和 server 27 的工具名都以 `mcp_` 开头，按 `mcp_` 裸剥会把
    `mcp_27_lookup_ticket` 剥成 `27_lookup_ticket` —— 一个两边都不认识的名字。
    """
    assert remote_tool_name("mcp_27_lookup_ticket", 3) == "mcp_27_lookup_ticket"


def test_remote_name_containing_the_prefix_word_survives():
    """远端工具自己就叫 mcp_something 时不能被截断。"""
    assert remote_tool_name("mcp_5_mcp_status", 5) == "mcp_status"


def test_unprefixed_name_passes_through():
    """前缀对不上就原样发。

    那种行不是 sync 建的（人工插的、或换过命名规则）。原样发至少还有一半机会是对的；
    在这里抛异常只会把一个可能可用的调用变成必然失败。
    """
    assert remote_tool_name("legacy_tool", 27) == "legacy_tool"


@pytest.mark.asyncio
async def test_mcp_call_sends_the_stripped_name(db, monkeypatch):
    """钉住调用点真的用了 remote_tool_name。"""
    server = mk_server(db)
    sent = {}

    async def fake_call_tool(self, name, params):
        sent["name"] = name
        return {"content": "ok"}

    monkeypatch.setattr(MCPSession, "call_tool", fake_call_tool)
    monkeypatch.setattr("lea_tools.db.SessionLocal", lambda: db)

    from lea_tools.executor.mcp_proxy import mcp_call

    await mcp_call(
        ToolCall(tool_name=f"mcp_{server.id}_lookup_ticket", params={},
                 execution_type="mcp", mcp_server_id=server.id),
        credential=None,
    )
    assert sent["name"] == "lookup_ticket"


# ── 凭证不进任何缓存 ──────────────────────────────────────────────────────

def test_no_module_level_session_cache():
    """`_sessions` 必须不存在。

    它以前是 `dict[int, MCPSession]`，按 server_id 分键，而 MCPSession 里装着
    auth_config。缓存键比数据的归属粒度粗 —— 今天最坏是用了过期凭证，放开每用户
    key 之后就是 **alice 的 token 发 bob 的请求**。

    而且它缓存不了任何有价值的东西：MCPSession 里根本没有连接，只有三个字段。

    断言"这个名字不存在"看着很笨，但重新引入一个进程内缓存正是最可能发生的"优化"。
    """
    import lea_tools.executor.mcp_proxy as m

    assert not hasattr(m, "_sessions"), "进程内凭证缓存回来了，见模块头"


@pytest.mark.asyncio
async def test_credential_override_does_not_mutate_the_row(db, monkeypatch):
    """tool_credentials 里的 key 盖过行上的 auth_config 时，**不能改到行本身**。

    原来是就地 `auth["bearer_token"] = credential["api_key"]`，而 `auth` 就是
    SQLAlchemy 的 JSON 属性 —— 同一个 session 后续任何一次 commit 都会把这个临时
    凭证写回库，于是一个用户的 key 变成了整行的默认值。
    """
    server = mk_server(db, auth_config={"api_key": "row-key"})

    captured = {}

    async def fake_call_tool(self, name, params):
        captured["auth"] = dict(self.auth_config)
        return {"content": "ok"}

    monkeypatch.setattr(MCPSession, "call_tool", fake_call_tool)
    monkeypatch.setattr("lea_tools.db.SessionLocal", lambda: db)

    from lea_tools.executor.mcp_proxy import mcp_call

    result = await mcp_call(
        ToolCall(tool_name="x", params={}, execution_type="mcp", mcp_server_id=server.id),
        credential={"api_key": "caller-key"},
    )
    assert result.status == "success"
    # 调用时用的是覆盖后的
    assert captured["auth"]["bearer_token"] == "caller-key"
    # 但行上那份原封不动
    db.expire_all()
    assert db.get(MCPServer, server.id).auth_config == {"api_key": "row-key"}


# ── mcp_call 的前置检查 ───────────────────────────────────────────────────

def test_legacy_stdio_row_is_readable(db):
    """历史行必须**读得出来**，然后才谈得上被拒绝。

    这条钉的是模型用 String 而不是 SQLAlchemy Enum。用 Enum 的话读到 stdio 行直接
    LookupError —— 实测现网 22/24 就是这种行，迁移跑之前 db.get() 会炸成 500，而不是
    返回一句"这个传输方式不再支持"。

    也就是说 Enum 会在代码和迁移之间焊一个**部署顺序依赖**：必须先跑迁移再上线。
    String 版本两个顺序都安全。

    库里的 ENUM（migrations/016）管住写入就够了；Python 层再套一层只换来读取时炸。
    """
    row = mk_server(db, transport="stdio")
    assert row.transport == "stdio"


@pytest.mark.asyncio
@pytest.mark.parametrize("kw,expect", [
    (dict(enabled=False), "disabled"),
    (dict(endpoint=None), "endpoint"),
    (dict(transport="stdio"), "stdio"),
])
async def test_unusable_servers_are_refused_before_dialing(db, monkeypatch, kw, expect):
    """三种不可用状态都要在**发请求之前**被挡住，而且说清是哪一种。

    不挡的后果不同但都难查：disabled 的会照常被调用（migrations/016 把所有搬不过来
    的历史行都停用了，靠的就是这道）；没有 endpoint 的在 `endpoint.rstrip()` 上抛
    AttributeError —— 看着像代码 bug，其实是配置缺失；stdio 的会拿一个 None 去发 HTTP。

    stdio 这一格只有在模型用 String 时才测得到 —— 见上一条。
    """
    server = mk_server(db, **kw)
    monkeypatch.setattr("lea_tools.db.SessionLocal", lambda: db)

    async def explode(*a, **k):
        raise AssertionError("不该走到真正的调用")

    monkeypatch.setattr(MCPSession, "call_tool", explode)

    from lea_tools.executor.mcp_proxy import mcp_call

    result = await mcp_call(
        ToolCall(tool_name="x", params={}, execution_type="mcp", mcp_server_id=server.id),
        credential=None,
    )
    assert result.status == "error"
    assert expect in result.error


# ── 远端报错要当错误处理 ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_is_error_result_becomes_an_error(db, monkeypatch):
    """MCP 的 `isError` 表示「工具自己失败了」，不能当成正常结果喂给模型。

    不认它的话，模型会拿着一句错误信息当数据用 —— 比如把 "API key invalid"
    当成查询结果写进回答里。
    """
    server = mk_server(db)
    monkeypatch.setattr("lea_tools.db.SessionLocal", lambda: db)

    async def fake_rpc(self, method, params):
        return {"isError": True, "content": [{"type": "text", "text": "API key invalid"}]}

    monkeypatch.setattr(MCPSession, "_rpc", fake_rpc)

    from lea_tools.executor.mcp_proxy import mcp_call

    result = await mcp_call(
        ToolCall(tool_name="x", params={}, execution_type="mcp", mcp_server_id=server.id),
        credential=None,
    )
    assert result.status == "error"
    assert "API key invalid" in result.error


# ── sync 的幂等与清理 ─────────────────────────────────────────────────────

def _remote(*names):
    return [
        {"name": n, "description": f"{n} desc",
         "inputSchema": {"type": "object", "required": [n],
                         "properties": {n: {"type": "string"}}}}
        for n in names
    ]


async def run_sync(db, monkeypatch, server, remote):
    async def fake_list(self):
        return remote

    monkeypatch.setattr(MCPSession, "list_tools", fake_list)
    monkeypatch.setattr("lea_tools.api.admin_mcp.get_db", lambda: db)

    from lea_tools.api.admin_mcp import sync_mcp_tools

    return await sync_mcp_tools(
        server_id=server.id, x_tenant_id=TENANT, x_user_role="admin", db=db
    )


@pytest.mark.asyncio
async def test_sync_creates_tools_with_the_executor_schema_column(db, monkeypatch):
    """schema 必须落 `schema_json`。

    写成 input_schema 的话工具建得出来、列得出来，**到了模型面前没有参数** ——
    lea-config → lea-agent 那条链上刚修完同一个坑。MCP 工具走的是同一条下发路，
    所以这里同样要钉。
    """
    server = mk_server(db)
    out = await run_sync(db, monkeypatch, server, _remote("search"))

    assert out["synced"] == 1
    row = db.query(Tool).filter_by(name=f"mcp_{server.id}_search").one()
    assert row.execution_type == "mcp"
    assert row.mcp_server_id == server.id
    assert row.tenant_id == TENANT
    assert row.schema_json["required"] == ["search"]


@pytest.mark.asyncio
async def test_sync_is_idempotent(db, monkeypatch):
    """跑两遍不该出现重复行。

    两个副本、或两个管理员，可能同时 sync 同一个 server。
    """
    server = mk_server(db)
    await run_sync(db, monkeypatch, server, _remote("search", "create"))
    await run_sync(db, monkeypatch, server, _remote("search", "create"))

    assert db.query(Tool).filter_by(mcp_server_id=server.id).count() == 2


@pytest.mark.asyncio
async def test_sync_updates_changed_schema_in_place(db, monkeypatch):
    server = mk_server(db)
    await run_sync(db, monkeypatch, server, _remote("search"))

    changed = [{"name": "search", "description": "新描述",
                "inputSchema": {"type": "object", "properties": {"q": {"type": "string"}}}}]
    await run_sync(db, monkeypatch, server, changed)

    row = db.query(Tool).filter_by(name=f"mcp_{server.id}_search").one()
    assert row.description == "新描述"
    assert list(row.schema_json["properties"]) == ["q"]


# ── sync 出来的工具，归属跟着 server 走 ───────────────────────────────────
#
# 用户私有 MCP server 一放开，这组就是**安全边界**。sync 以前写死
# OWNER_SCOPE_PLATFORM，因为 lea-tools 的 MCPServer 模型不映射归属两列 ——
# server.owner_scope 压根读不出来（跟 Tool 表当年那个病同一例的第三次）。
#
# 那个洞今天不可达：lea-config 的 CRUD 有 6 处硬过滤，server 只可能是 platform 级。
# 但它已经在代码里了，所以先修它再放开 CRUD —— 反过来的话中间有一个真实可利用的窗口：
# alice 私有 Notion 的 14 个工具会全租户可见，别人绑定、调用一路通，用的是 alice 的 key。


def test_model_maps_the_ownership_columns():
    """归属两列必须在**这一侧的模型**里。

    真实表一直有它们（lea-config 那份是 MCPServer(OwnerMixin, ...)，唯一约束也早按
    (tenant_id, owner_scope, owner_user_id, name) 建好了）。不映射的后果不是"少读两列"，
    而是任何跟归属有关的逻辑在这一侧**写不出来** —— sync 于是只能写死 platform。
    """
    cols = MCPServer.__table__.columns
    assert "owner_scope" in cols
    assert "owner_user_id" in cols


@pytest.mark.asyncio
async def test_sync_inherits_private_server_ownership(db, monkeypatch):
    """alice 的私有 server → 工具行也归 alice。

    这是那个洞的正向判据。写死 platform 的话这里会红。
    """
    server = mk_server(db, owner_scope="user", owner_user_id="alice")
    await run_sync(db, monkeypatch, server, _remote("search", "create"))

    rows = db.query(Tool).filter_by(mcp_server_id=server.id).all()
    assert len(rows) == 2
    for row in rows:
        assert row.owner_scope == "user"
        assert row.owner_user_id == "alice"


@pytest.mark.asyncio
async def test_sync_keeps_platform_server_tools_platform(db, monkeypatch):
    """platform server 仍然出 platform 工具 —— 别把修复做成"一律私有"。

    这条是防过度修正的：只断言"私有能继承"的话，把两行写成写死 user 也能过。
    """
    server = mk_server(db)  # 默认 platform
    await run_sync(db, monkeypatch, server, _remote("search"))

    row = db.query(Tool).filter_by(mcp_server_id=server.id).one()
    assert row.owner_scope == OWNER_SCOPE_PLATFORM
    assert row.owner_user_id is None


@pytest.mark.asyncio
async def test_resync_realigns_ownership_after_the_server_moves(db, monkeypatch):
    """server 的归属变了，**已存在的**工具行也要跟着变。

    归属只在新建分支设的话，update 分支会把老行留在旧归属上：

        私有 → 上架 platform    14 行还挂在 alice 名下，「上架了但别人看不到」
        platform → 收回私有     14 行仍然全租户可见，**收回不生效**

    后者是安全问题。所以两个方向都验。
    """
    server = mk_server(db, owner_scope="user", owner_user_id="alice")
    await run_sync(db, monkeypatch, server, _remote("search"))

    # 上架：私有 → 平台
    server.owner_scope = OWNER_SCOPE_PLATFORM
    server.owner_user_id = None
    db.commit()
    await run_sync(db, monkeypatch, server, _remote("search"))

    row = db.query(Tool).filter_by(mcp_server_id=server.id).one()
    assert row.owner_scope == OWNER_SCOPE_PLATFORM, "上架后工具还挂在原主人名下"
    assert row.owner_user_id is None

    # 收回：平台 → 私有。这个方向不生效 = 全租户仍然能用别人的 key。
    server.owner_scope = "user"
    server.owner_user_id = "alice"
    db.commit()
    await run_sync(db, monkeypatch, server, _remote("search"))

    row = db.query(Tool).filter_by(mcp_server_id=server.id).one()
    assert row.owner_scope == "user", "收回之后工具仍然全租户可见"
    assert row.owner_user_id == "alice"


@pytest.mark.asyncio
async def test_sync_removes_tools_the_remote_no_longer_exposes(db, monkeypatch):
    """上游下线的工具要跟着消失。

    留着的话模型会去调一个已经不存在的东西 —— 而它仍然出现在清单里、仍然能被绑定。
    """
    server = mk_server(db)
    await run_sync(db, monkeypatch, server, _remote("search", "create"))

    out = await run_sync(db, monkeypatch, server, _remote("search"))

    assert out["removed"] == [f"mcp_{server.id}_create"]
    names = {t.name for t in db.query(Tool).filter_by(mcp_server_id=server.id)}
    assert names == {f"mcp_{server.id}_search"}


@pytest.mark.asyncio
async def test_tool_filter_narrows_what_gets_synced(db, monkeypatch):
    """tool_filter 是**我们的策略**，收窄一个 server 暴露的工具集。"""
    server = mk_server(db, tool_filter=["search"])
    out = await run_sync(db, monkeypatch, server, _remote("search", "delete_everything"))

    assert out["synced"] == 1
    assert out["tools"] == [f"mcp_{server.id}_search"]


@pytest.mark.asyncio
async def test_sync_records_connection_status(db, monkeypatch):
    """那三列以前从来没被写过 —— connection_status 永远是 'unknown'。"""
    server = mk_server(db)
    await run_sync(db, monkeypatch, server, _remote("search"))

    db.expire_all()
    row = db.get(MCPServer, server.id)
    assert row.connection_status == "connected"
    assert row.last_connected_at is not None
    assert row.last_sync_at is not None
    assert not row.last_error


@pytest.mark.asyncio
async def test_names_are_prefixed_with_server_id(db, monkeypatch):
    """两个 server 各有一个 create_issue，名字不能撞。

    工具名只在单个 MCP server 内唯一，聚合多个必然撞（GitHub 和 Linear 都有
    create_issue）。规范给聚合方的建议就是自己加前缀消歧。
    """
    a = mk_server(db, name="github", endpoint="https://gh.example/mcp")
    b = mk_server(db, name="linear", endpoint="https://ln.example/mcp")

    await run_sync(db, monkeypatch, a, _remote("create_issue"))
    await run_sync(db, monkeypatch, b, _remote("create_issue"))

    names = {t.name for t in db.query(Tool).filter(Tool.execution_type == "mcp")}
    assert names == {f"mcp_{a.id}_create_issue", f"mcp_{b.id}_create_issue"}
