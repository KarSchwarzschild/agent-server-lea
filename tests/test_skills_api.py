"""lea-config 的 /skills —— 三层可见性 + 上架审核。

这个文件盯三类回归：

    可见性  三层各自谁看得见；尤其是「所有人可见」那一层住在 tenant_id='default'，
            而 scoped_filter 表达不了它（它最后一句是 tenant_id == actor.tenant_id）
    归属    上传/创建一律落创建者私有；往上走只能通过审核，不能由调用方指定
    越权    bind_skill 曾经按裸 id 查 skill，任何人猜个 id 就能绑别的租户的

为什么第一类需要测：三层里有两层是「同一个 owner_scope='platform'」，差别只在
tenant_id 是不是 'default'。这种"靠另一列区分"的编码最容易在某个查询里漏掉一半。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from lea_config.database import (
    DEFAULT_TENANT_ID,
    OWNER_SCOPE_PLATFORM,
    OWNER_SCOPE_USER,
    Base,
    get_db,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

T1, T2 = "skills-t1", "skills-t2"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("LEA_INTERNAL_TOKEN", "tok")
    monkeypatch.delenv("LEA_AUTHZ_ALLOW_UNTRUSTED_HEADERS", raising=False)

    import lea_config.models  # noqa: F401 — 让 Base.metadata 收齐外键指向的表
    from lea_config.api.skills import router

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


def h(user: str, tenant: str = T1, role: str | None = None) -> dict:
    headers = {"X-User-ID": user, "X-Tenant-ID": tenant}
    if role:
        headers["X-User-Role"] = role
    return headers


def seed(client, name, tenant=T1, scope=OWNER_SCOPE_USER, owner="alice",
         enabled=True, bundle="s3://b/k/abc.zip", category="general"):
    """直接落库建一个 skill —— 三层里有两层建不出来（要走审核），所以只能种。"""
    from lea_config.models.skill import Skill

    db = client.Session()
    try:
        s = Skill(
            tenant_id=tenant, name=name, display_name=name, category=category,
            description="", system_prompt="body", required_tools=[],
            owner_scope=scope, owner_user_id=owner if scope == OWNER_SCOPE_USER else None,
            enabled=enabled, is_builtin=False, created_by=owner, bundle_uri=bundle,
        )
        db.add(s)
        db.commit()
        return s.id
    finally:
        db.close()


def names_in(resp) -> set[str]:
    return {s["name"] for s in resp.json()["skills"]}


# ── 三层可见性 ────────────────────────────────────────────────────────────

def test_private_skill_visible_only_to_owner(client):
    seed(client, "alice_only", owner="alice")
    assert "alice_only" in names_in(client.get("/skills", headers=h("alice")))
    assert "alice_only" not in names_in(client.get("/skills", headers=h("bob")))


def test_tenant_skill_visible_to_everyone_in_tenant(client):
    seed(client, "shared", tenant=T1, scope=OWNER_SCOPE_PLATFORM)
    for user in ("alice", "bob"):
        assert "shared" in names_in(client.get("/skills", headers=h(user)))
    # 别的租户看不到
    assert "shared" not in names_in(client.get("/skills", headers=h("carol", T2)))


def test_global_skill_visible_across_tenants(client):
    """「所有人可见」那一层住在 tenant_id='default'。

    这一格是 scoped_filter **表达不了**的：它最后一句是
    `and_(tenant_id == actor.tenant_id, ...)`，会把全局市场整个滤掉。
    直接用它的话市场页对所有租户用户都是空的，而且空得"看起来正常"。
    """
    seed(client, "market_skill", tenant=DEFAULT_TENANT_ID, scope=OWNER_SCOPE_PLATFORM)
    for tenant in (T1, T2):
        assert "market_skill" in names_in(
            client.get("/skills", headers=h("anyone", tenant))
        )


def test_visibility_field_reports_the_right_tier(client):
    seed(client, "p", owner="alice")
    seed(client, "t", scope=OWNER_SCOPE_PLATFORM)
    seed(client, "g", tenant=DEFAULT_TENANT_ID, scope=OWNER_SCOPE_PLATFORM)

    got = {s["name"]: s["visibility"] for s in
           client.get("/skills", headers=h("alice")).json()["skills"]}
    assert got == {"p": "private", "t": "tenant", "g": "global"}


def test_visibility_filter_narrows(client):
    seed(client, "p", owner="alice")
    seed(client, "t", scope=OWNER_SCOPE_PLATFORM)
    seed(client, "g", tenant=DEFAULT_TENANT_ID, scope=OWNER_SCOPE_PLATFORM)

    assert names_in(client.get("/skills?visibility=private", headers=h("alice"))) == {"p"}
    assert names_in(client.get("/skills?visibility=tenant", headers=h("alice"))) == {"t"}
    assert names_in(client.get("/skills?visibility=global", headers=h("alice"))) == {"g"}


def test_disabled_skill_still_listed_for_owner(client):
    """停用只影响**下发**，不影响列表 —— 否则用户停用之后就再也找不到它了。

    下发那一侧的过滤在 internal.py，见 test_internal_agent_config 那边。
    """
    seed(client, "off", owner="alice", enabled=False)
    body = client.get("/skills", headers=h("alice")).json()["skills"]
    assert [s["enabled"] for s in body if s["name"] == "off"] == [False]


# ── 归属：谁都不能自己指定 ────────────────────────────────────────────────

def test_create_always_lands_private(client):
    r = client.post("/skills", json={"name": "mine", "category": "general"},
                    headers=h("alice"))
    assert r.status_code == 201, r.text
    assert r.json()["visibility"] == "private"

    skill_id = r.json()["id"]
    detail = client.get(f"/skills/{skill_id}?version=1.0.0", headers=h("alice"))
    assert detail.status_code == 200, detail.text
    assert detail.json()["version"] == "1.0.0"
    assert detail.json()["change_log"] == ""
    assert detail.json()["size_bytes"] == 0

    versions = client.get(f"/skills/{skill_id}/versions", headers=h("alice"))
    assert versions.status_code == 200, versions.text
    assert [v["version"] for v in versions.json()["versions"]] == ["1.0.0"]


def test_admin_created_skill_also_lands_private(client):
    """管理员建的也是私有 —— 他要给全租户用就走审核，跟别人一条路。

    这跟 tools 那边不同（那边管理员建的直接落 platform）。区别是 skill 有审核流程，
    而"管理员自己批自己"是个空动作，不如让他也走一遍留下记录。
    """
    r = client.post("/skills", json={"name": "adm", "category": "general"},
                    headers=h("root", role="admin"))
    assert r.json()["visibility"] == "private"


def test_create_ignores_ownership_fields_in_body(client):
    """传 owner_scope / tenant_id 都不该生效 —— 那是自助提权成"全平台可用"。"""
    from lea_config.api.skills import SkillCreate

    r = client.post(
        "/skills",
        json={"name": "x", "category": "general",
              "owner_scope": "platform", "tenant_id": DEFAULT_TENANT_ID},
        headers=h("alice"),
    )
    assert r.json()["visibility"] == "private"
    # 显式钉住第一道防线：schema 上就没有这些字段
    for f in ("owner_scope", "owner_user_id", "tenant_id", "is_builtin"):
        assert f not in SkillCreate.model_fields


# ── 上架审核 ──────────────────────────────────────────────────────────────

def publish(client, skill_id, target, user="alice", tenant=T1, role=None):
    # user="root" 的场景带上 admin 角色 —— 租户级 skill 没有个人 owner，
    # 只有管理员能提它的上架申请。
    if role is None and user == "root":
        role = "admin"
    return client.post(
        f"/skills/{skill_id}/publish",
        json={"target_scope": target},
        headers=h(user, tenant, role),
    )


def test_publish_request_then_tenant_admin_approves(client):
    sid = seed(client, "promote_me", owner="alice")
    r = publish(client, sid, "tenant")
    assert r.status_code == 201, r.text
    req_id = r.json()["id"]

    # 提完之后列表里带出在审状态，前端据此把按钮变成"审核中"
    listed = client.get("/skills", headers=h("alice")).json()["skills"]
    assert [s["pending_publish"] for s in listed if s["name"] == "promote_me"] == ["tenant"]

    r = client.post(
        f"/skills/publish-requests/{req_id}/review",
        json={"approve": True},
        headers=h("root", T1, "admin"),
    )
    assert r.status_code == 200, r.text
    assert r.json()["visibility"] == "tenant"

    # 现在同租户的别人也看得见了
    assert "promote_me" in names_in(client.get("/skills", headers=h("bob")))


def test_rejected_request_leaves_skill_private(client):
    sid = seed(client, "nope", owner="alice")
    req_id = publish(client, sid, "tenant").json()["id"]

    r = client.post(
        f"/skills/publish-requests/{req_id}/review",
        json={"approve": False, "note": "文档不全"},
        headers=h("root", T1, "admin"),
    )
    assert r.json()["status"] == "rejected"
    assert "nope" not in names_in(client.get("/skills", headers=h("bob")))


def test_global_publish_needs_platform_admin(client):
    """上架到「所有人可见」那一档，租户管理员批不了。

    分层是这套设计的核心：租户管理员能决定的只是自己租户内的事，把东西推给
    别的租户是平台级决定。
    """
    sid = seed(client, "wannabe_global", scope=OWNER_SCOPE_PLATFORM)
    # 租户级 skill 的 owner_user_id 是 NULL —— 它属于租户不属于个人，所以往上推
    # 由管理员提，不是原作者。见 request_publish 的归属检查。
    req_id = publish(client, sid, "global", user="root").json()["id"]

    r = client.post(
        f"/skills/publish-requests/{req_id}/review",
        json={"approve": True},
        headers=h("root", T1, "admin"),  # 租户管理员
    )
    assert r.status_code == 403


def test_global_publish_moves_skill_to_default_tenant(client):
    """通过之后 skill 搬进全局租户，别的租户就看得见了。

    平台管理员由 lea-agent 从已验签 JWT 转发到这里，HTTP 路径直接走这条身份链。
    """
    sid = seed(client, "going_global", scope=OWNER_SCOPE_PLATFORM)
    req_id = publish(client, sid, "global", user="root").json()["id"]

    r = client.post(
        f"/skills/publish-requests/{req_id}/review",
        json={"approve": True},
        headers=h("root", T1, "platform_admin"),
    )

    assert r.status_code == 200, r.text
    assert r.json()["visibility"] == "global"
    # 别的租户现在看得见了
    assert "going_global" in names_in(client.get("/skills", headers=h("carol", T2)))


def test_duplicate_pending_request_is_409(client):
    """同一个 skill 只能有一份在审。

    不挡的话待审列表里会出现同一个 skill 的多份申请，审了一份另一份还悬着 ——
    而 MySQL 没有部分唯一索引，这条只能在接口层挡。
    """
    sid = seed(client, "dup", owner="alice")
    assert publish(client, sid, "tenant").status_code == 201
    assert publish(client, sid, "tenant").status_code == 409


def test_cannot_publish_someone_elses_skill(client):
    sid = seed(client, "alices", owner="alice")
    assert publish(client, sid, "tenant", user="bob").status_code in (403, 404)


def test_reviewing_twice_is_409(client):
    """重复审核要挡住 —— 多副本下前端重发是常态，第二次不该再翻一次归属。"""
    sid = seed(client, "once", owner="alice")
    req_id = publish(client, sid, "tenant").json()["id"]
    hdr = h("root", T1, "admin")

    assert client.post(f"/skills/publish-requests/{req_id}/review",
                       json={"approve": True}, headers=hdr).status_code == 200
    assert client.post(f"/skills/publish-requests/{req_id}/review",
                       json={"approve": False}, headers=hdr).status_code == 409


def test_publish_request_list_is_scoped_by_role(client):
    """待审列表按角色分：租户管理员看 tenant 档，平台管理员看 global 档，
    普通用户看自己提的。"""
    s1 = seed(client, "a", owner="alice")
    s2 = seed(client, "b", scope=OWNER_SCOPE_PLATFORM)
    publish(client, s1, "tenant")
    publish(client, s2, "global", user="root")

    tenant_adm = client.get("/skills/publish-requests", headers=h("root", T1, "admin"))
    assert {r["target_scope"] for r in tenant_adm.json()["requests"]} == {"tenant"}

    plat_adm = client.get("/skills/publish-requests",
                          headers=h("root", T1, "platform_admin"))
    assert {r["target_scope"] for r in plat_adm.json()["requests"]} == {"global"}

    mine = client.get("/skills/publish-requests", headers=h("alice"))
    assert {r["skill_name"] for r in mine.json()["requests"]} == {"a"}


def test_publish_requests_route_not_shadowed_by_skill_id(client):
    """路由顺序回归：/skills/publish-requests 必须排在 /skills/{skill_id} 之前。

    排反了的话 FastAPI 会拿 "publish-requests" 去转 int，返 422 —— 而那个错看起来
    像是参数问题，不像路由问题。
    """
    r = client.get("/skills/publish-requests", headers=h("alice"))
    assert r.status_code == 200, f"被 /{{skill_id}} 捕获了: {r.status_code} {r.text[:120]}"


# ── 越权 ──────────────────────────────────────────────────────────────────

def test_get_other_tenants_skill_is_404(client):
    sid = seed(client, "t1_only", tenant=T1, scope=OWNER_SCOPE_PLATFORM)
    assert client.get(f"/skills/{sid}", headers=h("carol", T2)).status_code == 404


def test_cannot_edit_others_private_skill(client):
    sid = seed(client, "alices", owner="alice")
    r = client.put(f"/skills/{sid}", json={"description": "hijack"}, headers=h("bob"))
    assert r.status_code in (403, 404)


def test_download_requires_visibility(client):
    """下载链接必须走可见性校验。

    bundle_uri 里的 sha 是可枚举的，光靠"猜不到 key"不算保护 —— 而且做了市场之后
    skill id 本来就是公开信息。
    """
    sid = seed(client, "alices", owner="alice")
    assert client.get(f"/skills/{sid}/download", headers=h("bob")).status_code == 404


def test_download_404_when_no_bundle(client):
    """纯 prompt 型 skill 没有包可下 —— 说清楚，别返一个坏链接。"""
    sid = seed(client, "prompt_only", owner="alice", bundle=None)
    r = client.get(f"/skills/{sid}/download", headers=h("alice"))
    assert r.status_code == 404
    assert "bundle" in r.json()["detail"]


def test_obs_download_accepts_signed_url_without_status(client, monkeypatch):
    """华为 OBS SDK 成功生成签名链接时 status 可能为 None。"""
    from lea_config.api import skills

    class FakeObsClient:
        def createSignedUrl(self, *_args, **_kwargs):
            return type(
                "SignedUrlResponse", (),
                {"status": None, "signedUrl": "https://obs.example/signed"},
            )()

        def close(self):
            pass

    monkeypatch.setenv("OBJECT_STORAGE", "obs")
    monkeypatch.setattr(skills, "_get_obs_client", lambda: (FakeObsClient(), "b"))
    sid = seed(client, "obs_download", owner="alice", bundle="s3://b/k/abc.zip")

    response = client.get(f"/skills/{sid}/download", headers=h("alice"))

    assert response.status_code == 200
    assert response.json()["url"] == "https://obs.example/signed"
