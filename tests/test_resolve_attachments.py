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


def mk_skill(client, *, tenant=T1, scope=OWNER_SCOPE_PLATFORM, owner=None,
             enabled=True, name="ppt_maker", bundle="") -> int:
    from lea_config.models.skill import Skill

    db = client.Session()
    try:
        s = Skill(
            tenant_id=tenant, name=name, display_name="做 PPT",
            description="按模板生成演示文稿", system_prompt="Step 1: 调研…",
            enabled=enabled, owner_scope=scope, owner_user_id=owner,
            bundle_uri=bundle or None,
        )
        db.add(s)
        db.commit()
        return s.id
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
    # ⚠️ 挑一个**真的还不存在**的 kind。前两版分别用了 "skill" 和 "mcp_server"，
    # 各自在下一批做完时就过时了 —— 测试名说"未知"，用的却是已经认识的类型。
    body = resolve(client, [{"kind": "future_thing", "ref_id": 7}])
    assert body["tools"] == [] and body["kb_ids"] == [] and body["skills"] == []
    assert body["dropped"] == [{"kind": "future_thing", "ref_id": 7, "reason": "unknown kind"}]


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
    assert body == {"tools": [], "skills": [], "kb_ids": [], "dropped": []}


# ── skill ─────────────────────────────────────────────────────────────────
#
# 归属判据跟 tool 同一套三元组（Skill 也是 OwnerMixin），所以这一组盯的是
# **skill 特有**的三件事：形状要能被同一个 Go SkillDef 解、enabled 要过滤、
# 以及 models/skill.py::visible_to 跟 api/skills.py::_visible_skills 的分工
# （下发链路不认管理员，否则管理员的会话会挂上别人的私有 skill）。


def test_skill_shape_matches_agent_config(client):
    """形状必须跟 /internal/agent-config 的 skills 一致。

    引擎两边用**同一个** SkillDef 结构体去解。差一个键名就是静默失效 ——
    json.Decode 不报错，字段留零值，模型拿到一个没有正文的 skill。那正是
    schema_json 那个 bug 的形状。
    """
    sid = mk_skill(client, bundle="s3://b/ppt.zip")
    body = resolve(client, [{"kind": "skill", "ref_id": sid}])

    assert len(body["skills"]) == 1
    s = body["skills"][0]
    # Go SkillDef 读的这几个键必须都在
    for key in ("id", "name", "display_name", "description",
                "system_prompt", "bundle_uri"):
        assert key in s, f"缺键 {key} —— Go 侧会静默留零值"
    assert s["name"] == "ppt_maker"
    assert s["system_prompt"] == "Step 1: 调研…"
    assert s["bundle_uri"] == "s3://b/ppt.zip"


def test_skill_without_bundle_resolves(client):
    """纯 prompt 型 skill（没打包 reference）也要能挂。

    bundle_uri 为空是正常状态，不是残缺 —— ProvisionSessionSkills 会跳过它，
    引擎的 locationHeader 也会因为 Path 为空而整段跳过。
    """
    sid = mk_skill(client, bundle="")
    body = resolve(client, [{"kind": "skill", "ref_id": sid}])
    assert len(body["skills"]) == 1
    assert not body["skills"][0]["bundle_uri"]


def test_own_private_skill_resolves(client):
    sid = mk_skill(client, scope=OWNER_SCOPE_USER, owner=ALICE)
    body = resolve(client, [{"kind": "skill", "ref_id": sid}], user=ALICE)
    assert len(body["skills"]) == 1
    assert body["dropped"] == []


def test_another_users_private_skill_is_dropped(client):
    """bob 挂 alice 的私有 skill → 丢弃。

    比工具那条更要紧：skill 是一段**注入进 prompt 的正文**。漏过去的话 bob 的会话里
    会出现 alice 写的内容，而不只是多一个能调的名字。
    """
    sid = mk_skill(client, scope=OWNER_SCOPE_USER, owner=ALICE)
    body = resolve(client, [{"kind": "skill", "ref_id": sid}], user=BOB)
    assert body["skills"] == []
    assert body["dropped"][0]["kind"] == "skill"


