"""lea-rag 的 /admin/* 必须**两道**门都有：角色 + 租户。

## 为什么这个文件存在

`server_admin_kb.py` 有一版把 `tenant_id` 从 query 参数改成了从 actor 取 ——
跨租户那半修对了，但没加角色检查。于是普通用户 alice 可以
`DELETE /admin/knowledge-bases/{id}` 删掉同租户里 bob 的知识库。
那个文件甚至 `import` 了 `ROLE_ADMIN` 却一次没用。

这类漏洞的特征是**没有症状**：端点照常工作，只是对不该开放的人也开放。所以只能靠
测试盯着。下面按"每个 admin 端点，用普通用户身份打一遍，必须 403"来穷举 ——
它会自动覆盖将来新增的端点，不需要有人记得补用例。
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from lea_authkit import Actor, require_admin
from lea_authkit.authz import ROLE_ADMIN

alice = Actor("alice", "t1")
admin = Actor("root", "t1", (ROLE_ADMIN,))


# ── require_admin 本身 ───────────────────────────────────────────────────

def test_require_admin_rejects_plain_user():
    with pytest.raises(HTTPException) as e:
        require_admin(alice)
    assert e.value.status_code == 403


def test_require_admin_allows_tenant_admin():
    require_admin(admin)   # 不抛


# ── 每个 admin 端点都必须挂上门 ──────────────────────────────────────────
#
# 这是结构性检查，不是逐个端点的行为测试：把 router 上所有路由列出来，确认每条都
# 走得到角色门。两种挂法都算：router 级 dependencies，或函数体里调 require_admin。


def _admin_routers():
    from lea_rag.server_admin_kb import router as kb_router
    from lea_rag.server_admin_memory import router as mem_router

    return [("admin_kb", kb_router), ("admin_memory", mem_router)]


def _router_has_dependency(router, func_name: str) -> bool:
    return any(
        getattr(d.dependency, "__name__", "") == func_name
        for d in (router.dependencies or [])
    )


@pytest.mark.parametrize("name,router", _admin_routers())
def test_every_admin_route_is_role_gated(name, router):
    """每条 /admin 路由都必须过角色门。

    router 级依赖满足了就整个 router 通过；否则逐个函数检查源码里有没有
    require_admin 调用。
    """
    import inspect

    if _router_has_dependency(router, "_admin_only"):
        return   # router 级挂了，覆盖所有现有和将来的端点

    ungated = []
    for route in router.routes:
        endpoint = getattr(route, "endpoint", None)
        if endpoint is None:
            continue
        try:
            src = inspect.getsource(endpoint)
        except OSError:      # pragma: no cover
            continue
        if "require_admin(" not in src:
            ungated.append(f"{sorted(route.methods)} {route.path}")

    assert not ungated, (
        f"{name} 这些 admin 端点没有角色门（普通用户也能调）:\n  "
        + "\n  ".join(ungated)
        + "\n\n两种修法：函数体里 require_admin(actor)，或者给整个 router 挂 "
          "dependencies=[Depends(_admin_only)]（推荐 —— 新端点自动继承）。"
    )


@pytest.mark.parametrize("name,router", _admin_routers())
def test_every_admin_route_takes_an_actor(name, router):
    """每条路由都得能拿到 actor —— 没有 actor 就没法做租户过滤。

    router 级角色门挡住了普通用户，但**挡不住跨租户**：租户管理员在自己租户里是
    合法的 admin，如果端点不按 actor.tenant_id 过滤，他就能操作别租户的对象。
    两道门是正交的，这条盯的是第二道的前提。
    """
    import inspect

    missing = []
    for route in router.routes:
        endpoint = getattr(route, "endpoint", None)
        if endpoint is None:
            continue
        # 注意比的是注解的**字符串**：被测模块用了 from __future__ import
        # annotations，所以 p.annotation 是 "Actor" 而不是 Actor 这个类，
        # `is Actor` 永远为假（第一版就是这么写的，六个端点全部误报）。
        params = inspect.signature(endpoint).parameters
        if not any(
            p.annotation in (Actor, "Actor") for p in params.values()
        ):
            missing.append(f"{sorted(route.methods)} {route.path}")

    assert not missing, (
        f"{name} 这些端点拿不到 actor，因此不可能做租户过滤:\n  "
        + "\n  ".join(missing)
    )


@pytest.mark.parametrize("name,router", _admin_routers())
def test_admin_routes_do_not_take_tenant_id_from_request(name, router):
    """tenant_id 不能是请求参数。

    原来的 list_all_kbs 签名是 `tenant_id: Optional[str] = None` —— 不传就返回
    全平台。这条防止它以任何形式回来（包括"为了方便调试"临时加回去）。
    """
    import inspect

    offenders = []
    for route in router.routes:
        endpoint = getattr(route, "endpoint", None)
        if endpoint is None:
            continue
        for pname, p in inspect.signature(endpoint).parameters.items():
            if pname in ("tenant_id", "x_tenant_id"):
                offenders.append(f"{sorted(route.methods)} {route.path} 的 {pname}")

    assert not offenders, (
        f"{name} 这些端点从请求里取 tenant_id，应该从 actor 取:\n  "
        + "\n  ".join(offenders)
    )
