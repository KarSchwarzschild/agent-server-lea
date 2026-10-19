"""Agent 配置版本化 + 回滚。

skills 从 015 起就有版本表，agents 一直没有 —— 于是一个被调教好的 agent，
谁手滑改了 soul_prompt，上一版就永久没了。发布审批里的 resource_snapshot(032)
是审批流留档，只在提交审批那一刻产生，不覆盖日常编辑，也没有回滚路径。

这个文件钉住的是"改坏了能回去"，以及几个容易悄悄退化的决定：

  - 创建时就有基线版本（否则第一次编辑的历史里只有"改成了什么"）
  - 回滚是**追加**而不是改写（所以回滚之后还能再回滚回来）
  - 快照只覆盖 VERSIONED_FIELDS（回滚一次配置不该把 agent 换个主人）
  - "保存但什么都没动"不留版本（否则真正的变更被噪声淹掉）
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from lea_config.database import OWNER_SCOPE_PLATFORM, Base, get_db
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


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


def h(user="alice", tenant="t1", role="user"):
    return {"X-User-ID": user, "X-Tenant-ID": tenant, "X-User-Role": role}


def mk(client, **over):
    """走真实 POST /agents 创建，这样基线版本也是真实路径产生的。"""
    body = {"name": "测试专家", "soul_prompt": "你是一个助手", "model": "glm-4-air"}
    body.update(over)
    r = client.post("/agents", json=body, headers=h())
    assert r.status_code == 201, r.text
    return r.json()["id"]


def versions(client, aid):
    r = client.get(f"/agents/{aid}/versions", headers=h())
    assert r.status_code == 200, r.text
    return r.json()["items"]


# ── 基线 ────────────────────────────────────────────────────────────────────


def test_create_produces_baseline_version(client):
    """创建就该有 revision 1 —— 否则第一次编辑后无从回滚。"""
    aid = mk(client)
    vs = versions(client, aid)
    assert len(vs) == 1
    assert vs[0]["revision"] == 1
    assert vs[0]["change_kind"] == "create"


def test_baseline_snapshot_has_the_original_content(client):
    aid = mk(client, soul_prompt="原始人格")
    r = client.get(f"/agents/{aid}/versions/1", headers=h())
    assert r.status_code == 200, r.text
    assert r.json()["snapshot"]["soul_prompt"] == "原始人格"


# ── 编辑留痕 ────────────────────────────────────────────────────────────────


def test_update_appends_version(client):
    aid = mk(client, soul_prompt="第一版")
    client.put(f"/agents/{aid}", json={"soul_prompt": "第二版"}, headers=h())
    vs = versions(client, aid)
    assert [v["revision"] for v in vs] == [2, 1], "新的该在前"
    assert vs[0]["change_kind"] == "update"


def test_version_records_state_after_change(client):
    """快照记改完之后的状态，所以 revision N 的内容就是"那时候长什么样"。"""
    aid = mk(client, soul_prompt="第一版")
    client.put(f"/agents/{aid}", json={"soul_prompt": "第二版"}, headers=h())
    r1 = client.get(f"/agents/{aid}/versions/1", headers=h()).json()
    r2 = client.get(f"/agents/{aid}/versions/2", headers=h()).json()
    assert r1["snapshot"]["soul_prompt"] == "第一版"
    assert r2["snapshot"]["soul_prompt"] == "第二版"


def test_change_note_lists_changed_fields(client):
    aid = mk(client)
    client.put(
        f"/agents/{aid}",
        json={"soul_prompt": "新人格", "max_turns": 50},
        headers=h(),
    )
    note = versions(client, aid)[0]["change_note"]
    assert "soul_prompt" in note and "max_turns" in note


def test_noop_save_does_not_create_version(client):
    """空 body 的保存不留版本 —— 否则版本列表被噪声淹掉。"""
    aid = mk(client)
    before = len(versions(client, aid))
    r = client.put(f"/agents/{aid}", json={}, headers=h())
    assert r.status_code == 200, r.text
    assert len(versions(client, aid)) == before


def test_list_does_not_include_snapshots(client):
    """列表不带 snapshot —— soul_prompt 可能很长，几十条全带上是几百 KB。"""
    aid = mk(client, soul_prompt="x" * 500)
    assert "snapshot" not in versions(client, aid)[0]


# ── 回滚 ────────────────────────────────────────────────────────────────────


def test_rollback_restores_content(client):
    aid = mk(client, soul_prompt="好的人格")
    client.put(f"/agents/{aid}", json={"soul_prompt": "改坏了"}, headers=h())

    r = client.post(f"/agents/{aid}/rollback", json={"revision": 1}, headers=h())
    assert r.status_code == 200, r.text

    cur = client.get(f"/agents/{aid}/versions/3", headers=h()).json()
    assert cur["snapshot"]["soul_prompt"] == "好的人格"


def test_rollback_appends_rather_than_rewrites(client):
    """回滚产生新 revision，历史里那条"改坏了"仍然在。

    这是追加式的全部意义：回滚本身有留痕，而且回滚之后还能再回滚回来。
    把 revision 指针往回移的做法做不到后者。
    """
    aid = mk(client, soul_prompt="好的人格")
    client.put(f"/agents/{aid}", json={"soul_prompt": "改坏了"}, headers=h())
    client.post(f"/agents/{aid}/rollback", json={"revision": 1}, headers=h())

    vs = versions(client, aid)
    assert [v["revision"] for v in vs] == [3, 2, 1]
    assert vs[0]["change_kind"] == "rollback"
    assert "revision 1" in vs[0]["change_note"]
    # 被回滚掉的那一版内容还在，没被改写
    v2 = client.get(f"/agents/{aid}/versions/2", headers=h()).json()
    assert v2["snapshot"]["soul_prompt"] == "改坏了"


def test_can_roll_forward_again(client):
    """回滚之后能再回到被回滚掉的那一版 —— 误操作可挽回。"""
    aid = mk(client, soul_prompt="A")
    client.put(f"/agents/{aid}", json={"soul_prompt": "B"}, headers=h())
    client.post(f"/agents/{aid}/rollback", json={"revision": 1}, headers=h())  # 回到 A
    client.post(f"/agents/{aid}/rollback", json={"revision": 2}, headers=h())  # 再回 B

    v4 = client.get(f"/agents/{aid}/versions/4", headers=h()).json()
    assert v4["snapshot"]["soul_prompt"] == "B"


def test_rollback_to_missing_revision_is_404(client):
    aid = mk(client)
    r = client.post(f"/agents/{aid}/rollback", json={"revision": 99}, headers=h())
    assert r.status_code == 404


def test_rollback_does_not_change_ownership(client):
    """快照只覆盖 VERSIONED_FIELDS —— 回滚配置不该把 agent 换个主人。

    构造必须让身份字段**在快照和当前状态之间真的不同**，否则写回去是个 no-op，
    测试即使在"身份字段进了快照"的情况下也是绿的（第一版就是这个毛病：拿
    tenant_id 做注入，而它前后都是 t1）。

    所以这里在打完快照之后把 agent 共享出去（owner_scope: user → platform），
    再回滚到共享前的那一版。如果 owner_scope 跟着快照回滚，共享就被静默撤销了。
    """
    aid = mk(client)
    client.put(f"/agents/{aid}", json={"soul_prompt": "改一下"}, headers=h())

    # 共享：owner_scope 变成 platform，owner_user_id 置空
    r = client.post(f"/agents/{aid}/share", headers=h())
    assert r.status_code == 200, r.text

    db = client.Session()
    try:
        from lea_config.models.agent import Agent

        shared = db.query(Agent).filter(Agent.id == aid).first()
        assert shared.owner_scope == OWNER_SCOPE_PLATFORM, "共享没生效，构造失败"
        owner_after_share = (
            shared.tenant_id,
            shared.owner_scope,
            shared.owner_user_id,
        )
    finally:
        db.close()

    # 回滚到共享**之前**的那一版
    r = client.post(
        f"/agents/{aid}/rollback", json={"revision": 1}, headers=h(role="admin")
    )
    assert r.status_code == 200, r.text

    db = client.Session()
    try:
        from lea_config.models.agent import Agent

        after = db.query(Agent).filter(Agent.id == aid).first()
        assert (
            after.tenant_id,
            after.owner_scope,
            after.owner_user_id,
        ) == owner_after_share, "回滚把归属改了 —— 身份字段不该进快照"
        # 内容确实回滚了，证明这次回滚不是没生效
        assert after.soul_prompt == "你是一个助手"
    finally:
        db.close()


# ── 权限 ────────────────────────────────────────────────────────────────────


def test_other_user_cannot_rollback(client):
    """回滚是写操作，走 _require_mine。"""
    aid = mk(client)
    r = client.post(
        f"/agents/{aid}/rollback", json={"revision": 1}, headers=h(user="bob")
    )
    assert r.status_code in (403, 404), r.text


def test_snapshot_fields_match_updatable_fields(client):
    """VERSIONED_FIELDS 必须和 AgentUpdate 的可改字段一致。

    两边漂移的后果是静默的：AgentUpdate 加了新字段而 VERSIONED_FIELDS 没加，
    那个字段永远不进版本，回滚时被保留成当前值 —— 用户以为回滚了，其实没全回。
    """
    from lea_config.api.agents import AgentUpdate
    from lea_config.models.agent import VERSIONED_FIELDS

    updatable = set(AgentUpdate.model_fields) - {"expected_updated_at"}
    assert updatable == set(VERSIONED_FIELDS), (
        f"AgentUpdate 独有: {updatable - set(VERSIONED_FIELDS)}; "
        f"VERSIONED_FIELDS 独有: {set(VERSIONED_FIELDS) - updatable}"
    )
