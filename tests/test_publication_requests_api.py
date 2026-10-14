from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from lea_config.database import OWNER_SCOPE_PLATFORM, OWNER_SCOPE_USER, Base, get_db
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

T1, T2 = "publication-t1", "publication-t2"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("LEA_INTERNAL_TOKEN", "tok")
    monkeypatch.delenv("LEA_AUTHZ_ALLOW_UNTRUSTED_HEADERS", raising=False)

    import lea_config.models  # noqa: F401
    from lea_config.api.audit_events import router as audit_router
    from lea_config.api.internal_governance import router as internal_governance_router
    from lea_config.api.providers import router as providers_router
    from lea_config.api.publication_requests import router as publication_router
    from lea_config.api.publication_resources import router as resource_publication_router
    from lea_config.api.publications import router as publications_router
    from lea_config.api.resource_checks import router as checks_router
    from lea_config.api.skills import router as skills_router

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
    app.include_router(skills_router)
    app.include_router(publication_router)
    app.include_router(resource_publication_router)
    app.include_router(publications_router)
    app.include_router(checks_router)
    app.include_router(internal_governance_router)
    app.include_router(audit_router)
    app.include_router(providers_router)
    app.dependency_overrides[get_db] = override_get_db

    test_client = TestClient(app, raise_server_exceptions=False)
    test_client.Session = Session
    return test_client


def headers(user: str, tenant: str = T1, role: str | None = None) -> dict[str, str]:
    result = {"X-User-ID": user, "X-Tenant-ID": tenant}
    if role:
        result["X-User-Role"] = role
    return result


def seed_skill(
    client,
    name: str,
    *,
    tenant: str = T1,
    owner: str = "alice",
    scope: str = OWNER_SCOPE_USER,
) -> int:
    from lea_config.models.skill import Skill

    db = client.Session()
    try:
        skill = Skill(
            tenant_id=tenant,
            name=name,
            display_name=name,
            description="initial",
            system_prompt="private prompt",
            version="1.2.0",
            category="general",
            required_tools=["search"],
            owner_scope=scope,
            owner_user_id=owner if scope == OWNER_SCOPE_USER else None,
            created_by=owner,
            enabled=True,
        )
        db.add(skill)
        db.commit()
        return skill.id
    finally:
        db.close()


def submit(client, skill_id: int, target: str = "tenant", *, user: str = "alice", role=None):
    return client.post(
        f"/skills/{skill_id}/publication-requests",
        json={"target_scope": target, "note": "ready"},
        headers=headers(user, role=role),
    )


def test_submission_captures_version_and_redacted_snapshot(client):
    skill_id = seed_skill(client, "snapshot")
    response = submit(client, skill_id)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["resource_type"] == "skill"
    assert body["resource_id"] == str(skill_id)
    assert body["resource_version"] == "1.2.0"
    assert body["source_scope"] == "private"
    assert body["resource_snapshot"]["description"] == "initial"
    assert "system_prompt" not in body["resource_snapshot"]
    assert len(body["resource_snapshot"]["system_prompt_sha256"]) == 64


def test_request_lists_are_scoped_by_actor_role(client):
    private_id = seed_skill(client, "tenant-request")
    tenant_id = seed_skill(client, "global-request", scope=OWNER_SCOPE_PLATFORM)
    submit(client, private_id)
    submit(client, tenant_id, "global", user="root", role="admin")

    ordinary = client.get("/publication-requests", headers=headers("alice"))
    assert ordinary.status_code == 403

    tenant_admin = client.get(
        "/publication-requests", headers=headers("root", role="admin")
    ).json()
    assert {row["target_scope"] for row in tenant_admin["requests"]} == {"tenant"}

    platform_admin = client.get(
        "/publication-requests", headers=headers("platform", role="platform_admin")
    ).json()
    assert {row["target_scope"] for row in platform_admin["requests"]} == {"global"}