def test_other_tenant_skill_is_dropped(client):
    sid = mk_skill(client, tenant=T2)
    body = resolve(client, [{"kind": "skill", "ref_id": sid}], tenant=T1)
    assert body["skills"] == []


def test_global_platform_skill_resolves_across_tenants(client):
    """住在 default 租户的平台市场 skill，任何租户都该看得见。

    这一层是 lea_authkit.scoped_filter 表达不了的那层（它最后一句是
    tenant_id == actor.tenant_id），也是 api/skills.py 要本地写一份的原因。
    """
    sid = mk_skill(client, tenant=DEFAULT_TENANT_ID)
    body = resolve(client, [{"kind": "skill", "ref_id": sid}], tenant=T1)
    assert len(body["skills"]) == 1


def test_disabled_skill_is_dropped(client):
    """Skill.enabled=False 是**整个 skill 停用**，挂载也必须过它。

    不过滤就是"停用了但还能挂上用" —— 而绑定链路上那两级停用开关正是迁移时掉的，
    修完不久。挂载是第三条路，同一个坑不能再踩。
    """
    sid = mk_skill(client, enabled=False)
    body = resolve(client, [{"kind": "skill", "ref_id": sid}])
    assert body["skills"] == []
    assert "disabled" in body["dropped"][0]["reason"]


def test_deleted_skill_is_dropped_not_an_error(client):
    """挂载行没有外键，skill 随时会被删。报错的话用户的老会话直接打不开。"""
    body = resolve(client, [{"kind": "skill", "ref_id": 99999}])
    assert body["skills"] == []
    assert body["dropped"][0]["ref_id"] == 99999


def test_anonymous_user_gets_only_platform_skills(client):
    """没有 user_id 时 fail-closed，跟工具那条一致。"""
    mine = mk_skill(client, scope=OWNER_SCOPE_USER, owner=ALICE, name="private_one")
    plat = mk_skill(client, name="public_one")
    body = resolve(client, [
        {"kind": "skill", "ref_id": mine},
        {"kind": "skill", "ref_id": plat},
    ], user="")
    assert [s["name"] for s in body["skills"]] == ["public_one"]


def test_all_three_kinds_together(client):
    """tool + skill + kb 一次挂全 —— 引擎一次调用要拿到三样。"""
    tid = mk_tool(client)
    sid = mk_skill(client)
    kid = mk_kb(client, visibility="public")
    body = resolve(client, [
        {"kind": "tool", "ref_id": tid},
        {"kind": "skill", "ref_id": sid},
        {"kind": "kb", "ref_id": kid},
    ])
    assert names(body) == ["stock_lookup"]
    assert [s["name"] for s in body["skills"]] == ["ppt_maker"]
    assert body["kb_ids"] == [kid]
    assert body["dropped"] == []


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


# ── mcp_server ────────────────────────────────────────────────────────────
#
# 挂一个 MCP server = 挂它同步出来的**全部**工具行。粒度是整个 server，跟主流做法
# 一致：MCP 的心智模型是"接一个服务"，而用户在挂之前并不知道那服务有哪几个工具
# （那要 sync 之后才知道）。
#
# 所以返回形状里**没有** mcp 这一类 —— 它们混在 tools 里回来，Go 侧一个合并分支都
# 不用加。


def mk_mcp(client, *, tenant=T1, scope=OWNER_SCOPE_PLATFORM, owner=None,
           enabled=True, name="notion") -> int:
    from lea_config.models.mcp_server import MCPServer

    db = client.Session()
    try:
        s = MCPServer(
            tenant_id=tenant, name=name, endpoint="https://notion.example/mcp",
            transport="streamable_http", enabled=enabled,
            owner_scope=scope, owner_user_id=owner,
        )
        db.add(s)
        db.commit()
        return s.id
    finally:
        db.close()


