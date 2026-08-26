"""AgentBindingService 单测 —— CRUD + validate 关键行为。"""

from __future__ import annotations

import pytest


def test_upsert_skill_binding_idempotent(db_session, seed_agent, seed_skills):
    """同一对 (agent, skill) 反复 upsert，只有 1 行，priority/enabled 会更新。"""
    from lea_server.models.agent_binding import AgentSkillBinding
    from lea_server.services.agent_binding_service import AgentBindingService

    svc = AgentBindingService(db_session)
    skill_gen = seed_skills[0]

    b1 = svc.upsert_skill_binding(seed_agent.id, skill_gen.id, priority=50)
    b2 = svc.upsert_skill_binding(seed_agent.id, skill_gen.id, priority=100, enabled=False)
    db_session.commit()

    assert b1.id == b2.id  # 同一行
    rows = db_session.query(AgentSkillBinding).filter(
        AgentSkillBinding.agent_id == seed_agent.id
    ).all()
    assert len(rows) == 1
    assert rows[0].priority == 100
    assert rows[0].enabled is False


def test_replace_skill_bindings_clears_and_recreates(db_session, seed_agent, seed_skills):
    """replace 应清空后重建，最终计数与 items 数一致。"""
    from lea_server.services.agent_binding_service import AgentBindingService

    svc = AgentBindingService(db_session)

    svc.upsert_skill_binding(seed_agent.id, seed_skills[0].id)
    svc.upsert_skill_binding(seed_agent.id, seed_skills[1].id)
    db_session.commit()

    result = svc.replace_skill_bindings(
        seed_agent.id,
        [{"skill_id": seed_skills[2].id, "priority": 10}],
    )
    db_session.commit()

    assert len(result) == 1
    assert result[0]["skill_id"] == seed_skills[2].id
    assert result[0]["priority"] == 10


def test_get_bound_skill_ids_respects_enabled_flag(db_session, seed_agent, seed_skills):
    """only_enabled=True 应过滤 enabled=False 的行。"""
    from lea_server.services.agent_binding_service import AgentBindingService

    svc = AgentBindingService(db_session)
    svc.upsert_skill_binding(seed_agent.id, seed_skills[0].id, enabled=True)
    svc.upsert_skill_binding(seed_agent.id, seed_skills[1].id, enabled=False)
    db_session.commit()

    enabled_only = set(svc.get_bound_skill_ids(seed_agent.id, only_enabled=True))
    all_ids = set(svc.get_bound_skill_ids(seed_agent.id, only_enabled=False))

    assert enabled_only == {seed_skills[0].id}
    assert all_ids == {seed_skills[0].id, seed_skills[1].id}


def test_validate_skill_binding_reports_missing_tools(db_session, seed_agent, seed_skills):
    """required_tools 里的 tool 不在 available 集合中 → missing 非空。"""
    from lea_server.models.skill import Skill
    from lea_server.services.agent_binding_service import AgentBindingService

    # 加一个 skill，声明依赖 tool
    skill = Skill(
        name="needs-web-search",
        display_name="X",
        description="",
        model_tier="mid",
        required_tools=["web_search", "kb_search"],
        pipeline_class="",
        status="published",
        scope="public",
        is_builtin=False,
        tenant_id="default",
    )
    db_session.add(skill)
    db_session.commit()

    svc = AgentBindingService(db_session)
    result = svc.validate_skill_binding(
        seed_agent.id, skill.id, available_tool_names={"kb_search"}
    )

    assert result["ok"] is False
    assert result["missing_tools"] == ["web_search"]
    assert result["available_tools"] == ["kb_search"]


def test_upsert_kb_binding_rejects_invalid_strategy(db_session, seed_agent, seed_kbs):
    """strategy 非法值应抛 ValueError。"""
    from lea_server.services.agent_binding_service import AgentBindingService

    svc = AgentBindingService(db_session)
    with pytest.raises(ValueError, match="strategy"):
        svc.upsert_kb_binding(seed_agent.id, seed_kbs[0].id, strategy="bogus")


def test_get_kb_binding_defaults(db_session, seed_agent, seed_kbs):
    """LLM tool_call 读默认参数：绑了 → 返回 dict；未绑 → None。"""
    from lea_server.services.agent_binding_service import AgentBindingService

    svc = AgentBindingService(db_session)
    svc.upsert_kb_binding(
        seed_agent.id, seed_kbs[0].id, strategy="semantic", top_k=10, weight=0.7
    )
    db_session.commit()

    defaults = svc.get_kb_binding_defaults(seed_agent.id, seed_kbs[0].id)
    assert defaults == {"strategy": "semantic", "top_k": 10, "weight": 0.7}

    # 未绑的 kb → None
    assert svc.get_kb_binding_defaults(seed_agent.id, seed_kbs[1].id) is None
