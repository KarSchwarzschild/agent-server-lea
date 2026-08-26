"""配额管理 Admin API。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from lea_parser.auth import CurrentUser, get_current_user
from lea_parser.database import get_db
from lea_parser.models.quota import TenantQuota, UserQuota

router = APIRouter(prefix="/admin/quotas", tags=["admin"])


class TenantQuotaUpdate(BaseModel):
    max_calls_per_hour: int = 500
    max_file_size_mb: int = 200
    max_monthly_calls: int = 50000
    max_monthly_size_mb: int = 10000


class UserQuotaUpdate(BaseModel):
    user_id: str
    max_calls_per_hour: int = 100
    max_file_size_mb: int = 50
    max_monthly_calls: int = 5000


def _require_admin(user: CurrentUser):
    if user.role != "admin":
        raise HTTPException(403, "Admin required")


@router.get("/tenant")
def get_tenant_quota(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_admin(user)
    q = db.query(TenantQuota).filter_by(tenant_id=user.tenant_id).first()
    if not q:
        return TenantQuotaUpdate().model_dump()
    return {
        "max_calls_per_hour": q.max_calls_per_hour,
        "max_file_size_mb": q.max_file_size_mb,
        "max_monthly_calls": q.max_monthly_calls,
        "max_monthly_size_mb": q.max_monthly_size_mb,
    }


@router.put("/tenant")
def update_tenant_quota(
    body: TenantQuotaUpdate,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_admin(user)
    q = db.query(TenantQuota).filter_by(tenant_id=user.tenant_id).first()
    if not q:
        q = TenantQuota(tenant_id=user.tenant_id)
        db.add(q)
    q.max_calls_per_hour = body.max_calls_per_hour
    q.max_file_size_mb = body.max_file_size_mb
    q.max_monthly_calls = body.max_monthly_calls
    q.max_monthly_size_mb = body.max_monthly_size_mb
    db.commit()
    return {"ok": True}


@router.get("/user/{user_id}")
def get_user_quota(
    user_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_admin(user)
    q = db.query(UserQuota).filter_by(
        tenant_id=user.tenant_id, user_id=user_id
    ).first()
    if not q:
        return UserQuotaUpdate(user_id=user_id).model_dump()
    return {
        "user_id": q.user_id,
        "max_calls_per_hour": q.max_calls_per_hour,
        "max_file_size_mb": q.max_file_size_mb,
        "max_monthly_calls": q.max_monthly_calls,
    }


@router.put("/user")
def update_user_quota(
    body: UserQuotaUpdate,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _require_admin(user)
    q = db.query(UserQuota).filter_by(
        tenant_id=user.tenant_id, user_id=body.user_id
    ).first()
    if not q:
        q = UserQuota(tenant_id=user.tenant_id, user_id=body.user_id)
        db.add(q)
    q.max_calls_per_hour = body.max_calls_per_hour
    q.max_file_size_mb = body.max_file_size_mb
    q.max_monthly_calls = body.max_monthly_calls
    db.commit()
    return {"ok": True}