def mk_mcp_tool(client, server_id: int, bare: str, *, tenant=T1,
                scope=OWNER_SCOPE_PLATFORM, owner=None, enabled=True) -> int:
    """建一行 sync 出来的 MCP 工具。名字形状跟 admin_mcp.py 一致。"""
    from lea_config.models.tool import Tool

    db = client.Session()
    try:
        t = Tool(
            tenant_id=tenant, name=f"mcp_{server_id}_{bare}",
            display_name=f"[MCP] {bare}", description=f"{bare} desc",
            execution_type="mcp", schema_json=SCHEMA, enabled=enabled,
            mcp_server_id=server_id, owner_scope=scope, owner_user_id=owner,
        )
        db.add(t)
        db.commit()
        return t.id
    finally:
        db.close()


def test_mcp_server_resolves_to_all_its_tools(client):
    """挂一个 server → 它的每个工具都进来。"""
    sid = mk_mcp(client)
    for bare in ("search", "create", "update"):
        mk_mcp_tool(client, sid, bare)

    body = resolve(client, [{"kind": "mcp_server", "ref_id": sid}])
    assert sorted(names(body)) == [
        f"mcp_{sid}_create", f"mcp_{sid}_search", f"mcp_{sid}_update",
    ]
    assert body["dropped"] == []


def test_private_mcp_tools_get_the_user_prefix(client):
    """alice 私有 server 的工具带 my__ 前缀。

    少了它 resolve_tool 会在 owner_scope != USER 里找 → 404。两层前缀各自独立：
    my__ 是归属命名空间，mcp_{id}_ 是 server 消歧。
    """
    sid = mk_mcp(client, scope=OWNER_SCOPE_USER, owner=ALICE)
    mk_mcp_tool(client, sid, "search", scope=OWNER_SCOPE_USER, owner=ALICE)

    body = resolve(client, [{"kind": "mcp_server", "ref_id": sid}], user=ALICE)
    assert names(body) == [f"my__mcp_{sid}_search"]


def test_another_users_private_mcp_server_is_dropped(client):
    sid = mk_mcp(client, scope=OWNER_SCOPE_USER, owner=ALICE)
    mk_mcp_tool(client, sid, "search", scope=OWNER_SCOPE_USER, owner=ALICE)

    body = resolve(client, [{"kind": "mcp_server", "ref_id": sid}], user=BOB)
    assert body["tools"] == []
    assert body["dropped"][0]["kind"] == "mcp_server"


def test_disabled_mcp_server_is_dropped(client):
    """server 停用 → 它的工具一个都不该进来。

    停用 server 的语义就是"这个连接不能用了"，工具行可能还是 enabled 的（sync 只在
    远端下线时才删它们），所以必须在 server 这一层拦。
    """
    sid = mk_mcp(client, enabled=False)
    mk_mcp_tool(client, sid, "search")

    body = resolve(client, [{"kind": "mcp_server", "ref_id": sid}])
    assert body["tools"] == []
    assert "disabled" in body["dropped"][0]["reason"]


def test_individually_disabled_mcp_tool_is_skipped(client):
    """server 能用但某个工具被单独停用 → 只少那一个。

    这是"归属和启用都按工具行判"的好处：不需要为这种情况写特例。
    """
    sid = mk_mcp(client)
    mk_mcp_tool(client, sid, "search")
    mk_mcp_tool(client, sid, "danger", enabled=False)

    body = resolve(client, [{"kind": "mcp_server", "ref_id": sid}])
    assert names(body) == [f"mcp_{sid}_search"]


def test_unsynced_mcp_server_reports_a_reason(client):
    """server 可见但没有工具行 —— 几乎总是"还没 sync 过"。

    不是错误（会话照常建），但**必须报出来**：否则用户挂了个 server、模型什么都没多，
    而任何地方都查不到为什么。这类"静默少了东西"是挂载这套里最难查的一类。
    """
    sid = mk_mcp(client)
    body = resolve(client, [{"kind": "mcp_server", "ref_id": sid}])
    assert body["tools"] == []
    assert body["dropped"][0]["kind"] == "mcp_server"
    assert "sync" in body["dropped"][0]["reason"]


