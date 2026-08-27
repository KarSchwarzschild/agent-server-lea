"""`POST /internal/resolve-attachments` —— 会话级挂载的解析。

用户在**单个会话里**临时挂上的工具/知识库（migrations/017 的 session_attachments），
引擎重建会话时靠这个接口把 id 还原成真配置。

## 这一跳有两件事必须对，而且错法都是静默的

**一、归属过滤。** 挂载行没有外键（ref_id 是多态的），所以库里躺着的 id 不代表这个人
现在还够得着它 —— 工具可能被删了、被取消共享了、人可能被移出租户了。这里不重新校验
的话，一条陈旧的挂载记录就是一条越权通道。

**二、失效项静默丢弃，不报错。** 这条跟上一条方向相反但同样重要：如果对失效项报错，
后果是**用户的老会话直接打不开** —— 一个跟他无关的删除操作，让一整段对话变成打不开
的东西。少一个工具远比这个轻。但也不能真的悄无声息，所以失效项进 `dropped` 一起返回，
引擎打日志，"我昨天挂的工具今天不见了"才查得到。

## 形状

`tools` 必须跟 `/internal/agent-config` 的 tools 一模一样 —— 引擎两边用**同一个**
ToolDef 去解。差一个键名就是模型拿到空 schema 然后全靠猜（`schema_json` 那个 bug 的
形状）。所以下面专门有一条钉键名。
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

T1, T2 = "att-t1", "att-t2"
ALICE, BOB = "alice", "bob"

SCHEMA = {
    "type": "object",
    "required": ["warehouse_ref"],
    "properties": {"warehouse_ref": {"type": "string", "description": "库位编号"}},
}


@pytest.fixture
def client():
    import lea_config.models  # noqa: F401
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


def mk_tool(client, *, tenant=T1, scope=OWNER_SCOPE_PLATFORM, owner=None,
            enabled=True, name="stock_lookup") -> int:
    from lea_config.models.tool import Tool

    db = client.Session()
    try:
        t = Tool(
            tenant_id=tenant, name=name, description="按库位查库存",
            execution_type="api", schema_json=SCHEMA, enabled=enabled,
            owner_scope=scope, owner_user_id=owner,
        )
        db.add(t)
        db.commit()
        return t.id
    finally:
        db.close()


def mk_kb(client, *, tenant=T1, visibility="public", owner=ALICE,
          enabled=True, name="kb") -> int:
    from lea_config.models.knowledge import KnowledgeBase

    db = client.Session()
    try:
        k = KnowledgeBase(tenant_id=tenant, name=name, visibility=visibility,
                          owner=owner, enabled=enabled)
        db.add(k)
        db.commit()
        return k.id
    finally:
        db.close()


def resolve(client, items, *, tenant=T1, user=ALICE) -> dict:
    r = client.post("/internal/resolve-attachments", json={
        "tenant_id": tenant, "user_id": user, "items": items,
    })
    assert r.status_code == 200, r.text
    return r.json()


def names(body) -> list[str]:
    return [t["name"] for t in body["tools"]]


def dropped_kinds(body) -> list[str]:
    return [d["kind"] for d in body["dropped"]]


# ── 形状 ──────────────────────────────────────────────────────────────────

def test_tool_shape_matches_agent_config(client):
    """键名必须是 `schema_json` —— 引擎两边用同一个 ToolDef 解。

    这条看着笨（断言一个字符串字面量），但它是唯一能钉住这类 bug 的形状：
    json.Decode 对不认识的键静默丢弃，键名错了不会有任何运行时信号，
    症状是模型拿到一个空 schema 然后靠工具名猜参数。
    """
    tid = mk_tool(client)
    body = resolve(client, [{"kind": "tool", "ref_id": tid}])

    assert len(body["tools"]) == 1
    t = body["tools"][0]
    assert t["schema_json"] == SCHEMA
    assert "input_schema" not in t          # 老列名，不该复活
    assert t["execution_type"] == "api"     # 执行需要的字段都要在


def test_private_tool_gets_the_user_prefix(client):
    """私有工具下发带 my__ —— 跟 agent-config 那条一致。

    不加的话执行侧的 resolve_tool 在 owner_scope != USER 里找，404：
    挂上了但结构上调不通。
    """
    tid = mk_tool(client, scope=OWNER_SCOPE_USER, owner=ALICE, name="my_notes")
    assert names(resolve(client, [{"kind": "tool", "ref_id": tid}])) == ["my__my_notes"]


def test_platform_tool_keeps_bare_name(client):
    tid = mk_tool(client, name="web_search")
    assert names(resolve(client, [{"kind": "tool", "ref_id": tid}])) == ["web_search"]


# ── 归属过滤 ──────────────────────────────────────────────────────────────

def test_own_private_tool_resolves(client):
    tid = mk_tool(client, scope=OWNER_SCOPE_USER, owner=ALICE)
    body = resolve(client, [{"kind": "tool", "ref_id": tid}], user=ALICE)
    assert len(body["tools"]) == 1 and body["dropped"] == []


def test_another_users_private_tool_is_dropped(client):
    """陈旧或者伪造的挂载行不该变成越权通道。

    表上没有外键，也没人保证写进去的 id 属于这个人 —— 所以解析这一跳必须自己查。
    """
    tid = mk_tool(client, scope=OWNER_SCOPE_USER, owner=BOB)
    body = resolve(client, [{"kind": "tool", "ref_id": tid}], user=ALICE)
    assert body["tools"] == []
    assert dropped_kinds(body) == ["tool"]


def test_other_tenant_tool_is_dropped(client):
    tid = mk_tool(client, tenant=T2)
    body = resolve(client, [{"kind": "tool", "ref_id": tid}], tenant=T1)
    assert body["tools"] == []
    assert dropped_kinds(body) == ["tool"]


def test_global_platform_tool_resolves_across_tenants(client):
    """住在 default 租户的全局内置工具，任何租户都够得着。"""
    tid = mk_tool(client, tenant=DEFAULT_TENANT_ID, name="kb_search")
    assert len(resolve(client, [{"kind": "tool", "ref_id": tid}], tenant=T1)["tools"]) == 1


def test_disabled_tool_is_dropped(client):
    """停用的工具挂着也不下发 —— 跟 agent-config 的两级停用一致。"""
    tid = mk_tool(client, enabled=False)
    body = resolve(client, [{"kind": "tool", "ref_id": tid}])
    assert body["tools"] == [] and dropped_kinds(body) == ["tool"]


def test_deleted_tool_is_dropped_not_an_error(client):
    """指向一个已经不存在的 id。

    这是最常见的失效形态：用户挂了个租户共享工具，管理员把它删了。整个请求必须
    照常返回 200 —— 报错的话用户那段对话就打不开了。
    """
    body = resolve(client, [{"kind": "tool", "ref_id": 99999}])
    assert body["tools"] == []
    assert dropped_kinds(body) == ["tool"]


def test_dropped_entries_carry_a_reason(client):
    """`dropped` 不是个计数器，要能拿去打日志定位。

    没有 reason 的话，"工具无声消失"在任何地方都查不到 —— 而那正是这个字段存在的
    全部理由。
    """
    body = resolve(client, [{"kind": "tool", "ref_id": 99999}])
    d = body["dropped"][0]
    assert d["ref_id"] == 99999 and d["kind"] == "tool" and d["reason"]


def test_valid_and_invalid_mixed(client):
    """一半有效一半失效时，有效的照常返回。

    这是真实形态：挂了三样，其中一样被删了。要是实现成"有一个坏就整批丢"，用户会
    莫名其妙失去另外两个。
    """
    ok = mk_tool(client, name="ok_tool")
    gone = 99999
    body = resolve(client, [
        {"kind": "tool", "ref_id": ok},
        {"kind": "tool", "ref_id": gone},
    ])
    assert names(body) == ["ok_tool"]
    assert [d["ref_id"] for d in body["dropped"]] == [gone]


# ── 知识库 ────────────────────────────────────────────────────────────────

def test_public_kb_resolves(client):
    kid = mk_kb(client, visibility="public")
    body = resolve(client, [{"kind": "kb", "ref_id": kid}])
    assert body["kb_ids"] == [kid]


def test_own_private_kb_resolves(client):
    """自己的私有库自己当然能挂。"""
    kid = mk_kb(client, visibility="private", owner=ALICE)
    assert resolve(client, [{"kind": "kb", "ref_id": kid}], user=ALICE)["kb_ids"] == [kid]


def test_another_users_private_kb_is_dropped(client):
    kid = mk_kb(client, visibility="private", owner=BOB)
    body = resolve(client, [{"kind": "kb", "ref_id": kid}], user=ALICE)
    assert body["kb_ids"] == []
    assert dropped_kinds(body) == ["kb"]


def test_other_tenant_kb_is_dropped(client):
    kid = mk_kb(client, tenant=T2, visibility="public")
    body = resolve(client, [{"kind": "kb", "ref_id": kid}], tenant=T1)
    assert body["kb_ids"] == [] and dropped_kinds(body) == ["kb"]


# ── kind ──────────────────────────────────────────────────────────────────

def test_unknown_kind_is_dropped_not_an_error(client):
    """认不出的 kind 跳过，不是错误。

    `kind` 用 VARCHAR 而不是 ENUM，就是为了让"加一种新资源"不必先改所有读端
    （migrations/017 的说明）。老版本的 lea-config 碰到 mcp_server 该跳过，而不是
    让整个会话建不起来 —— 那会变成一个部署顺序依赖，正是 016 那轮收拾的病。
    """
    body = resolve(client, [{"kind": "mcp_server", "ref_id": 7}])
    assert body["tools"] == [] and body["kb_ids"] == []
    assert body["dropped"] == [{"kind": "mcp_server", "ref_id": 7, "reason": "unknown kind"}]


def test_tools_and_kbs_together(client):
    """混合挂载 —— 引擎一次调用要拿到全部。"""
    tid = mk_tool(client)
    kid = mk_kb(client, visibility="public")
    body = resolve(client, [
        {"kind": "tool", "ref_id": tid},
        {"kind": "kb", "ref_id": kid},
    ])
    assert names(body) == ["stock_lookup"]
    assert body["kb_ids"] == [kid]
    assert body["dropped"] == []


def test_empty_items_is_fine(client):
    """没挂任何东西是最常见的情况，不该是特例。"""
    body = resolve(client, [])
    assert body == {"tools": [], "kb_ids": [], "dropped": []}


def test_anonymous_user_gets_only_platform_tools(client):
    """没有 user_id 时 fail-closed：只给平台工具，不给任何私有的。

    这条对应 visible_to 里那个分支。写成"owner_user_id IS NULL"的话，平台工具和
    "没主人的行"会被混为一谈。
    """
    mine = mk_tool(client, scope=OWNER_SCOPE_USER, owner=ALICE, name="mine")
    shared = mk_tool(client, name="shared")
    body = resolve(client, [
        {"kind": "tool", "ref_id": mine},
        {"kind": "tool", "ref_id": shared},
    ], user="")
    assert names(body) == ["shared"]
    assert dropped_kinds(body) == ["tool"]
