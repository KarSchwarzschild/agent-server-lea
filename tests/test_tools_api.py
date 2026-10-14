"""lea-config 的 /tools —— 归属决定权限，以及**写的列执行器要读得懂**。

这个文件盯两类回归，第二类是这轮真正的坑：

    授权    普通用户能建自己的工具；改不了别人的；跨租户是 404 不是 403
    契约    写进去的列名必须是执行器读的那套（enabled / schema_json /
            execution_type），不是 lea-config 模型以前自己发明的
            （is_enabled / input_schema）

为什么第二类需要测：`tools` 表被三个服务各定义一遍，真实表是并集，两组列**都存在**。
所以写错一组不会报错 —— 存得下、列得出、一执行就找不到 schema。那种坏比 500 难查
得多，而它只能靠"断言列名"这种看着很笨的测试钉住。
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from lea_config.api.tools import ToolCreate
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

T1, T2 = "tools-t1", "tools-t2"


@pytest.fixture
def client(monkeypatch):
    """真 FastAPI + in-memory SQLite，走完整依赖链（含 require_actor 的头解析）。"""
    monkeypatch.setenv("LEA_INTERNAL_TOKEN", "tok")
    monkeypatch.delenv("LEA_AUTHZ_ALLOW_UNTRUSTED_HEADERS", raising=False)

    import lea_config.models  # noqa: F401 — 让 Base.metadata 收齐外键指向的表
    from lea_config.api.tools import router

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
    # 内置工具没有接口能建（这正是被测的规则），所以需要直接落库。挂在 client 上
    # 而不是再开一个 fixture —— 两个 fixture 各自 create_engine 会拿到两个互不相干
    # 的 in-memory 库，那种失败看起来像"数据没写进去"。
    tc.Session = Session
    return tc


def seed_builtin(client, name: str, tenant: str = DEFAULT_TENANT_ID) -> int:
    """种一行内置工具，模拟 lea-tools 的 seed.py 干的事。"""
    from lea_config.models.tool import Tool

    db = client.Session()
    try:
        tool = Tool(
            tenant_id=tenant,
            name=name,
            execution_type="internal",
            schema_json={"type": "object"},
            owner_scope=OWNER_SCOPE_PLATFORM,
            owner_user_id=None,
            is_builtin=True,
        )
        db.add(tool)
        db.commit()
        return tool.id
    finally:
        db.close()


def h(user: str, tenant: str = T1, role: str | None = None) -> dict:
    headers = {"X-User-ID": user, "X-Tenant-ID": tenant}
    if role:
        headers["X-User-Role"] = role
    return headers


VALID = {
    "name": "my_search",
    "execution_type": "api",
    "endpoint": "https://example.test/s",
    "schema_json": {"type": "object"},
}


def mk(client, user="alice", tenant=T1, role=None, **over):
    body = {**VALID, **over}
    return client.post("/tools", json=body, headers=h(user, tenant, role))


# ── 契约：写的列执行器要读得懂 ────────────────────────────────────────────

def test_create_persists_executor_columns(client):
    """schema_json / execution_type / enabled 必须落到响应里。

    执行器读的就是这三个（lea-tools 的 services/__init__.py 和 registry.py）。
    """
    r = mk(client)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["schema_json"] == {"type": "object"}
    assert body["execution_type"] == "api"
    assert body["enabled"] is True


def test_create_does_not_write_dead_columns(client):
    """以前那三列（is_enabled / input_schema / source）不该再出现。

    它们由 migrations/006 从表上删掉；在那之前这条测试挡住"又有人写回去"。

    ⚠️ `is_builtin` 曾经在这个名单里，**那是判断错了**。它不是跟 enabled 重复的死列，
    它标的是"实现在代码里"（lea-tools 的 seed.py 定义的 5 个内置工具，执行走
    INTERNAL_ROUTES / 硬编码沙箱适配）。当时把它当死列，直接后果是 lea-config 的模型
    不映射它，于是 PUT/DELETE 读不到这个标志 —— 每个租户管理员都能改掉平台内置工具的
    定义。现在它是活列，下面几条钉的就是那道门。
    """
    body = mk(client).json()
    for dead in ("is_enabled", "input_schema", "source"):
        assert dead not in body, f"{dead} 又回来了 —— 执行器不读它"


def test_user_created_tool_is_not_builtin(client):
    """用户建的工具永远不是内置的 —— 内置只能由 seed.py 生产。

    ToolCreate 里没有这个字段，所以传不进来；这条同时钉住"以后有人加了字段"。
    """
    assert mk(client).json()["is_builtin"] is False
    assert "is_builtin" not in ToolCreate.model_fields


def test_missing_schema_json_is_422_not_500(client):
    """缺必填字段要在校验层挡住。以前这里是 NOT NULL 冲突 → 500。"""
    r = client.post(
        "/tools",
        json={"name": "x", "execution_type": "api"},
        headers=h("alice"),
    )
    assert r.status_code == 422


@pytest.mark.parametrize("blocked", ["sandbox", "internal", "local"])
def test_platform_only_execution_types_rejected(client, blocked):
    """三种平台内部类型不能由外部指定。

    sandbox 尤其要挡：到 Go 侧走 DynamicTool，而它 IsConcurrencySafe() 硬编码 true，
    于是引擎会并行调一个持有跨调用沙箱状态的工具。DB 的 ENUM 拦不住这个 —— 那是个
    **合法值**，只是不该由外部填。
    """
    r = mk(client, execution_type=blocked)
    assert r.status_code == 422, f"{blocked} 被接受了"


# ── 归属：谁建的谁能改 ───────────────────────────────────────────────────

def test_plain_user_creates_user_owned_tool(client):
    """普通用户建工具是正常功能，不是管理员特权。"""
    body = mk(client, user="alice").json()
    assert body["owner_scope"] == OWNER_SCOPE_USER
    assert body["owner_user_id"] == "alice"


def test_admin_creates_platform_owned_tool(client):
    body = mk(client, user="root", role="admin").json()
    assert body["owner_scope"] == OWNER_SCOPE_PLATFORM
    assert body["owner_user_id"] is None


def test_ownership_cannot_be_set_by_caller(client):
    """传 owner_scope / owner_user_id 都不该生效 —— 那是自助提权成"全租户可用"。

    防线有两道，这条测两道：ToolCreate 里没有这两个字段（pydantic 直接丢掉），
    而且 create_tool 是从 actor 算归属、不看 body。任一道单独成立就够，但两道都在
    的时候"以后有人给 ToolCreate 加了字段"才不会立刻变成提权。
    """
    body = mk(
        client,
        user="alice",
        owner_scope=OWNER_SCOPE_PLATFORM,
        owner_user_id="root",
    ).json()
    assert body["owner_scope"] == OWNER_SCOPE_USER
    assert body["owner_user_id"] == "alice"

    # 显式钉住第一道：schema 上就没有这两个字段。加字段的人会看到这条测试失败，
    # 那时候必须同时确认 create_tool 仍然不读 body 的归属。
    assert "owner_scope" not in ToolCreate.model_fields
    assert "owner_user_id" not in ToolCreate.model_fields


def test_owner_can_update_own_tool(client):
    tid = mk(client, user="alice").json()["id"]
    r = client.put(f"/tools/{tid}", json={"description": "x"}, headers=h("alice"))
    assert r.status_code == 200


def test_other_user_cannot_update_private_tool(client):
    """同租户里别人的私有工具 —— 403。"""
    tid = mk(client, user="alice").json()["id"]
    r = client.put(f"/tools/{tid}", json={"description": "hijack"}, headers=h("bob"))
    assert r.status_code == 403


def test_other_user_cannot_delete_private_tool(client):
    tid = mk(client, user="alice").json()["id"]
    assert client.delete(f"/tools/{tid}", headers=h("bob")).status_code == 403


def test_admin_can_update_platform_tool(client):
    tid = mk(client, user="root", role="admin").json()["id"]
    r = client.put(f"/tools/{tid}", json={"enabled": False}, headers=h("root", role="admin"))
    assert r.status_code == 200
    assert r.json()["enabled"] is False


def test_plain_user_cannot_update_platform_tool(client):
    tid = mk(client, user="root", role="admin").json()["id"]
    r = client.put(f"/tools/{tid}", json={"enabled": False}, headers=h("alice"))
    assert r.status_code == 403


# ── 租户隔离 ─────────────────────────────────────────────────────────────

def test_cross_tenant_update_is_404_not_403(client):
    """403 会确认这个 id 存在 —— 跨租户不能给出这个信息。"""
    tid = mk(client, user="alice", tenant=T1).json()["id"]
    r = client.put(f"/tools/{tid}", json={"description": "x"}, headers=h("alice", T2))
    assert r.status_code == 404


def test_same_name_allowed_in_different_tenants(client):
    """全局 UNIQUE(name) 被 migrations/005 删掉了，这条钉住不许它回来。"""
    assert mk(client, tenant=T1).status_code == 201
    assert mk(client, tenant=T2).status_code == 201


def test_duplicate_name_for_same_user_is_409(client):
    """同一个人建两个同名 —— 不拦的话是 IntegrityError 500。"""
    assert mk(client, user="alice", tenant=T1).status_code == 201
    assert mk(client, user="alice", tenant=T1).status_code == 409


# ── 同名规则：谁不能跟谁撞 ───────────────────────────────────────────────

def test_two_users_can_have_the_same_tool_name(client):
    """migrations/007 放开的正是这一格 —— 也是唯一放开的一格。

    以前是 UNIQUE(tenant_id, name)：同租户里 alice 占了 weather，bob 就永远建不了。
    那比产品语义严 —— 工具是用户自己的东西，两个人各有一个同名私有工具很正常，
    而且谁都不会同时看见这两行（visible_tools 按人过滤）。
    """
    assert mk(client, user="alice", name="weather").status_code == 201
    assert mk(client, user="bob", name="weather").status_code == 201


def test_shared_tool_cannot_take_a_builtin_name(client):
    """管理员建的共享工具撞上内置工具名 —— 拒。

    共享工具和内置工具**都不带前缀**，所以它们之间会真撞。而内置工具住在
    DEFAULT_TENANT_ID，跨租户 —— 唯一索引根本碰不到这两行，只能在接口层挡。
    这是"不能覆盖系统底层工具"唯一还能发生的地方。
    """
    seed_builtin(client, "kb_search")
    r = mk(client, user="root", role="admin", tenant=T1, name="kb_search")
    assert r.status_code == 409
    assert "内置" in r.json()["detail"]


def test_private_tool_may_take_a_builtin_name(client):
    """用户私有工具**可以**叫 kb_search —— 它在清单里是 my__kb_search，不冲突。

    这条以前是拒的。加了命名空间之后再拒就是无谓的限制：用户根本遮蔽不了内置工具，
    禁止他用这个词毫无收益。
    """
    seed_builtin(client, "kb_search")
    assert mk(client, user="alice", tenant=T1, name="kb_search").status_code == 201


def test_private_and_shared_tool_may_share_a_name(client):
    """my__weather 和 weather 不是同一个名字 —— 两个方向都放行。

    这一格以前是**双向拒**的，其中挡住管理员那一侧尤其别扭：一个用户占了名字，
    管理员就再也建不了同名共享工具。加前缀之后这个取舍整个消失了 —— 不需要决定
    谁赢，因为它们压根不同名。
    """
    assert mk(client, user="root", role="admin", name="weather").status_code == 201
    assert mk(client, user="alice", name="weather").status_code == 201

    # 反向同样成立（先有私有的，管理员再建共享的）
    assert mk(client, user="bob", name="stock").status_code == 201
    assert mk(client, user="root", role="admin", name="stock").status_code == 201


def test_duplicate_shared_name_is_409(client):
    """租户内的共享工具之间仍然唯一 —— 它们都是裸名。"""
    assert mk(client, user="root", role="admin", name="weather").status_code == 201
    r = mk(client, user="admin2", role="admin", name="weather")
    assert r.status_code == 409
    assert "共享工具" in r.json()["detail"]


# ── 内置工具：定义不可改不可删 ───────────────────────────────────────────

def test_builtin_tool_cannot_be_updated_even_by_admin(client):
    """403 而不是 404：这行确实存在，说"改不了"是准确的。

    管理员也不行 —— 内置工具的归属是 platform，光靠归属判断挡不住租户管理员。
    """
    tid = seed_builtin(client, "kb_search", tenant=T1)
    r = client.put(
        f"/tools/{tid}", json={"endpoint": "https://evil.test"},
        headers=h("root", T1, "admin"),
    )
    assert r.status_code == 403


def test_builtin_tool_cannot_be_deleted_even_by_admin(client):
    tid = seed_builtin(client, "kb_search", tenant=T1)
    r = client.delete(f"/tools/{tid}", headers=h("root", T1, "admin"))
    assert r.status_code == 403


def test_cross_tenant_builtin_probe_is_404_not_403(client):
    """顺序回归：先 require 再拦内置。

    反过来的话，别的租户拿一个 id 去探会收到 403"内置工具不可修改" —— 等于确认了
    这一行存在，而 require 有意让跨租户是 404。
    """
    tid = seed_builtin(client, "kb_search", tenant=T1)
    r = client.put(f"/tools/{tid}", json={"description": "x"}, headers=h("root", T2, "admin"))
    assert r.status_code == 404


def test_list_excludes_other_users_private_tools(client):
    mk(client, user="alice", name="alice_tool")
    mk(client, user="bob", name="bob_tool")
    names = {t["name"] for t in client.get("/tools", headers=h("alice")).json()["items"]}
    assert names == {"alice_tool"}


def test_list_includes_platform_tools(client):
    mk(client, user="root", role="admin", name="shared")
    mk(client, user="alice", name="mine")
    names = {t["name"] for t in client.get("/tools", headers=h("alice")).json()["items"]}
    assert names == {"shared", "mine"}


def test_list_excludes_other_tenants(client):
    mk(client, user="alice", tenant=T1, name="t1_tool")
    mk(client, user="alice", tenant=T2, name="t2_tool")
    names = {t["name"] for t in client.get("/tools", headers=h("alice", T1)).json()["items"]}
    assert names == {"t1_tool"}


def test_list_count_matches_items(client):
    """分页的前提。逐行 can() 过滤做不到这一点。"""
    for i in range(3):
        mk(client, user="alice", name=f"tool_{i}")
    mk(client, user="bob", name="bobs")
    body = client.get("/tools", headers=h("alice")).json()
    assert body["total"] == len(body["items"]) == 3


def test_no_identity_is_401(client):
    assert client.get("/tools").status_code == 401


def test_unique_index_survives_create_all(client):
    """模型声明的唯一索引必须真的建出来，不能只在 MySQL 上成立。

    第三列是个函数表达式（COALESCE(owner_user_id, '')）。create_all() 对付不了
    某个方言的表达式索引时**不会报错**，只是不建 —— 于是新环境比生产少一条约束，
    而所有测试都还是绿的（应用层的 409 会先拦住）。这条钉住那个静默差异。

    ⚠️ 不能用 `inspect(...).get_indexes()` 查：表达式索引**反射不回来**
    （SQLAlchemy 直接 warn "Skipped unsupported reflection of expression-based
    index" 然后跳过）。用它查会得到"索引不存在"这个错误结论 —— 反射读不到不等于
    没建出来。所以直接问 sqlite_master 要 DDL。
    """
    from sqlalchemy import text

    db = client.Session()
    try:
        ddl = db.execute(
            text("SELECT sql FROM sqlite_master WHERE type='index' AND name=:n"),
            {"n": "uq_tools_owner_name"},
        ).scalar()
    finally:
        db.close()
    assert ddl, "唯一索引没建出来"
    assert "UNIQUE" in ddl.upper()
    assert "coalesce" in ddl.lower(), f"第三列不是那个表达式: {ddl}"


def test_list_excludes_global_tenant_builtins(client):
    """内置工具由 lea-tools 默认加载，/tools 只管理动态工具。"""
    seed_builtin(client, "kb_search")  # 落在 DEFAULT_TENANT_ID
    mk(client, user="alice", name="mine")

    names = {t["name"] for t in client.get("/tools", headers=h("alice")).json()["items"]}
    assert names == {"mine"}