def test_deleted_mcp_server_is_dropped_not_an_error(client):
    body = resolve(client, [{"kind": "mcp_server", "ref_id": 99999}])
    assert body["tools"] == []
    assert body["dropped"][0]["ref_id"] == 99999


def test_other_tenant_mcp_server_is_dropped(client):
    sid = mk_mcp(client, tenant=T2)
    mk_mcp_tool(client, sid, "search", tenant=T2)
    body = resolve(client, [{"kind": "mcp_server", "ref_id": sid}], tenant=T1)
    assert body["tools"] == []


def test_mcp_tools_from_two_servers_do_not_collide(client):
    """两个 server 各有一个 search —— 名字带 server_id 所以不撞。

    这是 mcp_{server_id}_ 前缀存在的理由（GitHub 和 Linear 都有 create_issue）。
    """
    a = mk_mcp(client, name="notion")
    b = mk_mcp(client, name="linear")
    mk_mcp_tool(client, a, "search")
    mk_mcp_tool(client, b, "search")

    body = resolve(client, [
        {"kind": "mcp_server", "ref_id": a},
        {"kind": "mcp_server", "ref_id": b},
    ])
    assert sorted(names(body)) == sorted([f"mcp_{a}_search", f"mcp_{b}_search"])


def test_mcp_server_alongside_other_kinds(client):
    """四种 kind 一次挂全。"""
    tid = mk_tool(client)
    skid = mk_skill(client)
    kid = mk_kb(client, visibility="public")
    sid = mk_mcp(client)
    mk_mcp_tool(client, sid, "search")

    body = resolve(client, [
        {"kind": "tool", "ref_id": tid},
        {"kind": "skill", "ref_id": skid},
        {"kind": "kb", "ref_id": kid},
        {"kind": "mcp_server", "ref_id": sid},
    ])
    # MCP 的工具跟普通工具混在同一个数组里 —— 这是设计，不是巧合
    assert sorted(names(body)) == sorted(["stock_lookup", f"mcp_{sid}_search"])
    assert [s["name"] for s in body["skills"]] == ["ppt_maker"]
    assert body["kb_ids"] == [kid]
    assert body["dropped"] == []


def test_tool_row_ownership_wins_when_it_lags_behind_the_server(client):
    """server 已经上架成 platform，工具行还挂在原主人名下 → bob 拿不到它们。

    ## 为什么这条必须存在

    归属**按工具行判，不按 server 判**。两者今天一致（sync 让工具行继承 server 的
    归属），但它们可以短暂不一致 —— server 归属改了、还没重 sync 的那个窗口。

    这条是变异测试逼出来的：去掉工具行那层归属过滤，其余 9 条 mcp 用例**全绿**，
    因为 server 那一层已经挡住了跨用户和跨租户。也就是说我注释里声称的那个理由
    当时没有任何测试覆盖，只是一句好听的话。

    判据方向：真正被执行的是工具行，所以宽的那一层（server 可见）不能放出严的那一层
    （工具行归属）不允许的东西。
    """
    sid = mk_mcp(client)  # server 是 platform 级，bob 看得见
    # 工具行还是 alice 的私有（sync 还没跑）
    mk_mcp_tool(client, sid, "search", scope=OWNER_SCOPE_USER, owner=ALICE)

    body = resolve(client, [{"kind": "mcp_server", "ref_id": sid}], user=BOB)
    assert body["tools"] == [], "bob 拿到了 alice 名下的工具行"


def test_tool_row_from_another_tenant_is_not_returned(client):
    """server 在 T1，工具行却是 T2 的 —— 不该返回。

    这种行只可能来自脏数据或迁移事故，但 mcp_server_id 上没有租户约束，所以查询必须
    自己带上。跟 tools 表当年"按名字查不带 tenant"是同一类。
    """
    sid = mk_mcp(client, tenant=T1)
    mk_mcp_tool(client, sid, "search", tenant=T2)

    body = resolve(client, [{"kind": "mcp_server", "ref_id": sid}], tenant=T1)
    assert body["tools"] == []
