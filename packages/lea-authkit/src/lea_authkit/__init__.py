"""LEA 服务间鉴权 —— 所有 Python 服务共用。

被调方：

    from lea_authkit import install_internal_auth
    install_internal_auth(app, service="lea-rag")   # 放在模块最后

调用方：

    from lea_authkit import internal_headers
    await client.post(url, json=payload, headers=internal_headers())

为什么内网也要挡、为什么不是 JWT、为什么没配密钥时放行 —— 见 internal_auth.py 顶部。
"""

from lea_authkit.authz import (
    DELETE,
    READ,
    ROLE_ADMIN,
    ROLE_PLATFORM_ADMIN,
    UPDATE,
    Actor,
    Ownership,
    can,
    ensure_tenant,
    require,
    require_actor,
    require_admin,
    scoped_filter,
)
from lea_authkit.internal_auth import (
    EXEMPT_PATHS,
    HEADER,
    install_internal_auth,
    internal_headers,
)

__all__ = [
    # 服务间认证（你是不是我们自己的服务）
    "EXEMPT_PATHS",
    "HEADER",
    "install_internal_auth",
    "internal_headers",
    # 用户授权（这个人能对这个对象做什么）
    "Actor",
    "Ownership",
    "require_actor",
    "can",
    "require",
    "require_admin",
    "ensure_tenant",
    "scoped_filter",
    "READ",
    "UPDATE",
    "DELETE",
    "ROLE_ADMIN",
    "ROLE_PLATFORM_ADMIN",
]
