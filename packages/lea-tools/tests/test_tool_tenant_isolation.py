"""工具解析必须按租户隔离。

## 这条 bug 长什么样

lea-tools 的 Tool 模型以前**没有 tenant_id**，而 name 是 unique=True。于是：

  - 四处按名字查工具的地方都不带租户过滤（不是忘了，是模型表达不了）
  - 真实表上因此有个全局 UNIQUE(name)，工具名全平台唯一
  - 那个全局唯一**恰好**让那些查询无歧义 —— 全库只有一行叫这个名字

后果：租户 B 执行某个工具名时，拿到的是全库唯一那一行，**可能属于租户 A**。
而工具行里有 endpoint / http_adapter_config / sandbox_config，也就是拿着别人的
配置去执行。

## 为什么单租户测不出来

单租户下"全局唯一那一行"就是自己的那一行，每个查询都返回正确结果。这条 bug 只在
两个租户拥有同名工具时才显形 —— 而在修好之前，那个状态**建不出来**（唯一索引拦着）。
所以下面每个用例都必须建两个租户的同名工具。
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import sessionmaker

from lea_tools.db import Base
from lea_tools.db.models import (
    GLOBAL_TENANT_ID,
    OWNER_SCOPE_PLATFORM,
    OWNER_SCOPE_USER,
    Tool,
)
from lea_tools.services import resolve_tool, visible_tools
from lea_tools.services.registry import list_tools_for_session

T1, T2 = "tenant-a", "tenant-b"


def _platform(tenant, name, **kw):
    return Tool(tenant_id=tenant, name=name, execution_type="api", schema_json={},
                owner_scope=OWNER_SCOPE_PLATFORM, owner_user_id=None, **kw)


def _private(tenant, owner, name, **kw):
    return Tool(tenant_id=tenant, name=name, execution_type="api", schema_json={},
                owner_scope=OWNER_SCOPE_USER, owner_user_id=owner, **kw)


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    s.add_all([
        # 两个租户的同名共享工具，endpoint 不同 —— 拿错行时断言能指出拿到了谁的
        _platform(T1, "web_search", endpoint="https://tenant-a.example/search"),
        _platform(T2, "web_search", endpoint="https://tenant-b.example/search"),
        # 平台内置，所有租户可见
        Tool(tenant_id=GLOBAL_TENANT_ID, name="code_interpreter",
             execution_type="sandbox", schema_json={},
             owner_scope=OWNER_SCOPE_PLATFORM, is_builtin=True),
        # 只有 T1 有
        _platform(T1, "tenant_a_only"),
        # 平台内置的 web_search —— 跟 T1/T2 各自的那个**同名**。
        # 这是"更具体的赢"那一格：它没标 is_builtin，所以租户自己的共享工具压过它。
        _platform(GLOBAL_TENANT_ID, "web_search",
                  endpoint="https://platform-default.example/search"),

        # ── 按人可见那个轴 ──────────────────────────────────────────────
        # alice 和 bob 在同一个租户里各有一个同名私有工具。这是 migrations/007
        # 放开的状态，也是"只按租户过滤"会串行的那一格。
        _private(T1, "alice", "weather", endpoint="https://alice.example/w"),
        _private(T1, "bob", "weather", endpoint="https://bob.example/w"),
        _private(T1, "alice", "alice_only"),
        # 跟内置工具同名的私有工具。加了命名空间之后这**不再是非法状态** ——
        # 它的暴露名是 my__code_interpreter，跟内置那行落在两个命名空间里。
        # 下面几条钉的就是"裸名字进不去私有、带前缀进不去内置"。
        _private(T1, "alice", "code_interpreter",
                 endpoint="https://alice.example/hijack"),
    ])
    s.commit()
    yield s
    s.close()


def _find(db, tenant, name, user=None):
    return visible_tools(db, tenant, user).filter(Tool.name == name).first()


def test_same_name_in_two_tenants_is_allowed(db):
    """前提条件：模型不再声明 name 全局唯一，否则上面的 fixture 就插不进去。

    三行同名 web_search：T1 的、T2 的、平台内置的。
    """
    rows = db.query(Tool).filter(Tool.name == "web_search").all()
    assert len(rows) == 3
    assert {r.tenant_id for r in rows} == {T1, T2, GLOBAL_TENANT_ID}


def test_resolves_own_tenant_tool(db):
    assert _find(db, T1, "web_search").endpoint == "https://tenant-a.example/search"
    assert _find(db, T2, "web_search").endpoint == "https://tenant-b.example/search"


def test_does_not_leak_other_tenants_tool(db):
    """T2 找一个只有 T1 有的工具 → 找不到，而不是拿到 T1 那行。

    这是整条 bug 的核心：拿到 T1 那行意味着用 T1 的 endpoint 去执行。
    """
    assert _find(db, T2, "tenant_a_only") is None
    assert _find(db, T1, "tenant_a_only") is not None


def test_global_tools_visible_to_every_tenant(db):
    """内置工具住在 GLOBAL_TENANT_ID，所以过滤是 IN 而不是 ==。

    这条挂了的表现是 code_interpreter 对所有租户 404 —— 也就是修隔离的时候
    把平台工具一起挡掉了。
    """
    for tenant in (T1, T2, "brand-new-tenant"):
        assert _find(db, tenant, "code_interpreter") is not None


def test_model_no_longer_declares_global_unique_name(db):
    """模型层面的回归：name 上不能再有唯一约束。

    留着的话 create_all() 会在新库上重新造出那个全局唯一索引，
    migrations/005 在生产上删掉的东西会在测试/新环境里复活 —— 那种不一致最难查。
    """
    idx = inspect(db.get_bind()).get_indexes("tools")
    unique_on_name = [
        i for i in idx if i.get("unique") and i["column_names"] == ["name"]
    ]
    assert not unique_on_name, f"name 上又有唯一索引了: {unique_on_name}"


def test_tenant_shared_tool_beats_non_builtin_global_tool(db):
    """同为共享工具时**更具体的赢** —— 本租户那行压过全局那行。

    这一格以前没测到，而它真的坏过：visible_tools 只做 IN 不排序，同名的两行都可见，
    而调用方是不带 ORDER BY 的 .first() —— 取哪行由存储引擎决定。实测全局那行赢，
    于是租户自己配的 endpoint 被平台默认值悄悄盖掉。

    e2e 里的表现是"本该打 mock 的请求打到了真的 serpapi.com"，报错来自远端而不是
    我们的代码 —— 那种失败最难往回追到这一行。

    ⚠️ 注意这跟"覆盖内置工具"不是一回事，见下一条。上一版把两者混成了一条规则
    （"租户可以覆盖平台工具"），那是错的。
    """
    assert _find(db, T1, "web_search").endpoint == "https://tenant-a.example/search"
    assert _find(db, T2, "web_search").endpoint == "https://tenant-b.example/search"


def test_builtin_name_resolves_to_builtin_not_private_tool(db):
    """裸的 `code_interpreter` 解析到内置那行，不会落到 alice 同名的私有工具上。

    alice 那行现在的暴露名是 `my__code_interpreter` —— 命名空间让"遮蔽"从一个
    需要裁决的冲突变成了两个不同的名字。这条钉住裸名字只在共享/内置里找。
    """
    got = resolve_tool(db, T1, "alice", "code_interpreter")
    assert got.is_builtin is True
    assert got.endpoint != "https://alice.example/hijack"


def test_private_tool_needs_the_prefix(db):
    """`my__` 前缀是私有工具唯一的入口，裸名字进不去。"""
    got = resolve_tool(db, T1, "alice", "my__code_interpreter")
    assert got is not None
    assert got.endpoint == "https://alice.example/hijack"
    assert got.is_builtin is False


def test_bare_name_does_not_fall_back_to_private_tool(db):
    """模型漏了前缀 → 404，而不是"猜它想调私有的那个"。

    兜底看着友好，但它把刚拆掉的歧义原样装回来：一旦裸名字能命中私有工具，
    "共享的 weather"和"我的 weather"又变成同一个 key。404 是模型下一轮能自己纠正的
    错误，串到别人的工具上不是。
    """
    assert resolve_tool(db, T1, "alice", "alice_only") is None
    assert resolve_tool(db, T1, "alice", "my__alice_only") is not None


def test_prefix_does_not_cross_users(db):
    """`my__` 是"我的"，不是"某个人的" —— bob 拿这个前缀够不到 alice 的工具。"""
    assert resolve_tool(db, T1, "bob", "my__alice_only") is None
    assert resolve_tool(db, T1, "bob", "my__weather").endpoint == "https://bob.example/w"


def test_exposed_names_in_capability_list(db):
    """清单里发出去的是暴露名，私有的带前缀、共享和内置的不带。"""
    names = {t["name"] for t in list_tools_for_session(db, T1, "alice", "user")}
    assert "my__weather" in names          # alice 自己的
    assert "my__alice_only" in names
    assert "weather" not in names          # 没有共享的 weather
    assert "web_search" in names           # 租户共享的，裸名
    assert "code_interpreter" in names     # 内置，裸名
    assert "my__code_interpreter" in names  # alice 同名的私有工具，两者并存不冲突


# ── 按人可见 ─────────────────────────────────────────────────────────────

def test_two_users_same_tool_name_resolve_to_their_own(db):
    """同租户里 alice 和 bob 各有一个 weather —— 各拿各的。

    只按租户过滤的话这两行同时可见，`.first()` 会在两个人的工具之间随便挑一个：
    alice 的调用打到 bob 的 endpoint，而两个人的租户是同一个，租户隔离一层都察觉不到。
    """
    assert _find(db, T1, "weather", user="alice").endpoint == "https://alice.example/w"
    assert _find(db, T1, "weather", user="bob").endpoint == "https://bob.example/w"


def test_private_tool_invisible_to_another_user(db):
    assert _find(db, T1, "alice_only", user="alice") is not None
    assert _find(db, T1, "alice_only", user="bob") is None


def test_no_user_id_sees_only_platform_tools(db):
    """身份缺失时 fail-closed：只能看到平台工具，看不到任何人的私有工具。

    反过来（把"没有身份"当成通配）就是一个漏所有人私有工具的洞，而且它只在
    调用方忘记传 X-User-ID 时才显形 —— 那种洞不会有人报障。
    """
    assert _find(db, T1, "weather") is None
    assert _find(db, T1, "web_search") is not None


def test_registry_excludes_other_users_private_tools(db):
    """能力清单是发给引擎的 —— 别人的工具名和 schema 不能进另一个人的上下文。"""
    names = {t["name"] for t in list_tools_for_session(db, T1, "bob", "user")}
    assert "my__alice_only" not in names
    assert "my__weather" in names  # bob 自己那个（同名不同人，各看各的）


def test_platform_tool_used_when_tenant_has_no_override(db):
    """反向：没自定义过的租户拿到平台那行。覆盖不能变成"挡住"。"""
    got = _find(db, "brand-new-tenant", "web_search")
    assert got is not None
    assert got.endpoint == "https://platform-default.example/search"


def test_registry_does_not_list_other_tenants_tools(db):
    """能力清单也要按租户过滤 —— 这是第五处漏掉租户的查询。

    registry.list_tools_for_session 以前是裸的 db.query(Tool)，于是发给引擎的清单里
    有**所有**租户的工具名和 schema。前四处漏的后果是"执行时拿错行"，这一处漏的是
    "存在哪些工具"直接泄漏出去 —— schema 里常有业务字段名。
    """
    from lea_tools.services.registry import list_tools_for_session

    names = {t["name"] for t in list_tools_for_session(db, T2, None, "user")}
    assert "tenant_a_only" not in names, "看到了 T1 独有的工具"
    assert "web_search" in names          # 自己的
    assert "code_interpreter" in names    # 平台的


def test_registry_dedups_shadowed_tool_names(db):
    """同名工具在清单里只能出现一次。

    引擎按名字派发工具，收到两个同名的就是一个不确定该调哪个的状态。
    """
    tools = list(list_tools_for_session(db, T1, "alice", "user"))
    names = [t["name"] for t in tools]
    assert names.count("web_search") == 1, f"同名工具出现多次: {names}"
    assert names.count("code_interpreter") == 1, f"同名工具出现多次: {names}"


def test_check_permission_denies_cross_tenant_tool(db):
    """check_permission 找不到工具时返 False，所以跨租户会在 403 那一层就被拦住，
    走不到 execute 的查询。两道都要对。"""
    from lea_tools.services import check_permission

    assert check_permission(db, T2, None, "user", None, "tenant_a_only") is False
    assert check_permission(db, T1, None, "user", None, "tenant_a_only") is True


def test_check_permission_denies_another_users_private_tool(db):
    """跨用户也要在 403 那一层就拦住，走不到 execute 的查询。"""
    from lea_tools.services import check_permission

    assert check_permission(db, T1, "bob", "user", None, "my__alice_only") is False
    assert check_permission(db, T1, "alice", "user", None, "my__alice_only") is True


def test_resolve_tool_warns_on_shadowed_name(db):
    """两行**平台**工具同名 —— 收敛可以，静默不行。

    加了命名空间之后这是唯一还会真撞的一种：全局的 web_search 和 T1 共享的
    web_search 都不带前缀。（"私有工具遮蔽内置"那一类现在构造不出来了。）

    静默选一行的症状会以"我改了工具配置但没生效"的形式出现在几周之后，那时已经
    没法往回追。所以 resolve_tool 在收敛的那一刻必须把候选行都报出来。

    ⚠️ 用 structlog 的 capture_logs，不能用 pytest 的 caplog：structlog 这边配的是
    直接渲染到 stdout，不经过 stdlib logging，caplog.text 会是空的 —— 日志明明打了，
    断言却红，那是个会让人去改被测代码的假失败。
    """
    from structlog.testing import capture_logs

    with capture_logs() as logs:
        got = resolve_tool(db, T1, "alice", "web_search")

    assert got.endpoint == "https://tenant-a.example/search"
    shadowed = [e for e in logs if e["event"] == "tool_name_shadowed"]
    assert shadowed, "同名被静默收敛了"
    # 报出来的东西要够定位：解析到哪一行 + 候选都是谁
    assert shadowed[0]["resolved_id"] == got.id
    assert {c["tenant"] for c in shadowed[0]["candidates"]} == {T1, GLOBAL_TENANT_ID}


def test_resolve_tool_silent_when_unambiguous(db):
    """反向：正常情况下不能刷这条日志，否则它会被当成噪音忽略掉。

    `my__code_interpreter` 特意选了个跟内置同名的 —— 加前缀之后它跟内置那行落在
    两个命名空间里，不该再被算成同名。
    """
    from structlog.testing import capture_logs

    with capture_logs() as logs:
        resolve_tool(db, T1, "alice", "my__code_interpreter")
    assert not [e for e in logs if e["event"] == "tool_name_shadowed"]
