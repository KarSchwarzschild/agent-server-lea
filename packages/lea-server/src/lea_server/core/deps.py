"""FastAPI 依赖注入。"""

from __future__ import annotations

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session, joinedload

from lea_server.core.auth import decode_token
from lea_server.core.tenant import TenantContext, get_or_create_tenant_config, get_tenant
from lea_server.database import get_db
from lea_server.models.models import User

security = HTTPBearer()


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security),
    db: Session = Depends(get_db),
) -> User:
    """获取当前认证用户。"""
    token_data = decode_token(credentials.credentials)
    if token_data is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的认证凭证",
        )

    user = (
        db.query(User)
        .options(joinedload(User.roles))
        .filter(User.user_id == token_data.user_id)
        .first()
    )

    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在或已禁用",
        )

    return user


def require_roles(*roles: str):
    """角色校验依赖工厂。

    用法：
        # 路由级依赖
        @router.get("/xxx", dependencies=[Depends(require_roles("admin"))])

        # 参数依赖（可拿到 user）
        async def handler(user: User = Depends(require_roles("admin"))): ...

        # 预定义
        admin_required = require_roles("admin")
    """
    required = set(roles)

    def _check(user: User = Depends(get_current_user)) -> User:
        user_roles = {r.code for r in user.roles}
        if not user_roles & required:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"需要以下角色之一: {', '.join(sorted(required))}",
            )
        return user

    return _check


admin_required = require_roles("admin")


def get_current_tenant() -> TenantContext:
    """获取当前请求的租户上下文（由 TenantMiddleware 注入）。"""
    return get_tenant()


def get_tenant_config_dep(
    db: Session = Depends(get_db),
    tenant: TenantContext = Depends(get_current_tenant),
):
    """获取当前租户的配置（不存在时自动创建默认配置）。"""
    return get_or_create_tenant_config(db, tenant.tenant_id)
