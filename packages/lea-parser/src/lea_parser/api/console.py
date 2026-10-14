"""Administration-console usage and quota contract."""

from __future__ import annotations

import os
import secrets
from typing import Any

import httpx
import structlog
from fastapi import APIRouter, Depends, Header, HTTPException
from lea_authkit import Actor, internal_headers, require_admin
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from lea_parser.database import get_db
from lea_parser.models.quota import TenantQuota, UserQuota

router = APIRouter(tags=["console-governance"])
logger = structlog.get_logger()


def console_actor_from_headers(
    x_internal_token: str,
    x_user_id: str,
    x_tenant_id: str,
    x_user_role: str,
) -> Actor:
    configured = os.getenv("LEA_INTERNAL_TOKEN", "").strip()
    if not configured or not x_internal_token or not secrets.compare_digest(
        configured, x_internal_token
    ):
        raise HTTPException(401, "invalid or missing internal token")
    if not x_user_id or not x_tenant_id:
        raise HTTPException(401, "missing trusted identity")
    roles = (x_user_role,) if x_user_role in ("admin", "platform_admin") else ()
    return Actor(user_id=x_user_id, tenant_id=x_tenant_id, roles=roles)


def require_console_actor(
    x_internal_token: str = Header("", alias="X-Internal-Token"),
    x_user_id: str = Header("", alias="X-User-ID"),
    x_tenant_id: str = Header("", alias="X-Tenant-ID"),
    x_user_role: str = Header("", alias="X-User-Role"),
) -> Actor:
    return console_actor_from_headers(
        x_internal_token, x_user_id, x_tenant_id, x_user_role
    )


def _record_audit(
    actor: Actor,
    *,
    action: str,
    resource_type: str,
    resource_id: str,
    before: dict[str, Any] | None,
    after: dict[str, Any] | None,
    reason: str,
) -> None:
    base = os.getenv("LEA_CONFIG_URL", "").strip().rstrip("/")
    if not base:
        return
    headers = internal_headers()
    headers.update(
        {
            "X-User-ID": actor.user_id,
            "X-Tenant-ID": actor.tenant_id,
            "X-User-Role": "platform_admin" if actor.is_platform_admin else "admin",
        }
    )
    try:
        response = httpx.post(
            base + "/internal/governance/audit-events",
            headers=headers,
            json={
                "tenant_id": actor.tenant_id,
                "action": action,
                "resource_type": resource_type,
                "resource_id": resource_id,
                "before_summary": before,
                "after_summary": after,
                "reason": reason,
            },
            timeout=3,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("console_audit_write_failed", action=action, error=str(exc))


class TenantQuotaBody(BaseModel):
    max_calls_per_hour: int = Field(500, ge=0)
    max_file_size_mb: int = Field(200, ge=0)
    max_monthly_calls: int = Field(50000, ge=0)
    max_monthly_size_mb: int = Field(10000, ge=0)
    reason: str = Field(min_length=1, max_length=1000)


class UserQuotaBody(BaseModel):
    max_calls_per_hour: int = Field(100, ge=0)
    max_file_size_mb: int = Field(50, ge=0)
    max_monthly_calls: int = Field(5000, ge=0)
    reason: str = Field(min_length=1, max_length=1000)


def _tenant_quota(row: TenantQuota | None) -> dict:
    return {
        "max_calls_per_hour": row.max_calls_per_hour if row else 500,
        "max_file_size_mb": row.max_file_size_mb if row else 200,
        "max_monthly_calls": row.max_monthly_calls if row else 50000,
        "max_monthly_size_mb": row.max_monthly_size_mb if row else 10000,
    }


def _user_quota(row: UserQuota | None, user_id: str) -> dict:
    return {
        "user_id": user_id,
        "max_calls_per_hour": row.max_calls_per_hour if row else 100,
        "max_file_size_mb": row.max_file_size_mb if row else 50,
        "max_monthly_calls": row.max_monthly_calls if row else 5000,
    }


@router.get("/quotas")
def get_quotas(
    user_id: str | None = None,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_console_actor),
):
    require_admin(actor)
    tenant = db.query(TenantQuota).filter_by(tenant_id=actor.tenant_id).first()
    result = {"tenant": _tenant_quota(tenant)}
    if user_id:
        row = db.query(UserQuota).filter_by(
            tenant_id=actor.tenant_id, user_id=user_id
        ).first()
        result["user"] = _user_quota(row, user_id)
    return result


@router.put("/quotas/tenant")
def update_tenant_quota(
    body: TenantQuotaBody,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_console_actor),
):
    require_admin(actor)
    row = db.query(TenantQuota).filter_by(tenant_id=actor.tenant_id).first()
    before = _tenant_quota(row)
    if row is None:
        row = TenantQuota(tenant_id=actor.tenant_id)
        db.add(row)
    for key, value in body.model_dump(exclude={"reason"}).items():
        setattr(row, key, value)
    db.commit()
    after = _tenant_quota(row)
    _record_audit(
        actor,
        action="quota.tenant.update",
        resource_type="tenant_quota",
        resource_id=actor.tenant_id,
        before=before,
        after=after,
        reason=body.reason,
    )
    return {"tenant": after, "reason": body.reason}


@router.put("/quotas/users/{user_id}")
def update_user_quota(
    user_id: str,
    body: UserQuotaBody,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_console_actor),
):
    require_admin(actor)
    row = db.query(UserQuota).filter_by(
        tenant_id=actor.tenant_id, user_id=user_id
    ).first()
    before = _user_quota(row, user_id)
    if row is None:
        row = UserQuota(tenant_id=actor.tenant_id, user_id=user_id)
        db.add(row)
    for key, value in body.model_dump(exclude={"reason"}).items():
        setattr(row, key, value)
    db.commit()
    after = _user_quota(row, user_id)
    _record_audit(
        actor,
        action="quota.user.update",
        resource_type="user_quota",
        resource_id=user_id,
        before=before,
        after=after,
        reason=body.reason,
    )
    return {"user": after, "reason": body.reason}
