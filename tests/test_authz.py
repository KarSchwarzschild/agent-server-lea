"""lea_authkit.authz —— 授权判断。

纯逻辑，所以穷举成本很低而弄错的代价很高。用例分四组：

    Ownership.of    四种命名变体都要抽对（抽错 = 静默放行或静默拒绝）
    can()           三层主体 × 三种归属 × 三个动作
    require()       403 / 404 的区分（跨租户不能确认存在性）
    scoped_filter   列表约束必须进 SQL；没有归属列时**报错而不是放行**
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from sqlalchemy import Column, Integer, String, create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from lea_authkit.authz import (
    DELETE,
    OWNER_SCOPE_PLATFORM,
    OWNER_SCOPE_USER,
    READ,
    ROLE_ADMIN,
    ROLE_PLATFORM_ADMIN,
    UPDATE,
    Actor,
    Ownership,
    can,
    require,
    scoped_filter,
)

T1, T2 = "tenant-a", "tenant-b"

# user_id 在两个租户里刻意用同一个字符串 —— 这是"跨租户检查必须在归属检查之前"
# 的唯一测法。顺序反了的话 alice@T2 会对 alice@T1 的资源成立 owner。
alice_t1 = Actor("alice", T1)
bob_t1 = Actor("bob", T1)
alice_t2 = Actor("alice", T2)
admin_t1 = Actor("root", T1, (ROLE_ADMIN,))
platform = Actor("god", T1, (ROLE_PLATFORM_ADMIN,))


def own(scope=OWNER_SCOPE_USER, user="alice", tenant=T1, visibility="private"):
    return Ownership(tenant_id=tenant, scope=scope, owner_user_id=user, visibility=visibility)


PLATFORM_RES = Ownership(tenant_id=T1, scope=OWNER_SCOPE_PLATFORM, owner_user_id=None)


# ── Ownership.of：四种命名变体 ────────────────────────────────────────────

class _FakeRow:
    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


def test_of_reads_owner_scope_pair():
    """agents / skills / tools / mcp_servers（004 迁移后）。"""
    o = Ownership.of(_FakeRow(tenant_id=T1, owner_scope=OWNER_SCOPE_USER, owner_user_id="alice"))
    assert (o.tenant_id, o.scope, o.owner_user_id) == (T1, OWNER_SCOPE_USER, "alice")


def test_of_reads_legacy_owner_column():
    """knowledge_bases：owner + visibility，没有 owner_scope。"""
    o = Ownership.of(_FakeRow(tenant_id=T1, owner="alice", visibility="public"))
    assert o.scope == OWNER_SCOPE_USER
    assert o.owner_user_id == "alice"
    assert o.visibility == "public"


def test_of_treats_empty_owner_as_platform():
    o = Ownership.of(_FakeRow(tenant_id=T1, owner=None))
    assert o.scope == OWNER_SCOPE_PLATFORM
    assert o.owner_user_id is None


def test_of_ignores_created_by():
    """created_by 是审计字段，**不能**当归属。

    平台预置资源的 created_by 是某个管理员的 user_id。如果 of() 拿它兜底，那个
    管理员就成了一堆共享资源的私有主人，租户里别人反而改不了 —— 而且没有任何
    报错，只是权限莫名其妙。
    """
    o = Ownership.of(_FakeRow(tenant_id=T1, created_by="some-admin"))
    assert o.scope == OWNER_SCOPE_PLATFORM
    assert o.owner_user_id is None


# ── can()：跨租户 ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("action", [READ, UPDATE, DELETE])
def test_cross_tenant_denied_even_for_same_user_id(action):
    """同名 user_id 跨租户不成立 owner —— 检查顺序的回归测试。"""
    assert can(alice_t2, action, own(user="alice", tenant=T1)) is False


@pytest.mark.parametrize("action", [READ, UPDATE, DELETE])
def test_tenant_admin_is_not_cross_tenant(action):
    """租户管理员 ≠ 平台管理员。最容易搞错的一条。"""
    assert can(admin_t1, action, own(tenant=T2)) is False


@pytest.mark.parametrize("action", [READ, UPDATE, DELETE])
def test_platform_admin_crosses_tenants(action):
    assert can(platform, action, own(tenant=T2)) is True


# ── can()：自己的东西 ────────────────────────────────────────────────────

@pytest.mark.parametrize("action", [READ, UPDATE, DELETE])
def test_owner_has_full_control(action):
    assert can(alice_t1, action, own(user="alice")) is True


def test_public_visibility_grants_read_not_write():
    """把 visibility 当可变性用是个常见错误 —— 这条钉住它。"""
    pub = own(user="alice", visibility="public")
    assert can(bob_t1, READ, pub) is True
    assert can(bob_t1, UPDATE, pub) is False
    assert can(bob_t1, DELETE, pub) is False


def test_others_private_is_invisible():
    assert can(bob_t1, READ, own(user="alice")) is False


def test_tenant_admin_cannot_read_others_private_by_default():
    """默认最小权限。要改成能看的话，应该是独立的带审计接口。"""
    assert can(admin_t1, READ, own(user="alice")) is False


# ── can()：平台资源 ─────────────────────────────────────────────────────

def test_platform_resource_readable_by_everyone_in_tenant():
    assert can(alice_t1, READ, PLATFORM_RES) is True
    assert can(bob_t1, READ, PLATFORM_RES) is True


def test_platform_resource_writable_only_by_admin():
    assert can(alice_t1, UPDATE, PLATFORM_RES) is False
    assert can(admin_t1, UPDATE, PLATFORM_RES) is True
    assert can(admin_t1, DELETE, PLATFORM_RES) is True


def test_unknown_action_raises():
    """拼错 action 必须炸，不能静默返 False —— 那会变成一道假的门。"""
    with pytest.raises(ValueError, match="未知 action"):
        can(alice_t1, "updaet", PLATFORM_RES)


# ── require()：403 vs 404 ────────────────────────────────────────────────

def test_require_passes_for_owner():
    require(alice_t1, UPDATE, own(user="alice"))   # 不抛


def test_require_cross_tenant_is_404_not_403():
    """403 会确认"这个 id 存在" —— 跨租户不能给出这个信息。"""
    with pytest.raises(HTTPException) as e:
        require(alice_t2, READ, own(tenant=T1))
    assert e.value.status_code == 404


def test_require_same_tenant_denied_is_403():
    """同租户内 403 是可以接受的：那个 id 本来就在调用方能触及的范围里，
    而且不说"这不是你的"用户会对着 404 反复重试。"""
    with pytest.raises(HTTPException) as e:
        require(bob_t1, UPDATE, own(user="alice"))
    assert e.value.status_code == 403


# ── scoped_filter：约束必须进 SQL ────────────────────────────────────────

class _Base(DeclarativeBase):
    pass


class ScopedRow(_Base):
    __tablename__ = "scoped_rows"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(String(50))
    owner_scope = Column(String(20))
    owner_user_id = Column(String(64))


class LegacyRow(_Base):
    __tablename__ = "legacy_rows"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(String(50))
    owner = Column(String(64))
    visibility = Column(String(20))


class NoOwnershipRow(_Base):
    __tablename__ = "no_ownership_rows"
    id = Column(Integer, primary_key=True)
    tenant_id = Column(String(50))


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    _Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    s.add_all([
        ScopedRow(id=1, tenant_id=T1, owner_scope=OWNER_SCOPE_PLATFORM, owner_user_id=None),
        ScopedRow(id=2, tenant_id=T1, owner_scope=OWNER_SCOPE_USER, owner_user_id="alice"),
        ScopedRow(id=3, tenant_id=T1, owner_scope=OWNER_SCOPE_USER, owner_user_id="bob"),
        # 同名 user_id、不同租户 —— 漏 tenant 约束时这行会漏出来
        ScopedRow(id=4, tenant_id=T2, owner_scope=OWNER_SCOPE_USER, owner_user_id="alice"),
        LegacyRow(id=1, tenant_id=T1, owner=None, visibility="private"),
        LegacyRow(id=2, tenant_id=T1, owner="alice", visibility="private"),
        LegacyRow(id=3, tenant_id=T1, owner="bob", visibility="private"),
        LegacyRow(id=4, tenant_id=T1, owner="bob", visibility="public"),
    ])
    s.commit()
    yield s
    s.close()


def _ids(db, model, actor):
    return sorted(
        r.id for r in db.query(model).filter(scoped_filter(actor, model)).all()
    )


def test_scoped_filter_owner_scope_pair(db):
    # 平台的(1) + 自己的(2)。不含 bob 的(3)，不含另一租户的(4)。
    assert _ids(db, ScopedRow, alice_t1) == [1, 2]
    assert _ids(db, ScopedRow, bob_t1) == [1, 3]


def test_scoped_filter_excludes_other_tenant_same_user_id(db):
    """alice@T2 只应该看到 T2 的那行。"""
    assert _ids(db, ScopedRow, alice_t2) == [4]


def test_scoped_filter_platform_admin_crosses_tenants(db):
    assert _ids(db, ScopedRow, platform) == [1, 2, 3, 4]


def test_scoped_filter_legacy_owner_and_visibility(db):
    # 平台的(1) + 自己的(2) + 别人公开的(4)。不含 bob 私有的(3)。
    assert _ids(db, LegacyRow, alice_t1) == [1, 2, 4]


def test_scoped_filter_count_matches_rows(db):
    """分页的前提：count() 和实际行数一致。逐行 can() 过滤做不到这一点。"""
    q = db.query(ScopedRow).filter(scoped_filter(alice_t1, ScopedRow))
    assert q.count() == len(q.all())


def test_scoped_filter_refuses_model_without_ownership():
    """没有归属列时**报错**，不是放行。

    静默放行会把"忘了给这张表加归属"和"这张表本来就人人可读"混为一谈，
    而前者是个洞。
    """
    with pytest.raises(TypeError, match="没有归属列"):
        scoped_filter(alice_t1, NoOwnershipRow)


# ── 身份头的信任边界 ──────────────────────────────────────────────────────
#
# require_actor 从 X-User-ID / X-Tenant-ID / X-User-Role 取身份。上游 lea-agent 的
# 反代保证这三个头是它写的（proxy.go，有 12 条 Go 测试钉住），但这里**独立**再挡
# 一次角色白名单 —— 两侧都成立，任一侧被误改都不立刻破防。

def _actor_from_headers(**headers):
    """走真 FastAPI 依赖链拿 Actor，而不是直接调 __call__。

    直接调会绕过 Header() 的解析，测不到 alias 拼错这类问题。
    """
    from fastapi import Depends, FastAPI
    from fastapi.testclient import TestClient

    from lea_authkit import require_actor

    app = FastAPI()

    @app.get("/whoami")
    def whoami(actor: Actor = Depends(require_actor)):
        return {"user_id": actor.user_id, "tenant_id": actor.tenant_id,
                "roles": list(actor.roles),
                "is_tenant_admin": actor.is_tenant_admin,
                "is_platform_admin": actor.is_platform_admin}

    return TestClient(app, raise_server_exceptions=False).get("/whoami", headers=headers)


@pytest.fixture(autouse=True)
def _trusted_headers(monkeypatch):
    """让 require_actor 处于"服务间鉴权已开"的状态。

    没有这个的话它会 503 —— 那道联锁本身在 test_internal_auth.py 那边验。
    """
    monkeypatch.setenv("LEA_INTERNAL_TOKEN", "tok")
    monkeypatch.delenv("LEA_AUTHZ_ALLOW_UNTRUSTED_HEADERS", raising=False)


def test_actor_from_valid_headers():
    r = _actor_from_headers(**{"X-User-ID": "alice", "X-Tenant-ID": "t1"})
    assert r.status_code == 200
    assert r.json()["user_id"] == "alice"
    assert r.json()["roles"] == []


def test_missing_identity_is_401_not_anonymous():
    """以前是落到 ("anonymous", "default") —— 所有无头请求共享一个身份，
    于是它们互相看得见对方的资源，而且完全静默。"""
    assert _actor_from_headers(**{"X-Tenant-ID": "t1"}).status_code == 401
    assert _actor_from_headers(**{"X-User-ID": "alice"}).status_code == 401
    assert _actor_from_headers().status_code == 401


def test_admin_header_grants_tenant_admin():
    r = _actor_from_headers(**{
        "X-User-ID": "root", "X-Tenant-ID": "t1", "X-User-Role": "admin",
    })
    assert r.json()["is_tenant_admin"] is True


@pytest.mark.parametrize("forged", ["superadmin", "root", "operator"])
def test_unknown_role_header_is_ignored(forged):
    r = _actor_from_headers(**{
        "X-User-ID": "attacker", "X-Tenant-ID": "t1", "X-User-Role": forged,
    })
    assert r.json()["is_platform_admin"] is False
    assert r.json()["is_tenant_admin"] is False
    assert r.json()["roles"] == []


def test_platform_admin_header_from_trusted_gateway_is_accepted():
    r = _actor_from_headers(**{
        "X-User-ID": "root", "X-Tenant-ID": "t1",
        "X-User-Role": "platform_admin",
    })
    assert r.json()["is_platform_admin"] is True
    assert r.json()["roles"] == ["platform_admin"]


def test_role_header_is_case_and_space_insensitive():
    """反代下发的是规范化过的值，但别让大小写差异把真管理员降级。"""
    r = _actor_from_headers(**{
        "X-User-ID": "root", "X-Tenant-ID": "t1", "X-User-Role": "  Admin  ",
    })
    assert r.json()["is_tenant_admin"] is True


def test_untrusted_headers_refuse_service(monkeypatch):
    """没配服务间密钥时整条接口 503。

    那种部署下端口谁都能打、身份头谁都能填，"认出身份"是假的 —— 宁可不可用。
    """
    monkeypatch.delenv("LEA_INTERNAL_TOKEN", raising=False)
    r = _actor_from_headers(**{"X-User-ID": "alice", "X-Tenant-ID": "t1"})
    assert r.status_code == 503


def test_local_escape_hatch_allows_untrusted_headers(monkeypatch):
    monkeypatch.delenv("LEA_INTERNAL_TOKEN", raising=False)
    monkeypatch.setenv("LEA_AUTHZ_ALLOW_UNTRUSTED_HEADERS", "1")
    r = _actor_from_headers(**{"X-User-ID": "alice", "X-Tenant-ID": "t1"})
    assert r.status_code == 200
