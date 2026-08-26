"""鉴权 — JWT 验证 + tenant/user 提取。"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import Header, HTTPException
from jose import JWTError, jwt

from lea_parser.config import get_service_settings


@dataclass
class CurrentUser:
    user_id: str
    tenant_id: str
    role: str = "user"


def get_current_user(authorization: str = Header("")) -> CurrentUser:
    """从 Bearer token 解析用户身份。支持内部服务直接传 X-Tenant-Id。"""
    if not authorization:
        raise HTTPException(401, "Missing authorization header")

    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(401, "Empty token")

    settings = get_service_settings()
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except JWTError:
        raise HTTPException(401, "Invalid token")

    user_id = payload.get("sub") or payload.get("user_id", "")
    tenant_id = payload.get("tenant_id", "default")
    role = payload.get("role", "user")

    if not user_id:
        raise HTTPException(401, "Token missing user identity")

    return CurrentUser(user_id=user_id, tenant_id=tenant_id, role=role)
