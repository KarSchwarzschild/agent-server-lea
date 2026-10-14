"""provider 的租户可见性：本租户 + default，且两个消费者判据一致。

── 这个文件钉的是什么 ────────────────────────────────────────────────

`llm_providers` 原先写的是 `tenant_id` **严格相等**，而本仓库其他所有资源都是
"本租户 + default"两者都看（models/skill.py:81、models/tool.py:164、
models/mcp_server.py:158、models/agent.py:72）。provider 是唯一的例外。

那个例外的后果是一对看不出同源的故障。provider 只在 default 下建了一行，于是
非 default 租户：

    /llm-models        → 空列表，前端模型选择器什么都没有，换不了模型
    internal.py 匹配   → 匹配不到，退到 _tenant_llm_fallback（只给 base_url
                         和 api_key，**不给 model_context_window**），于是
                         窗口按 lea-agent 的保守兜底 32k 算 —— 而中转上的
                         模型实际有 200k+

现象是"选择器是空的"加"对话上下文莫名其妙短"。实测 wjtest / test2 / seed-acme
三个租户全中。

── 为什么要有"两边一致"那组用例 ──────────────────────────────────────

llm_models.py 的 docstring 早就写明了"筛选判据必须跟 internal.py 一模一样"，
但两边各写了一遍条件 —— 于是也一起错了同一处。注释约束不住同形，所以判据被抽进
models/llm_provider.py，这里再用测试把"两边必须同意"钉死。

分叉的方向都有害：
    列表宽、匹配窄  用户选中一个运行时选不中的名字，请求悄悄走默认 provider
    列表窄、匹配宽  能用的模型选不着，没有任何提示

── 放宽过滤的风险面 ──────────────────────────────────────────────────

把 `==` 改成 `in_([tenant, default])` 是**放宽**，所以必须同时证明没有顺带开出
跨租户泄露：t1 不能看到 t2 的 provider。provider 行里装着租户的 LLM 凭据
（api_key），泄露的代价比"看到一个模型名"大得多。test_cross_tenant_* 那两条就是
为这件事写的。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from lea_config.database import DEFAULT_TENANT_ID, Base, get_db
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

T1, T2 = "scope-t1", "scope-t2"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("LEA_INTERNAL_TOKEN", "tok")
    monkeypatch.delenv("LEA_AUTHZ_ALLOW_UNTRUSTED_HEADERS", raising=False)
    # internal.py 的 _tenant_llm_fallback 会读这两个。清掉，让"退到 env 兜底"
    # 这件事在测试里是可见的（llm 子对象为空）而不是悄悄拿到一组真地址。
    monkeypatch.delenv("LEA_TENANT_API_BASE", raising=False)
    monkeypatch.delenv("LEA_TENANT_API_KEY", raising=False)

    import lea_config.models  # noqa: F401
    from lea_config.api.internal import router as internal_router
    from lea_config.api.llm_models import router as llm_models_router

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
    app.include_router(llm_models_router)
    app.include_router(internal_router)
    app.dependency_overrides[get_db] = override_get_db

    test_client = TestClient(app, raise_server_exceptions=False)
    test_client.Session = Session
    return test_client


def headers(tenant: str, user: str = "alice", role: str | None = None) -> dict[str, str]:
    result = {"X-User-ID": user, "X-Tenant-ID": tenant}
    if role:
        result["X-User-Role"] = role
    return result


def seed_provider(
    client,
    name: str,
    *,
    tenant: str,
    models: str,
    limits: dict | None = None,
    is_default: bool = False,
    enabled: bool = True,
) -> int:
    from lea_config.models.llm_provider import LLMProvider

    db = client.Session()
    try:
        p = LLMProvider(
            tenant_id=tenant,
            name=name,
            base_url=f"https://{name}.example.com/v1",
            api_key=f"key-for-{name}",
            models=models,
            model_limits=limits,
            is_default=is_default,
            enabled=enabled,
        )
        db.add(p)
        db.commit()
        return p.id
    finally:
        db.close()


def seed_agent(client, name: str, *, tenant: str, model: str) -> int:
    from lea_config.models.agent import Agent

    db = client.Session()
    try:
        agent = Agent(
            tenant_id=tenant,
            name=name,
            description="d",
            model=model,
            enabled=True,
        )
        db.add(agent)
        db.commit()
        return agent.id
    finally:
        db.close()


def listed_models(client, tenant: str) -> list[str]:
    res = client.get("/llm-models", headers=headers(tenant))
    assert res.status_code == 200, res.text
    return [row["name"] for row in res.json()["items"]]


def resolved_llm(client, agent_id: int) -> dict:
    res = client.get(f"/internal/agent-config/{agent_id}")
    assert res.status_code == 200, res.text
    return res.json()["llm"]


def resolved_config(client, agent_id: int, model: str) -> dict:
    res = client.get(f"/internal/agent-config/{agent_id}", params={"model": model})
    assert res.status_code == 200, res.text
    return res.json()


# ── 主缺口 ────────────────────────────────────────────────────────────


def test_non_default_tenant_sees_platform_models(client):
    """这就是现网那个"模型列表是空的"。

    provider 只在 default 下有一行，访问者在别的租户里。修复前返回 []。
    """
    seed_provider(
        client, "tenant_qwen",
        tenant=DEFAULT_TENANT_ID,
        models="qwen-plus,glm-4-plus",
        limits={"qwen-plus": 128000, "glm-4-plus": 128000},
        is_default=True,
    )

    assert listed_models(client, T1) == ["glm-4-plus", "qwen-plus"]
    assert listed_models(client, T2) == ["glm-4-plus", "qwen-plus"]
    assert listed_models(client, DEFAULT_TENANT_ID) == ["glm-4-plus", "qwen-plus"]


def test_non_default_tenant_resolves_window_not_env_fallback(client):
    """列表能看到还不够 —— 运行时必须真的匹配上，否则窗口拿不到。

    修复前这里会落到 _tenant_llm_fallback：base_url / api_key 从环境变量拿
    （所以聊天还能跑，不容易发现），但 model_context_window 整个缺失，于是
    lea-agent 按 32k 兜底算预算。这条用例就是钉那个"聊天没坏、只是上下文被砍"
    的隐性降级。
    """
    seed_provider(
        client, "tenant_qwen",
        tenant=DEFAULT_TENANT_ID,
        models="qwen-plus",
        limits={"qwen-plus": 128000},
        is_default=True,
    )
    agent_id = seed_agent(client, "expert", tenant=T1, model="qwen-plus")

    llm = resolved_llm(client, agent_id)
    assert llm.get("model_context_window") == 128000
    assert llm.get("llm_base_url") == "https://tenant_qwen.example.com/v1"


def test_session_model_override_selects_provider_without_mutating_agent(client):
    seed_provider(
        client, "platform",
        tenant=DEFAULT_TENANT_ID,
        models="qwen-plus,glm-4-plus",
        limits={"qwen-plus": 128000, "glm-4-plus": 64000},
        is_default=True,
    )
    agent_id = seed_agent(client, "expert", tenant=T1, model="qwen-plus")

    config = resolved_config(client, agent_id, "glm-4-plus")
    assert config["agent"]["model"] == "glm-4-plus"
    assert config["llm"]["model_context_window"] == 64000

    db = client.Session()
    try:
        from lea_config.models.agent import Agent

        assert db.get(Agent, agent_id).model == "qwen-plus"
    finally:
        db.close()


# ── 两个消费者必须同意 ─────────────────────────────────────────────────


@pytest.mark.parametrize("tenant", [DEFAULT_TENANT_ID, T1])
def test_listed_models_are_exactly_the_resolvable_ones(client, tenant):
    """列表里的每个名字都必须能在运行时匹配到 provider，反之亦然。

    "列表里有但选不中"是最坏的一种：用户选了它，请求悄悄走默认 provider，
    现象只是"这个专家答得不太对劲"，不会有人归因到模型名。
    """
    seed_provider(
        client, "platform",
        tenant=DEFAULT_TENANT_ID,
        models="qwen-plus,glm-4-flash",
        limits={"qwen-plus": 128000, "glm-4-flash": 128000},
        is_default=True,
    )
    seed_provider(
        client, "tenant_own",
        tenant=T1,
        models="glm-4-plus",
        limits={"glm-4-plus": 128000},
    )

    for name in listed_models(client, tenant):
        agent_id = seed_agent(client, f"a-{name}", tenant=tenant, model=name)
        llm = resolved_llm(client, agent_id)
        # 匹配到了真 provider 的标志：窗口有值。退到 env 兜底时这个键不存在。
        assert llm.get("model_context_window") == 128000, (
            f"{tenant} 的列表里有 {name}，但运行时没匹配到 provider：{llm}"
        )


def test_disabled_provider_hidden_from_both_sides(client):
    """enabled=False 要在两边同时消失，不能一边看得见一边匹配不到。"""
    seed_provider(
        client, "retired",
        tenant=DEFAULT_TENANT_ID,
        models="qwen3-max",
        limits={"qwen3-max": 258048},
        enabled=False,
    )

    assert listed_models(client, T1) == []

    agent_id = seed_agent(client, "stale", tenant=T1, model="qwen3-max")
    llm = resolved_llm(client, agent_id)
    assert "model_context_window" not in llm, "停用的 provider 仍被运行时选中"


# ── 本租户优先于 default ──────────────────────────────────────────────


def test_tenant_own_provider_wins_over_default(client):
    """同名模型在两个租户下都有时，本租户自己的那条优先。

    顺序由 tenant_first 定，不是数据库返回顺序。不定序的话租户自建的 provider
    会被 default 盖掉 —— 而两者的 base_url / api_key 可能完全不同。
    """
    seed_provider(
        client, "platform",
        tenant=DEFAULT_TENANT_ID,
        models="qwen-plus",
        limits={"qwen-plus": 100000},
        is_default=True,
    )
    seed_provider(
        client, "tenant_own",
        tenant=T1,
        models="qwen-plus",
        limits={"qwen-plus": 128000},
    )

    items = client.get("/llm-models", headers=headers(T1)).json()["items"]
    assert [m["name"] for m in items] == ["qwen-plus"], "同名模型没去重"
    assert items[0]["provider"] == "tenant_own"
    assert items[0]["context_window"] == 128000

    # 运行时必须选中同一个 provider，否则列表显示的和实际调用的不是一个
    agent_id = seed_agent(client, "e", tenant=T1, model="qwen-plus")
    llm = resolved_llm(client, agent_id)
    assert llm["llm_base_url"] == "https://tenant_own.example.com/v1"
    assert llm["model_context_window"] == 128000


# ── 放宽过滤不能开出跨租户泄露 ─────────────────────────────────────────


def test_cross_tenant_provider_stays_invisible(client):
    """t1 不能看到 t2 的 provider。放宽只放开 default 这一个口子。"""
    seed_provider(
        client, "t2_private",
        tenant=T2,
        models="secret-model",
        limits={"secret-model": 999},
    )

    assert listed_models(client, T1) == []
    assert listed_models(client, DEFAULT_TENANT_ID) == []
    assert listed_models(client, T2) == ["secret-model"]


def test_cross_tenant_provider_not_resolvable(client):
    """运行时同样不能跨租户选中 —— 那等于把别人的 api_key 下发给引擎。"""
    seed_provider(
        client, "t2_private",
        tenant=T2,
        models="secret-model",
        limits={"secret-model": 999},
    )
    agent_id = seed_agent(client, "thief", tenant=T1, model="secret-model")

    llm = resolved_llm(client, agent_id)
    assert llm == {} or "t2_private" not in str(llm), f"跨租户拿到了 provider：{llm}"
