"""Admin 用户与角色管理 API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session, joinedload

from lea_server.core.deps import admin_required, get_current_user
from lea_server.database import get_db
from lea_server.models.models import Role, User

router = APIRouter(prefix="/admin", tags=["admin-users"])


@router.get("/roles")
async def list_roles(
    db: Session = Depends(get_db),
    _: User = Depends(admin_required),
):
    """所有可用角色。"""
    roles = db.query(Role).filter(Role.is_active == True).all()  # noqa: E712
    return {
        "roles": [
            {"id": r.id, "code": r.code, "name": r.name, "description": r.description or "", "is_system": r.is_system}
            for r in roles
        ]
    }


@router.get("/users")
async def list_users(
    page: int = 1,
    size: int = 50,
    db: Session = Depends(get_db),
    _: User = Depends(admin_required),
):
    """用户列表（含角色信息）。"""
    query = db.query(User).options(joinedload(User.roles))
    total = query.count()
    users = query.order_by(User.id).offset((page - 1) * size).limit(size).all()
    return {
        "users": [
            {
                "id": u.id,
                "user_id": u.user_id,
                "username": u.username,
                "email": u.email or "",
                "is_active": u.is_active,
                "roles": [{"code": r.code, "name": r.name} for r in u.roles],
                "created_at": u.created_at.isoformat() if u.created_at else None,
                "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
            }
            for u in users
        ],
        "total": total,
        "page": page,
        "size": size,
    }


class AssignRoleRequest(BaseModel):
    role_code: str


@router.post("/users/{user_id}/roles")
async def assign_role(
    user_id: str,
    req: AssignRoleRequest,
    db: Session = Depends(get_db),
    _: User = Depends(admin_required),
):
    """给用户分配角色。"""
    user = db.query(User).options(joinedload(User.roles)).filter(User.user_id == user_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    role = db.query(Role).filter(Role.code == req.role_code).first()
    if role is None:
        raise HTTPException(status_code=404, detail=f"角色 '{req.role_code}' 不存在")

    if role in user.roles:
        return {"message": f"用户已拥有角色 '{req.role_code}'"}

    user.roles.append(role)
    db.commit()
    return {
        "message": f"已为用户 {user.username} 分配角色 {role.name}",
        "roles": [{"code": r.code, "name": r.name} for r in user.roles],
    }


@router.delete("/users/{user_id}/roles/{role_code}")
async def remove_role(
    user_id: str,
    role_code: str,
    db: Session = Depends(get_db),
    _: User = Depends(admin_required),
):
    """移除用户角色。"""
    user = db.query(User).options(joinedload(User.roles)).filter(User.user_id == user_id).first()
    if user is None:
        raise HTTPException(status_code=404, detail="用户不存在")

    role = db.query(Role).filter(Role.code == role_code).first()
    if role is None:
        raise HTTPException(status_code=404, detail=f"角色 '{role_code}' 不存在")

    if role not in user.roles:
        return {"message": f"用户没有角色 '{role_code}'"}

    user.roles.remove(role)
    db.commit()
    return {
        "message": f"已移除用户 {user.username} 的角色 {role.name}",
        "roles": [{"code": r.code, "name": r.name} for r in user.roles],
    }