def test_tenant_and_platform_approval_boundaries(client):
    private_id = seed_skill(client, "tenant-approval")
    tenant_request = submit(client, private_id).json()
    approved = client.post(
        f"/publication-requests/{tenant_request['id']}/review",
        json={"decision": "approved", "note": "ok"},
        headers=headers("root", role="admin"),
    )
    assert approved.status_code == 200, approved.text

    tenant_id = seed_skill(client, "global-approval", scope=OWNER_SCOPE_PLATFORM)
    global_request = submit(
        client, tenant_id, "global", user="root", role="admin"
    ).json()
    denied = client.post(
        f"/publication-requests/{global_request['id']}/review",
        json={"decision": "approved"},
        headers=headers("root", role="admin"),
    )
    assert denied.status_code == 403
    accepted = client.post(
        f"/publication-requests/{global_request['id']}/review",
        json={"decision": "approved"},
        headers=headers("platform", role="platform_admin"),
    )
    assert accepted.status_code == 200, accepted.text


def test_changed_resource_cannot_be_approved_from_stale_snapshot(client):
    skill_id = seed_skill(client, "changed")
    request_id = submit(client, skill_id).json()["id"]
    changed = client.put(
        f"/skills/{skill_id}",
        json={"description": "changed after review submission"},
        headers=headers("alice"),
    )
    assert changed.status_code == 200

    response = client.post(
        f"/publication-requests/{request_id}/review",
        json={"decision": "approved"},
        headers=headers("root", role="admin"),
    )
    assert response.status_code == 409


def test_duplicate_and_repeated_review_are_conflicts(client):
    skill_id = seed_skill(client, "conflicts")
    first = submit(client, skill_id)
    assert first.status_code == 201
    assert submit(client, skill_id).status_code == 409

    request_id = first.json()["id"]
    review_url = f"/publication-requests/{request_id}/review"
    review_headers = headers("root", role="admin")
    assert client.post(
        review_url, json={"decision": "rejected"}, headers=review_headers
    ).status_code == 200
    assert client.post(
        review_url, json={"decision": "approved"}, headers=review_headers
    ).status_code == 409


def test_only_requester_can_cancel_and_audit_events_are_appended(client):
    from lea_config.models.publication import AuditEvent

    skill_id = seed_skill(client, "cancel")
    request_id = submit(client, skill_id).json()["id"]

    denied = client.post(
        f"/skills/{skill_id}/publication-requests/{request_id}/cancel",
        json={"note": "not mine"},
        headers=headers("bob"),
    )
    assert denied.status_code == 403
    canceled = client.post(
        f"/skills/{skill_id}/publication-requests/{request_id}/cancel",
        json={"note": "withdrawn"},
        headers=headers("alice"),
    )
    assert canceled.status_code == 200
    assert canceled.json()["status"] == "canceled"

    db = client.Session()
    try:
        events = db.query(AuditEvent).order_by(AuditEvent.created_at).all()
        assert [event.action for event in events] == [
            "publication.submitted",
            "publication.canceled",
        ]
        assert all(event.request_id == str(request_id) for event in events)
        assert events[-1].reason == "withdrawn"
    finally:
        db.close()


def test_deleting_resource_cancels_pending_request(client):
    skill_id = seed_skill(client, "delete-with-request")
    request_id = submit(client, skill_id).json()["id"]

    deleted = client.delete(f"/skills/{skill_id}", headers=headers("alice"))
    assert deleted.status_code == 200
    pending = client.get(
        "/publication-requests", headers=headers("root", role="admin")
    ).json()
    assert pending["requests"] == []

    canceled = client.get(
        "/publication-requests?status=canceled",
        headers=headers("root", role="admin"),
    ).json()
    assert [row["id"] for row in canceled["requests"]] == [request_id]


