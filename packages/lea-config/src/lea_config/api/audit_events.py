"""Read-only, role-scoped audit log."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from lea_authkit import Actor, require_actor
from lea_config.api._auth_helpers import require_admin
from lea_config.database import get_db
from lea_config.models.publication import AuditEvent

router = APIRouter(prefix="/audit-events", tags=["audit-events"])


def _event_to_dict(event: AuditEvent) -> dict:
    return {
        "event_id": event.event_id,
        "tenant_id": event.tenant_id,
        "actor_user_id": event.actor_user_id,
        "actor_role": event.actor_role,
        "action": event.action,
        "resource_type": event.resource_type,
        "resource_id": event.resource_id,
        "request_id": event.request_id,
        "result": event.result,
        "before_summary": event.before_summary or {},
        "after_summary": event.after_summary or {},
        "reason": event.reason,
        "created_at": event.created_at,
    }


def _visible_events(db: Session, actor: Actor):
    query = db.query(AuditEvent)
    if actor.is_platform_admin:
        return query
    if actor.is_tenant_admin:
        return query.filter(AuditEvent.tenant_id == actor.tenant_id)
    return query.filter(
        AuditEvent.tenant_id == actor.tenant_id,
        AuditEvent.actor_user_id == actor.user_id,
    )


@router.get("")
def list_audit_events(
    action: str | None = None,
    resource_type: str | None = None,
    resource_id: str | None = None,
    actor_user_id: str | None = None,
    result: str | None = None,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    require_admin(actor)
    if actor_user_id and not (actor.is_tenant_admin or actor.is_platform_admin):
        raise HTTPException(403, "只有管理员可以按操作人筛选审计日志")

    query = _visible_events(db, actor)
    if action:
        query = query.filter(AuditEvent.action == action)
    if resource_type:
        query = query.filter(AuditEvent.resource_type == resource_type)
    if resource_id:
        query = query.filter(AuditEvent.resource_id == resource_id)
    if actor_user_id:
        query = query.filter(AuditEvent.actor_user_id == actor_user_id)
    if result:
        query = query.filter(AuditEvent.result == result)

    total = query.count()
    rows = (
        query.order_by(AuditEvent.created_at.desc(), AuditEvent.event_id.desc())
        .offset((page - 1) * size)
        .limit(size)
        .all()
    )
    return {
        "items": [_event_to_dict(row) for row in rows],
        "total": total,
        "page": page,
        "size": size,
    }


@router.get("/{event_id}")
def get_audit_event(
    event_id: str,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    require_admin(actor)
    event = _visible_events(db, actor).filter(AuditEvent.event_id == event_id).first()
    if event is None:
        raise HTTPException(404, "审计事件不存在")
    return _event_to_dict(event)
