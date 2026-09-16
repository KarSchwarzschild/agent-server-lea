"""`POST /agents/{id}/share` 的发布门。

agent 是个**容器**：它自己没有内容，内容全在它绑的 tool / skill / kb 里。共享容器
而里面装着私有资源，等于绕开那些资源自己的可见性。

判据一句话：**agent 的可见性不能高于它绑的任何一样东西。**

## 硬拦和软提示的分界

不是所有"私有"都同样危险，分界在**泄漏的是定义还是数据**：

    tool / skill / 团队成员   硬拦（409）
        定义本身就是要下发给模型的：名字、描述、完整参数 schema 直接进别人的
        对话上下文。执行会被 resolve_tool 挡住，但那时内部结构已经送出去了。

    knowledge_base           只提示，不拦
        下发的只有 kb_ids，内容要再经过 kb_search 才拿得到，那一跳是 lea-rag
        授权的。所以是"别人可能搜到"，不是"共享即泄漏"。留给用户判断。

这个分界是产品决定，不是技术推论 —— 所以下面专门有一条用例钉住"kb 不拦"，
免得下一个人看到 warnings 觉得"这不也是私有吗"顺手改成 blocker。

## 为什么这条链是真的

私有工具挂共享 agent 这个形状在真链路上跑出来过（scripts/my_prefix_e2e.py）：
另一个用户的工具清单里确实出现了 my__stock_lookup。执行被 403 挡住了，
下发没挡住。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from lea_config.database import (
    OWNER_SCOPE_PLATFORM,
    OWNER_SCOPE_USER,
    Base,
    get_db,
)

T1 = "share-t1"
OWNER = "alice"


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


def h(user: str = OWNER, tenant: str = T1) -> dict:
    return {"X-User-ID": user, "X-Tenant-ID": tenant}


def mk_agent(client, *, owner: str = OWNER, scope: str = OWNER_SCOPE_USER,
             name: str = "a", created_by: str | None = "__same__") -> int:
    from lea_config.models.agent import Agent

    db = client.Session()
    try:
        a = Agent(
            tenant_id=T1, name=name, model="",
            owner_scope=scope,
            owner_user_id=owner if scope == OWNER_SCOPE_USER else None,
            # created_by 不能省：_require_mine 校验的是**这一列**，不是 owner_user_id。
            created_by=owner if created_by == "__same__" else created_by,
        )
        db.add(a)
        db.commit()
        return a.id
    finally:
        db.close()


def bind_tool(client, agent_id: int, *, scope: str, owner: str | None = None,
              name: str = "tl") -> int:
    from lea_config.models.binding import AgentToolBinding
    from lea_config.models.tool import Tool

    db = client.Session()
    try:
        t = Tool(
            tenant_id=T1, name=name, execution_type="api",
            schema_json={"type": "object", "properties": {"secret_field": {"type": "string"}}},
            enabled=True, owner_scope=scope, owner_user_id=owner,
        )
        db.add(t)
        db.flush()
        db.add(AgentToolBinding(agent_id=agent_id, tool_id=t.id, enabled=True))
        db.commit()
        return t.id
    finally:
        db.close()


def bind_mcp(client, agent_id: int, *, scope: str, owner: str | None = None,
             name: str = "mcp1") -> int:
    from lea_config.models.binding import AgentMcpBinding
    from lea_config.models.mcp_server import MCPServer

    db = client.Session()
    try:
        s = MCPServer(tenant_id=T1, name=name, enabled=True,
                      owner_scope=scope, owner_user_id=owner)
        db.add(s)
        db.flush()
        db.add(AgentMcpBinding(agent_id=agent_id, mcp_server_id=s.id, enabled=True))
        db.commit()
        return s.id
    finally:
        db.close()


def bind_skill(client, agent_id: int, *, scope: str, owner: str | None = None,
               name: str = "sk") -> int:
    from lea_config.models.binding import AgentSkillBinding
    from lea_config.models.skill import Skill

    db = client.Session()
    try:
        s = Skill(tenant_id=T1, name=name, description="", enabled=True,
                  category="general", owner_scope=scope, owner_user_id=owner)
        db.add(s)
        db.flush()
        db.add(AgentSkillBinding(agent_id=agent_id, skill_id=s.id, enabled=True))
        db.commit()
        return s.id
    finally:
        db.close()


def bind_kb(client, agent_id: int, *, visibility: str, owner: str = OWNER,
            name: str = "kb") -> int:
    from lea_config.models.binding import AgentKbBinding
    from lea_config.models.knowledge import KnowledgeBase

    db = client.Session()
    try:
        k = KnowledgeBase(tenant_id=T1, name=name, visibility=visibility, owner=owner)
        db.add(k)
        db.flush()
        db.add(AgentKbBinding(agent_id=agent_id, kb_id=k.id, enabled=True))
        db.commit()
        return k.id
    finally:
        db.close()


def add_team_member(client, leader_id: int, member_id: int) -> None:
    from lea_config.models.team import AgentTeam, AgentTeamMember

    db = client.Session()
    try:
        team = AgentTeam(agent_id=leader_id, strategy="handoff")
        db.add(team)
        db.flush()
        db.add(AgentTeamMember(team_id=team.id, member_agent_id=member_id, role="worker"))
        db.commit()
    finally:
        db.close()


def scope_of(client, agent_id: int) -> str:
    from lea_config.models.agent import Agent

    db = client.Session()
    try:
        return db.query(Agent).get(agent_id).owner_scope
    finally:
        db.close()


# ── 干净的 agent 能共享 ────────────────────────────────────────────────────

def test_agent_with_only_shared_resources_can_be_shared(client):
    """正向那一格。没有它，下面所有"被拦住"的用例靠"永远拦"也能绿。"""
    a = mk_agent(client)
    bind_tool(client, a, scope=OWNER_SCOPE_PLATFORM)
    bind_skill(client, a, scope=OWNER_SCOPE_PLATFORM)

    r = client.post(f"/agents/{a}/share", headers=h())
    assert r.status_code == 200, r.text
    assert scope_of(client, a) == OWNER_SCOPE_PLATFORM


def test_shared_agent_drops_its_owner(client):
    """升到 platform 之后 owner_user_id 必须置空。

    留着的话归属编码就自相矛盾了：(t1, platform, alice) 不属于三段式里的任何一档，
    而 visible_tools / scoped_filter 那些查询是按 owner_scope 分支的 —— 一个
    scope=platform 但带 owner 的行会被当成共享的，同时又在"我的"列表里出现。
    """
    from lea_config.models.agent import Agent

    a = mk_agent(client)
    client.post(f"/agents/{a}/share", headers=h())

    db = client.Session()
    try:
        row = db.query(Agent).get(a)
        assert row.owner_scope == OWNER_SCOPE_PLATFORM
        assert row.owner_user_id is None
    finally:
        db.close()


def test_share_does_not_make_it_a_system_agent(client):
    """共享 ≠ 系统内置。

    顺手把 is_system 也置上的话，用户就再也改不了、删不了自己建的 agent 了
    （_require_mine 对 is_system 直接 403）。
    """
    from lea_config.models.agent import Agent

    a = mk_agent(client)
    client.post(f"/agents/{a}/share", headers=h())

    db = client.Session()
    try:
        assert db.query(Agent).get(a).is_system in (False, 0, None)
    finally:
        db.close()


# ── 硬拦 ──────────────────────────────────────────────────────────────────

def test_private_tool_blocks_share(client):
    a = mk_agent(client)
    bind_tool(client, a, scope=OWNER_SCOPE_USER, owner=OWNER)

    r = client.post(f"/agents/{a}/share", headers=h())
    assert r.status_code == 409, r.text
    kinds = [b["kind"] for b in r.json()["detail"]["blockers"]]
    assert kinds == ["tool"]
    # 没共享成，归属不能被改坏
    assert scope_of(client, a) == OWNER_SCOPE_USER


def test_private_mcp_server_blocks_share(client):
    """私有 MCP server 硬拦 —— 整个 server 绑上去会把它同步出的全部工具下发，
    server 私有而 agent 共享就是把私有工具的名字/描述/schema 泄给所有使用者。"""
    a = mk_agent(client)
    bind_mcp(client, a, scope=OWNER_SCOPE_USER, owner=OWNER)

    r = client.post(f"/agents/{a}/share", headers=h())
    assert r.status_code == 409, r.text
    assert [b["kind"] for b in r.json()["detail"]["blockers"]] == ["mcp_server"]
    assert scope_of(client, a) == OWNER_SCOPE_USER  # 没共享成，归属没被改坏


def test_shared_mcp_server_does_not_block(client):
    """server 已经共享到租户就不该拦 —— 否则装了 MCP 的专家永远共享不出去。"""
    a = mk_agent(client)
    bind_mcp(client, a, scope=OWNER_SCOPE_PLATFORM, owner=None)

    assert client.post(f"/agents/{a}/share", headers=h()).status_code == 200


def test_private_skill_blocks_share(client):
    a = mk_agent(client)
    bind_skill(client, a, scope=OWNER_SCOPE_USER, owner=OWNER)

    r = client.post(f"/agents/{a}/share", headers=h())
    assert r.status_code == 409
    assert [b["kind"] for b in r.json()["detail"]["blockers"]] == ["skill"]


def test_private_team_member_blocks_share(client):
    """容器套容器 —— 团队成员那条支路不堵，前面几条全白做。

    引擎会用成员的 agent_id 再打一次 /internal/agent-config
    （runtime/expert_assembly.go:104），成员绑的私有工具照样下发。
    """
    leader = mk_agent(client, name="leader")
    member = mk_agent(client, name="member", owner="bob")
    add_team_member(client, leader, member)

    r = client.post(f"/agents/{leader}/share", headers=h())
    assert r.status_code == 409
    blockers = r.json()["detail"]["blockers"]
    assert [b["kind"] for b in blockers] == ["team_member"]
    assert blockers[0]["id"] == member


def test_shared_team_member_does_not_block(client):
    """成员已经是共享的就不该拦 —— 否则专家团永远共享不出去。"""
    leader = mk_agent(client, name="leader")
    member = mk_agent(client, name="member", scope=OWNER_SCOPE_PLATFORM, owner="x")
    add_team_member(client, leader, member)

    assert client.post(f"/agents/{leader}/share", headers=h()).status_code == 200


def test_all_blockers_are_listed_not_just_the_first(client):
    """一次给全清单。

    碰到第一个就返回的话，绑了十样东西的 agent 要点十次才知道全部要改什么 ——
    每次还得重新读一遍报错。这是这个接口存在的主要价值。
    """
    a = mk_agent(client)
    bind_tool(client, a, scope=OWNER_SCOPE_USER, owner=OWNER, name="t1")
    bind_tool(client, a, scope=OWNER_SCOPE_USER, owner=OWNER, name="t2")
    bind_skill(client, a, scope=OWNER_SCOPE_USER, owner=OWNER, name="s1")

    r = client.post(f"/agents/{a}/share", headers=h())
    assert r.status_code == 409
    blockers = r.json()["detail"]["blockers"]
    assert sorted(b["kind"] for b in blockers) == ["skill", "tool", "tool"]
    # 每一条都要能定位到具体是哪一行，光说"有工具没共享"没用
    assert all(b["id"] and b["name"] and b["reason"] for b in blockers)


# ── 软提示：知识库不拦 ────────────────────────────────────────────────────

def test_private_kb_warns_but_does_not_block(client):
    """**产品决定**：未公开的知识库只提示，不挡共享。

    它跟 tool / skill 不是同一类问题：

        tool / skill   安全问题 —— 定义直接进别人的上下文，泄漏已经发生
        kb             可用性问题 —— 下发的只有 kb_ids，别人用这个 agent 时
                       可能检索不到，于是同一个 agent 对 owner 和对别人表现不同

    提示的目的是告诉作者"你共享出去的这个 agent 对别人是残的"，而不是拦住他。
    他完全可能明知故犯（就想共享一个骨架，让别人自己接知识库）。

    这一条钉的是"**不要**把它改成 blocker"。下一个人看到 warnings 里躺着一个
    private kb，很容易觉得"这不也是私有吗"顺手升级 —— 那会把一个刻意的产品选择
    当成漏网之鱼修掉。
    """
    a = mk_agent(client)
    bind_kb(client, a, visibility="private")

    r = client.post(f"/agents/{a}/share", headers=h())
    assert r.status_code == 200, r.text
    assert scope_of(client, a) == OWNER_SCOPE_PLATFORM

    warnings = r.json()["warnings"]
    assert [w["kind"] for w in warnings] == ["kb"]
    # 文案讲的是**可用性**（别人可能用不了），不是泄漏（别人可能看到）。
    # 第一版写反了，提示成了"别人可能搜到你的内容" —— 那会把一个"你的 agent
    # 对别人不完整"的提醒，变成一句吓人的隐私警告，用户的反应完全不同。
    assert "检索不到" in warnings[0]["reason"]


def test_warnings_survive_a_successful_share(client):
    """共享成功了，软提示也要返回。

    只在 409 里带 warnings 的话，它就只在失败时出现 —— 而那时用户满脑子是
    blockers，根本不会看。真正需要看到"你有个私有知识库在里面"的时刻是共享成功那一刻。
    """
    a = mk_agent(client)
    bind_tool(client, a, scope=OWNER_SCOPE_PLATFORM)
    bind_kb(client, a, visibility="private")

    r = client.post(f"/agents/{a}/share", headers=h())
    assert r.status_code == 200
    assert len(r.json()["warnings"]) == 1


def test_public_kb_does_not_warn(client):
    a = mk_agent(client)
    bind_kb(client, a, visibility="public")

    r = client.post(f"/agents/{a}/share", headers=h())
    assert r.status_code == 200
    assert r.json()["warnings"] == []


# ── 预检 ──────────────────────────────────────────────────────────────────

def test_share_check_reports_without_changing_anything(client):
    """预检是只读的 —— 前端要能在按钮旁边先亮清单。"""
    a = mk_agent(client)
    bind_tool(client, a, scope=OWNER_SCOPE_USER, owner=OWNER)

    r = client.get(f"/agents/{a}/share-check", headers=h())
    assert r.status_code == 200
    body = r.json()
    assert body["can_share"] is False
    assert body["already_shared"] is False
    assert [b["kind"] for b in body["blockers"]] == ["tool"]
    # 只读：调完之后归属没变
    assert scope_of(client, a) == OWNER_SCOPE_USER


def test_share_check_on_a_clean_agent(client):
    a = mk_agent(client)
    bind_tool(client, a, scope=OWNER_SCOPE_PLATFORM)

    body = client.get(f"/agents/{a}/share-check", headers=h()).json()
    assert body["can_share"] is True
    assert body["blockers"] == []


# ── 幂等与撤回 ────────────────────────────────────────────────────────────

def test_sharing_twice_is_idempotent(client):
    """重复调用无害 —— 前端重试和双击都会走到这里。"""
    a = mk_agent(client)
    assert client.post(f"/agents/{a}/share", headers=h()).status_code == 200
    assert client.post(f"/agents/{a}/share", headers=h()).status_code == 200
    assert scope_of(client, a) == OWNER_SCOPE_PLATFORM


def test_unshare_returns_it_to_the_creator(client):
    """共享要有回退路径，否则它是一道单向门，用户会不敢点。"""
    from lea_config.models.agent import Agent

    a = mk_agent(client)
    client.post(f"/agents/{a}/share", headers=h())

    r = client.post(f"/agents/{a}/unshare", headers=h())
    assert r.status_code == 200, r.text

    db = client.Session()
    try:
        row = db.query(Agent).get(a)
        assert row.owner_scope == OWNER_SCOPE_USER
        assert row.owner_user_id == OWNER  # 还给原作者，不是给当前操作者
    finally:
        db.close()


def test_unshare_refuses_when_there_is_no_creator(client):
    """种子数据不能撤回。

    库里 agent 1/3/4/5/6 的 created_by 是 NULL。撤回等于把平台预置的 agent
    塞进当前操作者的私人名下 —— 一个管理员点一下，全租户就少一个公共 agent。

    ⚠️ 这条**必须用管理员身份**测，不能用普通用户。普通用户走不到这道门：
    `_require_mine` 比它早一步，看到 `created_by != actor.user_id` 直接 403
    （created_by 是 NULL 时任何人都不等于它）。也就是说这道 400 的门**只对
    管理员可达** —— 而管理员正是唯一能造成这个后果的角色，因为 _require_mine
    对管理员整个跳过。第一版用普通用户写，拿到 403 还以为是门生效了。
    """
    a = mk_agent(client, scope=OWNER_SCOPE_PLATFORM, created_by=None)

    admin = {"X-User-ID": "root", "X-Tenant-ID": T1, "X-User-Role": "admin"}
    r = client.post(f"/agents/{a}/unshare", headers=admin)
    assert r.status_code == 400, r.text
    assert scope_of(client, a) == OWNER_SCOPE_PLATFORM


def test_unshare_by_admin_returns_it_to_the_original_creator(client):
    """管理员撤回别人共享的 agent，要还给**原作者**，不是落到管理员自己名下。

    跟上一条是一对：那条管"没有原作者时拒绝"，这条管"有原作者时给对的人"。
    少了这条，把 `owner_user_id = agent.created_by` 写成 `= actor.user_id`
    照样能让上一条通过。
    """
    from lea_config.models.agent import Agent

    a = mk_agent(client, owner=OWNER)          # created_by = alice
    client.post(f"/agents/{a}/share", headers=h())

    admin = {"X-User-ID": "root", "X-Tenant-ID": T1, "X-User-Role": "admin"}
    assert client.post(f"/agents/{a}/unshare", headers=admin).status_code == 200

    db = client.Session()
    try:
        assert db.query(Agent).get(a).owner_user_id == OWNER  # 不是 root
    finally:
        db.close()
