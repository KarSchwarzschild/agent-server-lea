"""用户上下文 helper —— 委托给 lea-authkit 的 require_actor。

用法不变：
    from lea_config.core.deps import UserCtx, get_user_ctx

    @router.get("/foo")
    def foo(ctx: UserCtx = Depends(get_user_ctx)):
        ctx.user_id  ctx.tenant_id

跟旧版的区别：缺 X-User-ID / X-Tenant-ID 头时**不再兜底 anonymous**，
而是 401（见 lea_authkit.authz._TrustedHeaderActor）。没有服务间鉴权
（LEA_INTERNAL_TOKEN 未配置）时 503，防止"谁都能自称任何人"。
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends
from lea_authkit import Actor, require_actor


@dataclass
class UserCtx:
    user_id: str
    tenant_id: str


def get_user_ctx(actor: Actor = Depends(require_actor)) -> UserCtx:
    """从 lea-authkit 拿到的 Actor 构造 UserCtx。

    FastAPI 解析 Depends(require_actor) 时：
    - 缺头 → 401
    - LEA_INTERNAL_TOKEN 未配 → 503（除非设 LEA_AUTHZ_ALLOW_UNTRUSTED_HEADERS=1）
    - 正常 → 返回 Actor(user_id, tenant_id, roles)
    """
    return UserCtx(user_id=actor.user_id, tenant_id=actor.tenant_id)
