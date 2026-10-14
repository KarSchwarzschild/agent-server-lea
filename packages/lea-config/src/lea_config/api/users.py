"""User / Role 管理路由。

⚠️ **这个文件很可能整个都该删掉。** 身份、租户成员关系、租户内角色现在全部由
`lea-gateway` 从**外部**取，这三张表在真实链路上一次都不被读：

    外部 OIDC JWT          → lea-gateway 用 JWKS 验签（RS256 + issuer）
    X-Selected-Tenant-ID   → 拿 Authorization 去问外部 TenantUser/CurrUserTenants
                             （同时校验成员身份和 Disabled）
    外部返回的 role        → mapTenantRole() 收敛成 platform_admin / admin / user
                           → 下发 X-User-ID / X-Tenant-ID / X-User-Role

平台这边只负责**接受和验证**。`users` / `roles` / `user_roles` 是第二份、更弱、
而且已经不被读的真源。

## 留着它的代价不是零

这里有一条**提权链**（今天够不着，见下）：

    ① POST /roles {"code": "platform_admin"}      门是 require_admin → 租户管理员通过
    ② POST /users/{自己}/roles {"platform_admin"}  同一道门 → 通过

`roles` 表没有 tenant_id，是全平台一份定义，却由租户管理员管着。而 platform_admin
在十几处绕过租户隔离（`if not actor.is_platform_admin and ... != actor.tenant_id`）。

`is_system` 挡不住：现网 admin / user 两行的 `is_system` 是 **NULL**，
`if role.is_system:` 恒假 —— 内置角色本身也能被改被删。

## 为什么今天够不着

`/users` 和 `/roles` **没有任何外部入口**：

    lea-gateway   mux 里只有 agents / skills / tools / knowledge-bases /
                  memories / chat / sessions / files —— 没有这两个
    lea-agent     routes.go 里没有对应的反代
    frontend-admin  调的是 /api/admin/roles，那是**老 lea-server** 的路径

所以它今天只对能直连 lea-config:8500 的调用方开放，而那由 internal_auth +
NetworkPolicy 挡着。是个**潜伏**的洞：谁哪天把这两个路由接到网关上，它就活了。

正确的处理是删掉整组路由（跟 lea-tools 那四组裸奔的 /admin/* 同样处理），而不是
给一个不该存在的东西加门。删之前要确认后台控制台的迁移计划里不需要它。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from lea_authkit import Actor, require_actor
from pydantic import BaseModel
from sqlalchemy.orm import Session, joinedload

from lea_config.api._auth_helpers import require_admin
from lea_config.database import get_db
from lea_config.models.user import Role, User

users_router = APIRouter(prefix="/users", tags=["users"])
roles_router = APIRouter(prefix="/roles", tags=["roles"])

# 兼容旧引用
router = users_router


# ── Schemas ─────────────────────────────────────────────

class UserUpdate(BaseModel):
    email: str | None = None
    avatar: str | None = None
    phone: str | None = None
    is_active: bool | None = None


class AssignRoleRequest(BaseModel):
    role_code: str


class RoleCreate(BaseModel):
    code: str
    name: str
    description: str = ""


class RoleUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    is_active: bool | None = None


def _user_to_dict(u: User) -> dict:
    return {
        "id": u.id,
        "user_id": u.user_id,
        "username": u.username,
        "tenant_id": u.tenant_id,
        "email": u.email or "",
        "avatar": u.avatar or "",
        "phone": u.phone or "",
        "is_active": u.is_active,
        "roles": [{"code": r.code, "name": r.name} for r in u.roles],
        "created_at": u.created_at.isoformat() if u.created_at else None,
        "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None,
    }


# ── Users ───────────────────────────────────────────────

@users_router.get("")
def list_users(
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    search: str | None = None,
    is_active: bool | None = None,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    require_admin(actor)
    q = db.query(User).options(joinedload(User.roles))
    # 租户管理员只看自己租户的用户，平台管理员看全部
    if not actor.is_platform_admin:
        q = q.filter(User.tenant_id == actor.tenant_id)
    if search:
        like = f"%{search}%"
        q = q.filter((User.username.ilike(like)) | (User.email.ilike(like)))
    if is_active is not None:
        q = q.filter(User.is_active == is_active)
    total = q.count()
    users = q.order_by(User.id).offset((page - 1) * size).limit(size).all()
    return {"total": total, "page": page, "size": size, "items": [_user_to_dict(u) for u in users]}


@users_router.get("/{user_id}")
def get_user(
    user_id: str,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    require_admin(actor)
    user = db.query(User).options(joinedload(User.roles)).filter(User.user_id == user_id).first()
    if not user:
        raise HTTPException(404, "User not found")
    if not actor.is_platform_admin and user.tenant_id != actor.tenant_id:
        raise HTTPException(404, "User not found")
    return _user_to_dict(user)


@users_router.put("/{user_id}")
def update_user(
    user_id: str,
    body: UserUpdate,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    require_admin(actor)
    user = db.query(User).options(joinedload(User.roles)).filter(User.user_id == user_id).first()
    if not user:
        raise HTTPException(404, "User not found")
    if not actor.is_platform_admin and user.tenant_id != actor.tenant_id:
        raise HTTPException(404, "User not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(user, k, v)
    db.commit()
    db.refresh(user)
    return _user_to_dict(user)


@users_router.post("/{user_id}/roles")
def assign_role(
    user_id: str,
    req: AssignRoleRequest,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    require_admin(actor)
    user = db.query(User).options(joinedload(User.roles)).filter(User.user_id == user_id).first()
    if not user:
        raise HTTPException(404, "User not found")
    if not actor.is_platform_admin and user.tenant_id != actor.tenant_id:
        raise HTTPException(404, "User not found")
    role = db.query(Role).filter(Role.code == req.role_code).first()
    if not role:
        raise HTTPException(404, f"Role '{req.role_code}' not found")
    if role not in user.roles:
        user.roles.append(role)
        db.commit()
    return {"roles": [{"code": r.code, "name": r.name} for r in user.roles]}


@users_router.delete("/{user_id}/roles/{role_code}")
def remove_role(
    user_id: str,
    role_code: str,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    require_admin(actor)
    user = db.query(User).options(joinedload(User.roles)).filter(User.user_id == user_id).first()
    if not user:
        raise HTTPException(404, "User not found")
    if not actor.is_platform_admin and user.tenant_id != actor.tenant_id:
        raise HTTPException(404, "User not found")
    role = db.query(Role).filter(Role.code == role_code).first()
    if not role:
        raise HTTPException(404, f"Role '{role_code}' not found")
    if role in user.roles:
        user.roles.remove(role)
        db.commit()
    return {"roles": [{"code": r.code, "name": r.name} for r in user.roles]}


# ── Roles ───────────────────────────────────────────────

@roles_router.get("")
def list_roles(
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    require_admin(actor)
    roles = db.query(Role).filter(Role.is_active == True).order_by(Role.id).all()  # noqa: E712
    return {
        "items": [
            {
                "id": r.id,
                "code": r.code,
                "name": r.name,
                "description": r.description or "",
                "is_system": r.is_system,
            }
            for r in roles
        ]
    }


@roles_router.post("", status_code=201)
def create_role(
    body: RoleCreate,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    require_admin(actor)
    if db.query(Role).filter(Role.code == body.code).first():
        raise HTTPException(409, f"Role '{body.code}' already exists")
    role = Role(**body.model_dump())
    db.add(role)
    db.commit()
    db.refresh(role)
    return {"id": role.id, "code": role.code, "name": role.name, "description": role.description or ""}


@roles_router.put("/{role_code}")
def update_role(
    role_code: str,
    body: RoleUpdate,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    require_admin(actor)
    role = db.query(Role).filter(Role.code == role_code).first()
    if not role:
        raise HTTPException(404, "Role not found")
    if role.is_system:
        raise HTTPException(400, "System role cannot be modified")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(role, k, v)
    db.commit()
    db.refresh(role)
    return {"id": role.id, "code": role.code, "name": role.name}


@roles_router.delete("/{role_code}", status_code=204)
def delete_role(
    role_code: str,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    require_admin(actor)
    role = db.query(Role).filter(Role.code == role_code).first()
    if not role:
        raise HTTPException(404, "Role not found")
    if role.is_system:
        raise HTTPException(400, "System role cannot be deleted")
    db.delete(role)
    db.commit()