def test_audit_events_are_role_scoped(client):
    first_id = seed_skill(client, "audit-one")
    second_id = seed_skill(client, "audit-two", owner="bob")
    # 两条都只是铺数据：alice 的那条用来证明 actor_user_id=bob 的过滤真的排除了别人
    submit(client, first_id)
    submit(client, second_id, user="bob")

    ordinary = client.get("/audit-events", headers=headers("alice"))
    assert ordinary.status_code == 403

    forbidden = client.get(
        "/audit-events?actor_user_id=bob", headers=headers("alice")
    )
    assert forbidden.status_code == 403

    tenant_admin = client.get(
        "/audit-events?actor_user_id=bob", headers=headers("root", role="admin")
    )
    assert tenant_admin.status_code == 200
    assert {row["actor_user_id"] for row in tenant_admin.json()["items"]} == {"bob"}


def test_skill_update_invalidates_pending_and_reports_impact(client):
    skill_id = seed_skill(client, "impact")
    submit(client, skill_id)

    updated = client.put(
        f"/skills/{skill_id}",
        json={"enabled": False},
        headers=headers("alice"),
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["impact"] == {
        "active_agent_bindings": 0,
        "enabled_changed": True,
        "publication_request_invalidated": True,
    }

    pending = client.get(
        "/publication-requests", headers=headers("root", role="admin")
    )
    assert pending.json()["requests"] == []


def test_agent_publication_uses_generic_workflow(client):
    from lea_config.models.agent import Agent

    db = client.Session()
    try:
        agent = Agent(
            tenant_id=T1,
            name="agent-for-publication",
            description="draft agent",
            soul_prompt="private instructions",
            owner_scope=OWNER_SCOPE_USER,
            owner_user_id="alice",
            created_by="alice",
            enabled=True,
        )
        db.add(agent)
        db.commit()
        agent_id = agent.id
    finally:
        db.close()

    submitted = client.post(
        f"/agents/{agent_id}/publication-requests",
        json={"target_scope": "tenant", "note": "share agent"},
        headers=headers("alice"),
    )
    assert submitted.status_code == 201, submitted.text
    request = submitted.json()
    assert request["resource_type"] == "agent"
    assert "soul_prompt" not in request["resource_snapshot"]

    reviewed = client.post(
        f"/publication-requests/{request['id']}/review",
        json={"decision": "approved"},
        headers=headers("root", role="admin"),
    )
    assert reviewed.status_code == 200, reviewed.text

    db = client.Session()
    try:
        current = db.get(Agent, agent_id)
        assert current.owner_scope == OWNER_SCOPE_PLATFORM
        assert current.tenant_id == T1
    finally:
        db.close()


def test_tool_requires_latest_check_and_supports_publication_lifecycle(client):
    from lea_config.models.publication import ResourcePublication
    from lea_config.models.tool import Tool

    db = client.Session()
    try:
        tool = Tool(
            tenant_id=T1,
            name="checked-tool",
            display_name="Checked Tool",
            execution_type="api",
            schema_json={"type": "object", "properties": {}},
            endpoint="https://example.com/tool",
            owner_scope=OWNER_SCOPE_USER,
            owner_user_id="alice",
            enabled=True,
            is_builtin=False,
        )
        db.add(tool)
        db.commit()
        tool_id = tool.id
    finally:
        db.close()

    submitted = client.post(
        f"/tools/{tool_id}/publication-requests",
        json={"target_scope": "tenant", "note": "publish checked tool"},
        headers=headers("alice"),
    )
    assert submitted.status_code == 201, submitted.text
    request_id = submitted.json()["id"]

    blocked = client.post(
        f"/publication-requests/{request_id}/review",
        json={"decision": "approved", "note": "missing check"},
        headers=headers("root", role="admin"),
    )
    assert blocked.status_code == 409

    checked = client.post(
        f"/tools/{tool_id}/checks", headers=headers("root", role="admin")
    )
    assert checked.status_code == 201, checked.text
    assert checked.json()["status"] == "passed"

    approved = client.post(
        f"/publication-requests/{request_id}/review",
        json={"decision": "approved", "note": "checks passed"},
        headers=headers("root", role="admin"),
    )
    assert approved.status_code == 200, approved.text

    db = client.Session()
    try:
        publication = db.query(ResourcePublication).one()
        assert publication.status == "active"
    finally:
        db.close()

    suspended = client.post(
        f"/publications/tool/{tool_id}/suspend",
        json={"reason": "incident investigation"},
        headers=headers("root", role="admin"),
    )
    assert suspended.status_code == 200, suspended.text
    assert suspended.json()["status"] == "suspended"

    resumed = client.post(
        f"/publications/tool/{tool_id}/resume",
        json={"reason": "incident resolved"},
        headers=headers("root", role="admin"),
    )
    assert resumed.status_code == 200

    archived = client.post(
        f"/publications/tool/{tool_id}/archive",
        json={"reason": "retired"},
        headers=headers("root", role="admin"),
    )
    assert archived.status_code == 200
    assert client.post(
        f"/publications/tool/{tool_id}/resume",
        json={"reason": "should fail"},
        headers=headers("root", role="admin"),
    ).status_code == 409


def test_external_knowledge_base_request_uses_shared_review_flow(client, monkeypatch):
    import lea_config.services.publication as publication_service

    applied = []
    monkeypatch.setattr(
        publication_service,
        "_apply_knowledge_base_review",
        lambda request: applied.append((request.resource_id, request.target_scope)),
    )
    created = client.post(
        "/internal/governance/publication-requests",
        json={
            "resource_type": "knowledge_base",
            "resource_id": "42",
            "resource_version": "2026-10-08T10:00:00",
            "tenant_id": T1,
            "source_scope": "private",
            "target_scope": "tenant",
            "note": "publish kb",
            "risk_snapshot": {"document_count": 2},
            "resource_snapshot": {"name": "course materials"},
        },
        headers=headers("alice"),
    )
    assert created.status_code == 201, created.text

    reviewed = client.post(
        f"/publication-requests/{created.json()['id']}/review",
        json={"decision": "approved", "note": "ok"},
        headers=headers("root", role="admin"),
    )
    assert reviewed.status_code == 200, reviewed.text
    assert applied == [("42", "tenant")]


def test_external_audit_ingestion_is_tenant_scoped(client):
    created = client.post(
        "/internal/governance/audit-events",
        json={
            "tenant_id": T1,
            "action": "quota.update",
            "resource_type": "tenant_quota",
            "resource_id": T1,
            "reason": "capacity change",
            "after_summary": {"max_calls_per_hour": 900},
        },
        headers=headers("ops", role="admin"),
    )
    assert created.status_code == 201, created.text

    cross_tenant = client.post(
        "/internal/governance/audit-events",
        json={
            "tenant_id": "tenant-2",
            "action": "quota.update",
            "resource_type": "tenant_quota",
            "resource_id": "tenant-2",
        },
        headers=headers("ops", role="admin"),
    )
    assert cross_tenant.status_code == 403

    events = client.get(
        "/audit-events?action=quota.update", headers=headers("ops", role="admin")
    )
    assert events.status_code == 200
    assert events.json()["total"] == 1


def test_provider_create_writes_redacted_audit_event(client):
    created = client.post(
        "/providers",
        json={
            "name": "tenant-provider",
            "base_url": "https://example.com/v1",
            "api_key": "secret-value",
            "models": "model-a",
            "reason": "add tenant model access",
        },
        headers=headers("ops", role="admin"),
    )
    assert created.status_code == 201, created.text
    assert created.json()["has_api_key"] is True
    assert "api_key" not in created.json()

    events = client.get(
        "/audit-events?action=provider.create", headers=headers("ops", role="admin")
    )
    assert events.status_code == 200
    event = events.json()["items"][0]
    assert event["after_summary"]["has_api_key"] is True
    assert "api_key" not in event["after_summary"]
    assert event["reason"] == "add tenant model access"


def test_provider_delete_requires_explicit_disable(client):
    created = client.post(
        "/providers",
        json={
            "name": "delete-guard",
            "base_url": "https://example.com/v1",
            "reason": "temporary provider",
        },
        headers=headers("ops", role="admin"),
    )
    provider_id = created.json()["id"]

    blocked = client.delete(
        f"/providers/{provider_id}?reason=retire",
        headers=headers("ops", role="admin"),
    )
    assert blocked.status_code == 409

    disabled = client.put(
        f"/providers/{provider_id}",
        json={"enabled": False, "is_default": False, "reason": "prepare retirement"},
        headers=headers("ops", role="admin"),
    )
    assert disabled.status_code == 200, disabled.text
    deleted = client.delete(
        f"/providers/{provider_id}?reason=retire",
        headers=headers("ops", role="admin"),
    )
    assert deleted.status_code == 204, deleted.text
    events = client.get(
        "/audit-events?action=provider.delete", headers=headers("ops", role="admin")
    ).json()["items"]
    assert {event["result"] for event in events} == {"blocked", "success"}


def test_provider_rejects_local_network_target(client):
    response = client.post(
        "/providers",
        json={
            "name": "unsafe-provider",
            "base_url": "http://127.0.0.1:8500",
            "reason": "should be blocked",
        },
        headers=headers("ops", role="admin"),
    )
    assert response.status_code == 400


# ── 用户侧撤销：agent / tool / mcp-server ────────────────────────────────
#
# 通用管理路由 /publication-requests/{id}/cancel 把 require_admin 和"只有申请人"
# 两道门叠在一起，申请人倒在第一道、租户管理员倒在第二道，实际只有平台管理员能撤。
# 下面这组用例锁住资源侧那条补缺口的路由：申请人能撤自己的，别人撤不动。


def seed_agent(client, name: str, *, tenant: str = T1, owner: str = "alice") -> int:
    from lea_config.models.agent import Agent

    db = client.Session()
    try:
        agent = Agent(
            tenant_id=tenant,
            name=name,
            description="draft",
            soul_prompt="private instructions",
            owner_scope=OWNER_SCOPE_USER,
            owner_user_id=owner,
            created_by=owner,
            enabled=True,
        )
        db.add(agent)
        db.commit()
        return agent.id
    finally:
        db.close()


def test_requester_can_cancel_own_agent_publication(client):
    agent_id = seed_agent(client, "cancel-own-agent")
    request_id = client.post(
        f"/agents/{agent_id}/publication-requests",
        json={"target_scope": "tenant"},
        headers=headers("alice"),
    ).json()["id"]

    # 这正是以前走不通的那条：申请人自己撤，不带任何管理角色。
    canceled = client.post(
        f"/agents/{agent_id}/publication-requests/{request_id}/cancel",
        json={"note": "想再改改"},
        headers=headers("alice"),
    )
    assert canceled.status_code == 200, canceled.text
    assert canceled.json()["status"] == "canceled"

    # 撤销后 pending_key 释放，能重新提交。
    again = client.post(
        f"/agents/{agent_id}/publication-requests",
        json={"target_scope": "tenant"},
        headers=headers("alice"),
    )
    assert again.status_code == 201, again.text


def test_cancel_rejects_non_requester(client):
    agent_id = seed_agent(client, "cancel-other-agent")
    request_id = client.post(
        f"/agents/{agent_id}/publication-requests",
        json={"target_scope": "tenant"},
        headers=headers("alice"),
    ).json()["id"]

    # 同租户里的另一个普通用户：撤不动别人的申请。
    assert client.post(
        f"/agents/{agent_id}/publication-requests/{request_id}/cancel",
        json={},
        headers=headers("bob"),
    ).status_code == 403

    # 租户管理员也不行 —— 撤销是申请人的动作，管理员要拒就走 review。
    assert client.post(
        f"/agents/{agent_id}/publication-requests/{request_id}/cancel",
        json={},
        headers=headers("root", role="admin"),
    ).status_code == 403

    # 跨租户一律 404，不泄露申请是否存在。
    assert client.post(
        f"/agents/{agent_id}/publication-requests/{request_id}/cancel",
        json={},
        headers=headers("alice", tenant=T2),
    ).status_code == 404

    # 平台管理员可以兜底撤。
    assert client.post(
        f"/agents/{agent_id}/publication-requests/{request_id}/cancel",
        json={},
        headers=headers("platform", role="platform_admin"),
    ).status_code == 200


def test_cancel_rejects_mismatched_resource(client):
    agent_id = seed_agent(client, "cancel-mismatch-agent")
    other_id = seed_agent(client, "cancel-mismatch-other")
    request_id = client.post(
        f"/agents/{agent_id}/publication-requests",
        json={"target_scope": "tenant"},
        headers=headers("alice"),
    ).json()["id"]

    # 申请号对、资源号不对：不能借另一个资源的路径撤掉它。
    assert client.post(
        f"/agents/{other_id}/publication-requests/{request_id}/cancel",
        json={},
        headers=headers("alice"),
    ).status_code == 404

    # 资源类型也得对上：agent 的申请不能从 tools 路径撤。
    assert client.post(
        f"/tools/{agent_id}/publication-requests/{request_id}/cancel",
        json={},
        headers=headers("alice"),
    ).status_code == 404


def test_cancel_is_not_repeatable(client):
    agent_id = seed_agent(client, "cancel-twice-agent")
    request_id = client.post(
        f"/agents/{agent_id}/publication-requests",
        json={"target_scope": "tenant"},
        headers=headers("alice"),
    ).json()["id"]

    assert client.post(
        f"/agents/{agent_id}/publication-requests/{request_id}/cancel",
        json={},
        headers=headers("alice"),
    ).status_code == 200
    # 已经是 canceled，再撤一次是 409 而不是又成功一遍。
    assert client.post(
        f"/agents/{agent_id}/publication-requests/{request_id}/cancel",
        json={},
        headers=headers("alice"),
    ).status_code == 409


def test_tool_and_mcp_cancel_routes_are_wired_to_their_own_type(client):
    """三条撤销路由共用 _cancel，但各自传的 RESOURCE_* 常量是手写的。

    这个用例只为一件事：确认 tool / mcp-server 那两条没把常量抄错 —— 抄错的话
    资源类型对不上，撤自己的申请会变成 404。
    """
    from lea_config.models.mcp_server import MCPServer
    from lea_config.models.tool import Tool

    db = client.Session()
    try:
        tool = Tool(
            tenant_id=T1,
            name="cancelable-tool",
            display_name="Cancelable Tool",
            execution_type="api",
            schema_json={"type": "object", "properties": {}},
            endpoint="https://example.com/tool",
            owner_scope=OWNER_SCOPE_USER,
            owner_user_id="alice",
            enabled=True,
            is_builtin=False,
        )
        server = MCPServer(
            tenant_id=T1,
            name="cancelable-mcp",
            description="draft",
            endpoint="https://example.com/mcp",
            owner_scope=OWNER_SCOPE_USER,
            owner_user_id="alice",
            created_by="alice",
            enabled=True,
        )
        db.add_all([tool, server])
        db.commit()
        tool_id, server_id = tool.id, server.id
    finally:
        db.close()

    for path, resource_id in (("tools", tool_id), ("mcp-servers", server_id)):
        request_id = client.post(
            f"/{path}/{resource_id}/publication-requests",
            json={"target_scope": "tenant"},
            headers=headers("alice"),
        ).json()["id"]
        canceled = client.post(
            f"/{path}/{resource_id}/publication-requests/{request_id}/cancel",
            json={"note": "撤回"},
            headers=headers("alice"),
        )
        assert canceled.status_code == 200, f"{path}: {canceled.text}"
        assert canceled.json()["status"] == "canceled"
