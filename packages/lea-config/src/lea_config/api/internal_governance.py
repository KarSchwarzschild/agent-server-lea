"""Trusted service-to-service governance ingestion endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from lea_authkit import Actor, require_actor
from lea_config.database import get_db
from lea_config.services.publication import (
    cancel_request,
    publication_request_dict,
    record_audit_event,
    submit_external_request,
)

router = APIRouter(prefix="/internal/governance", tags=["internal-governance"])


class ExternalPublicationRequest(BaseModel):
    resource_type: str
    resource_id: str
    resource_version: str
    tenant_id: str
    source_scope: str
    target_scope: str
    note: str = ""
    risk_snapshot: dict[str, Any] = Field(default_factory=dict)
    resource_snapshot: dict[str, Any] = Field(default_factory=dict)


@router.post("/publication-requests", status_code=201)
def create_external_publication_request(
    body: ExternalPublicationRequest,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    request = submit_external_request(
        db,
        actor,
        resource_type=body.resource_type,
        resource_id=body.resource_id,
        resource_version=body.resource_version,
        tenant_id=body.tenant_id,
        source_scope=body.source_scope,
        target_scope=body.target_scope,
        note=body.note,
        risk_snapshot=body.risk_snapshot,
        resource_snapshot_data=body.resource_snapshot,
    )
    return publication_request_dict(request)


class ExternalCancelRequest(BaseModel):
    note: str = ""


@router.post("/publication-requests/{request_id}/cancel")
def cancel_external_publication_request(
    request_id: int,
    body: ExternalCancelRequest,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    return publication_request_dict(cancel_request(db, actor, request_id, body.note))


class ExternalAuditEvent(BaseModel):
    tenant_id: str = Field(min_length=1, max_length=64)
    action: str = Field(min_length=1, max_length=100)
    resource_type: str = Field(min_length=1, max_length=50)
    resource_id: str = Field(min_length=1, max_length=100)
    request_id: str | None = Field(default=None, max_length=100)
    result: str = Field(default="success", min_length=1, max_length=20)
    before_summary: dict[str, Any] | None = None
    after_summary: dict[str, Any] | None = None
    reason: str | None = Field(default=None, max_length=4000)


@router.post("/audit-events", status_code=201)
def create_external_audit_event(
    body: ExternalAuditEvent,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    if not actor.is_platform_admin and body.tenant_id != actor.tenant_id:
        raise HTTPException(403, "cannot write audit events for another tenant")
    record_audit_event(
        db,
        actor,
        action=body.action,
        result=body.result,
        resource_type=body.resource_type,
        resource_id=body.resource_id,
        tenant_id=body.tenant_id,
        request_id=body.request_id,
        before=body.before_summary,
        after=body.after_summary,
        reason=body.reason,
    )
    db.commit()
    return {"ok": True}
