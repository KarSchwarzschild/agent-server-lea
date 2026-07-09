"""chat.py 里的候选池过滤 —— agent_id 限制 skill 池。

不 boot 整个 supervisor 图，只测 `_get_skill_data` 的核心 SQL 过滤逻辑。
"""

from __future__ import annotations


def test_skill_pool_only_bound_skills(db_session, seed_agent, seed_skills):
    """给 super-agent 只绑 slide-generator → 候选池只有 slide-generator。"""
    from lea_server.api.chat import _get_skill_data
    from lea_server.services.agent_binding_service import AgentBindingService

    svc = AgentBindingService(db_session)
    svc.upsert_skill_binding(seed_agent.id, seed_skills[0].id)  # 只绑 slide-generator
    db_session.commit()

    candidates, definitions = _get_skill_data(
        db_session, user_id="test-user", agent_id=seed_agent.id
    )

    names = [c["name"] for c in candidates]
    assert names == ["slide-generator"]
    assert set(definitions.keys()) == {"slide-generator"}


def test_skill_pool_disabled_binding_excluded(db_session, seed_agent, seed_skills):
    """enabled=False 的绑定不进候选池。"""
    from lea_server.api.chat import _get_skill_data
    from lea_server.services.agent_binding_service import AgentBindingService

    svc = AgentBindingService(db_session)
    svc.upsert_skill_binding(seed_agent.id, seed_skills[0].id, enabled=True)
    svc.upsert_skill_binding(seed_agent.id, seed_skills[1].id, enabled=False)
    db_session.commit()

    candidates, _ = _get_skill_data(
        db_session, user_id="test-user", agent_id=seed_agent.id
    )
    assert [c["name"] for c in candidates] == ["slide-generator"]


def test_skill_pool_draft_status_excluded(db_session, seed_agent, seed_skills):
    """status != published 的 skill 即便绑了也不进候选池。"""
    from lea_server.api.chat import _get_skill_data
    from lea_server.services.agent_binding_service import AgentBindingService

    seed_skills[1].status = "draft"
    db_session.commit()

    svc = AgentBindingService(db_session)
    svc.upsert_skill_binding(seed_agent.id, seed_skills[0].id)
    svc.upsert_skill_binding(seed_agent.id, seed_skills[1].id)
    db_session.commit()

    candidates, _ = _get_skill_data(
        db_session, user_id="test-user", agent_id=seed_agent.id
    )
    assert [c["name"] for c in candidates] == ["slide-generator"]


def test_skill_pool_priority_order(db_session, seed_agent, seed_skills):
    """priority 数字越小越靠前。"""
    from lea_server.api.chat import _get_skill_data
    from lea_server.services.agent_binding_service import AgentBindingService

    svc = AgentBindingService(db_session)
    svc.upsert_skill_binding(seed_agent.id, seed_skills[0].id, priority=200)
    svc.upsert_skill_binding(seed_agent.id, seed_skills[1].id, priority=10)
    db_session.commit()

    candidates, _ = _get_skill_data(
        db_session, user_id="test-user", agent_id=seed_agent.id
    )
    assert [c["name"] for c in candidates] == ["slide-editor", "slide-generator"]


def test_skill_pool_agent_id_none_fallbacks_to_all(db_session, seed_skills):
    """agent_id=None 走旧行为，拉所有 published 可见 skill。"""
    from lea_server.api.chat import _get_skill_data

    candidates, _ = _get_skill_data(
        db_session, user_id="test-user", agent_id=None
    )
    names = {c["name"] for c in candidates}
    # 应至少包含 3 个 skill（skill_service.list_skills 的 user 可见性会过滤 private/created_by）
    assert "slide-generator" in names
    assert "slide-editor" in names
