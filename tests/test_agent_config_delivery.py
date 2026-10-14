"""`/internal/agent-config` 的工具下发契约。

这是工具从库走到模型面前的**唯一**一跳（引擎只调这一个接口拿清单，lea-tools 的
`/session/capabilities` 没有任何 Go 调用方）。所以这一跳错了，症状不会是接口报错，
而是模型手里的工具变了形。

盯三条，全都是**静默**失效的：

    键名      下发 schema_json；Go 的 ToolDef 读的就是这个键。错一个词 → 模型看到
              的工具没有参数，而不是报错
    绑定开关  b.enabled=False 的绑定不下发
    工具开关  Tool.enabled=False 的工具不下发

后两条不是从没有过，是**迁移时掉的** —— 老的 lea-server 那版在 JOIN 里带着
`AgentToolBinding.enabled` 和 `Tool.is_enabled` 两个过滤，搬到 lea-config 时只剩
`filter_by(agent_id)`。同一次迁移把 `input_schema` 改名成 `schema_json`，Go 没跟。

⚠️ 这个文件跟 `test_tools_api.py` 是互补的，别以为重复了：那边测的是**写**进去的
列名对不对（lea-config → 库），这边测的是**发**出来的键名对不对（库 → 引擎）。
写对了发错了，一样是模型拿不到 schema —— 而那一半以前没有任何测试。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from lea_config.database import OWNER_SCOPE_PLATFORM, OWNER_SCOPE_USER, Base, get_db
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

TENANT = "delivery-t1"

# 一个**有必填参数**的 schema。空 schema 测不出这个 bug —— 键名错了拿到的也是空的，
# 两种情况长得一模一样。
WEATHER_SCHEMA = {
    "type": "object",
    "required": ["latitude", "longitude"],
    "properties": {
        "latitude": {"type": "number", "description": "纬度"},
        "longitude": {"type": "number", "description": "经度"},
    },
}


@pytest.fixture
def client():
    import lea_config.models  # noqa: F401 — 让 Base.metadata 收齐外键指向的表
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


def seed(client, *, tool_enabled=True, binding_enabled=True) -> int:
    """一个 agent + 一个 api 工具 + 一条绑定。返回 agent_id。"""
    from lea_config.models.agent import Agent
    from lea_config.models.binding import AgentToolBinding
    from lea_config.models.tool import Tool

    db = client.Session()
    try:
        agent = Agent(tenant_id=TENANT, name="a", model="", owner_scope=OWNER_SCOPE_PLATFORM)
        db.add(agent)
        db.flush()

        tool = Tool(
            tenant_id=TENANT,
            name="get_weather",
            description="查天气",
            execution_type="api",
            schema_json=WEATHER_SCHEMA,
            endpoint="https://api.open-meteo.com/v1/forecast",
            enabled=tool_enabled,
            owner_scope=OWNER_SCOPE_PLATFORM,
        )
        db.add(tool)
        db.flush()

        db.add(AgentToolBinding(agent_id=agent.id, tool_id=tool.id, enabled=binding_enabled))
        db.commit()
        return agent.id
    finally:
        db.close()


def fetch(client, agent_id: int) -> list[dict]:
    r = client.get(f"/internal/agent-config/{agent_id}?tenant_id={TENANT}")
    assert r.status_code == 200, r.text
    return r.json()["tools"]


# ── 键名 ──────────────────────────────────────────────────────────────────

def test_schema_is_delivered_under_the_key_go_reads(client):
    """键必须是 `schema_json` —— Go 的 ToolDef.InputSchema 标的就是它。

    这条看着很笨（断言一个字符串字面量），但它是唯一能钉住这个 bug 的形状：
    json.Decode 对不认识的键**静默丢弃**，所以键名错了不会有任何运行时信号。
    """
    tools = fetch(client, seed(client))
    assert len(tools) == 1
    assert "schema_json" in tools[0]
    assert tools[0]["schema_json"] == WEATHER_SCHEMA


def test_required_params_survive_the_hop(client):
    """光有键还不够 —— 参数本身要完整到模型能把值传出来。

    这条是从症状那头写的：get_weather 的 required 是经纬度，丢了它们模型只会调一次
    空参数，网关那边 jinja 把 {{ latitude }} 渲染成空串，上游返 400。
    """
    schema = fetch(client, seed(client))[0]["schema_json"]
    assert schema["required"] == ["latitude", "longitude"]
    assert set(schema["properties"]) == {"latitude", "longitude"}


def test_dead_key_is_not_resurrected(client):
    """`input_schema` 是老 lea-server 的列名，不该再出现。

    两个键都发看起来"更安全"，实际是重造 migrations/006 刚删掉的那组重复列 ——
    这次是在下发契约上。权威词汇只有一套。
    """
    assert "input_schema" not in fetch(client, seed(client))[0]


def test_binding_enabled_flag_is_not_delivered(client):
    """不发标志位，因为没有消费者会看它。

    以前发 binding_enabled，而 Go 的 ToolDef 没有这个字段 —— 于是"发了标志位"看起来
    像是处理了绑定开关，实际什么都没发生。skills 那段就是这么漏了一轮。
    """
    assert "binding_enabled" not in fetch(client, seed(client))[0]


# ── 两级停用 ──────────────────────────────────────────────────────────────

def test_disabled_binding_is_not_delivered(client):
    """这个 agent 不再用这个工具。"""
    assert fetch(client, seed(client, binding_enabled=False)) == []


def test_disabled_tool_is_not_delivered(client):
    """工具整个停用 —— 所有绑它的 agent 一起断，不需要逐个解绑。"""
    assert fetch(client, seed(client, tool_enabled=False)) == []


def test_enabled_tool_with_enabled_binding_is_delivered(client):
    """正向那一格。

    没有它，上面两条全返空数组也会绿 —— 那种"过滤写反了"的测试是自洽的废纸。
    """
    tools = fetch(client, seed(client))
    assert [t["name"] for t in tools] == ["get_weather"]


# ── 命名空间：下发的是给模型看的名字 ──────────────────────────────────────
#
# 执行侧的 resolve_tool 是严格解析的：`my__weather` 只在用户私有里找，裸的
# `weather` 只在平台/共享里找，不做裸名兜底。所以下发侧必须发带前缀的名字，
# 否则用户私有工具**结构上调不通** —— 模型调裸名，执行侧在 owner_scope != USER
# 里找，404。
#
# 这一跳以前是漏的。没显形是因为库里 0 行 user 工具。

def seed_private(client, owner: str = "alice") -> int:
    from lea_config.models.agent import Agent
    from lea_config.models.binding import AgentToolBinding
    from lea_config.models.tool import Tool

    db = client.Session()
    try:
        agent = Agent(tenant_id=TENANT, name="a", model="", owner_scope=OWNER_SCOPE_PLATFORM)
        db.add(agent)
        db.flush()
        tool = Tool(
            tenant_id=TENANT, name="weather", execution_type="api",
            schema_json=WEATHER_SCHEMA, enabled=True,
            owner_scope=OWNER_SCOPE_USER, owner_user_id=owner,
        )
        db.add(tool)
        db.flush()
        db.add(AgentToolBinding(agent_id=agent.id, tool_id=tool.id, enabled=True))
        db.commit()
        return agent.id
    finally:
        db.close()


def test_private_tool_is_delivered_with_the_user_prefix(client):
    """用户私有工具下发时带 my__ —— 这正是 resolve_tool 期待的形状。"""
    tools = fetch(client, seed_private(client))
    assert [t["name"] for t in tools] == ["my__weather"]


def test_platform_tool_keeps_its_bare_name(client):
    """平台/共享工具不加前缀 —— 它们是平台自己的命名空间。

    加了的话内置工具的名字会变，而 Go 侧 BuildPlatformTools 的 builtins map 是按
    裸名匹配的：kb_search 变成 my__kb_search 就会被当成 DynamicTool 走网关，
    而它的实现在 Go 代码里。
    """
    tools = fetch(client, seed(client))
    assert [t["name"] for t in tools] == ["get_weather"]


def test_prefix_does_not_leak_into_the_stored_name(client):
    """库里那列还是裸名 —— 绑定、权限、审计都按它和 id 走。

    前缀只是**展示层**。写回库的话 migrations/007 的用户内唯一约束就白做了：
    alice 的 my__weather 和 bob 的 my__weather 又成了两个不同的字符串，
    而那正是那个约束想统一的东西。
    """
    from lea_config.models.tool import Tool

    seed_private(client)
    db = client.Session()
    try:
        assert db.query(Tool).filter_by(owner_user_id="alice").one().name == "weather"
    finally:
        db.close()
