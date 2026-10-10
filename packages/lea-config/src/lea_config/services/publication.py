"""Publication state machine shared by resource APIs and the admin console."""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Query, Session

from lea_authkit import Actor, internal_headers
from lea_config.database import DEFAULT_TENANT_ID, OWNER_SCOPE_PLATFORM
from lea_config.models.publication import (
    AuditEvent,
    PUBLICATION_APPROVED,
    PUBLICATION_CANCELED,
    PUBLICATION_PENDING,
    PUBLICATION_REJECTED,
    PUBLICATION_STATUSES,
    PUBLICATION_TARGET_GLOBAL,
    PUBLICATION_TARGET_TENANT,
    PublicationRequest,
)
from lea_config.models.agent import Agent
from lea_config.models.mcp_server import MCPServer
from lea_config.models.skill import Skill
from lea_config.models.tool import Tool

RESOURCE_SKILL = "skill"
RESOURCE_AGENT = "agent"
RESOURCE_TOOL = "tool"
RESOURCE_MCP_SERVER = "mcp_server"
RESOURCE_KNOWLEDGE_BASE = "knowledge_base"
GENERIC_RESOURCE_TYPES = (RESOURCE_AGENT, RESOURCE_TOOL, RESOURCE_MCP_SERVER)
RESOURCE_MODELS = {
    RESOURCE_AGENT: Agent,
    RESOURCE_TOOL: Tool,
    RESOURCE_MCP_SERVER: MCPServer,
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def actor_role(actor: Actor) -> str:
    if actor.is_platform_admin:
        return "platform_admin"
    if actor.is_tenant_admin:
        return "admin"
    return "user"


def publication_request_dict(request: PublicationRequest) -> dict[str, Any]:
    return {
        "id": request.id,
        "resource_type": request.resource_type,
        "resource_id": request.resource_id,
        "resource_version": request.resource_version,
        "tenant_id": request.tenant_id,
        "source_scope": request.source_scope,
        "target_scope": request.target_scope,
        "status": request.status,
        "requested_by": request.requested_by,
        "request_note": request.request_note,
        "reviewed_by": request.reviewed_by,
        "review_note": request.review_note,
        "reviewed_at": request.reviewed_at,
        "risk_snapshot": request.risk_snapshot or {},
        "resource_snapshot": request.resource_snapshot or {},
        "created_at": request.created_at,
        "updated_at": request.updated_at,
    }


def scoped_requests(db: Session, actor: Actor) -> Query:
    query = db.query(PublicationRequest)
    if actor.is_platform_admin:
        return query.filter(
            PublicationRequest.target_scope == PUBLICATION_TARGET_GLOBAL
        )
    if actor.is_tenant_admin:
        return query.filter(
            PublicationRequest.tenant_id == actor.tenant_id,
            PublicationRequest.target_scope == PUBLICATION_TARGET_TENANT,
        )
    return query.filter(
        PublicationRequest.tenant_id == actor.tenant_id,
        PublicationRequest.requested_by == actor.user_id,
    )


def load_visible_request(db: Session, actor: Actor, request_id: int) -> PublicationRequest:
    request = scoped_requests(db, actor).filter(PublicationRequest.id == request_id).first()
    if request is None:
        raise HTTPException(404, "申请不存在")
    return request


def skill_scope(skill: Skill) -> str:
    if skill.owner_scope != OWNER_SCOPE_PLATFORM:
        return "private"
    return "global" if skill.tenant_id == DEFAULT_TENANT_ID else "tenant"


def skill_snapshot(skill: Skill) -> dict[str, Any]:
    prompt = (skill.system_prompt or "").encode("utf-8")
    bundle = (skill.bundle_uri or "").encode("utf-8")
    return {
        "name": skill.name,
        "display_name": skill.display_name or skill.name,
        "description": skill.description or "",
        "category": skill.category or "general",
        "version": skill.version or "1.0.0",
        "model_tier": skill.model_tier or "standard",
        "required_tools": skill.required_tools or [],
        "enabled": bool(skill.enabled),
        "system_prompt_sha256": hashlib.sha256(prompt).hexdigest(),
        "bundle_uri_sha256": hashlib.sha256(bundle).hexdigest(),
    }


def resource_scope(resource: Any) -> str:
    if getattr(resource, "owner_scope", None) != OWNER_SCOPE_PLATFORM:
        return "private"
    return "global" if resource.tenant_id == DEFAULT_TENANT_ID else "tenant"


def resource_snapshot(resource_type: str, resource: Any) -> dict[str, Any]:
    if resource_type == RESOURCE_SKILL:
        return skill_snapshot(resource)
    if resource_type == RESOURCE_AGENT:
        return {
            "name": resource.name,
            "description": resource.description or "",
            "category": resource.category or "general",
            "enabled": bool(resource.enabled),
            "soul_prompt_sha256": hashlib.sha256(
                (resource.soul_prompt or "").encode("utf-8")
            ).hexdigest(),
            "updated_at": resource.updated_at.isoformat() if resource.updated_at else None,
        }
    if resource_type == RESOURCE_TOOL:
        return {
            "name": resource.name,
            "display_name": resource.display_name or resource.name,
            "description": resource.description or "",
            "execution_type": resource.execution_type,
            "schema_json": resource.schema_json or {},
            "endpoint": resource.endpoint,
            "mcp_server_id": resource.mcp_server_id,
            "enabled": bool(resource.enabled),
            "updated_at": resource.updated_at.isoformat() if resource.updated_at else None,
        }
    if resource_type == RESOURCE_MCP_SERVER:
        auth = resource.auth_config or {}
        auth_fingerprint = hashlib.sha256(
            repr(sorted(auth.items())).encode("utf-8")
        ).hexdigest()
        return {
            "name": resource.name,
            "description": resource.description or "",
            "transport": resource.transport,
            "endpoint": resource.endpoint,
            "tool_filter": resource.tool_filter or [],
            "enabled": bool(resource.enabled),
            "auth_config_sha256": auth_fingerprint,
            "updated_at": resource.updated_at.isoformat() if resource.updated_at else None,
        }
    raise HTTPException(422, f"暂不支持资源类型 {resource_type!r}")


def record_audit_event(
    db: Session,
    actor: Actor,
    request: PublicationRequest | None = None,
    action: str = "",
    result: str = "success",
    *,
    resource_type: str | None = None,
    resource_id: str | None = None,
    tenant_id: str | None = None,
    request_id: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    reason: str | None = None,
) -> None:
    if request is not None:
        resource_type = request.resource_type
        resource_id = request.resource_id
        tenant_id = request.tenant_id
        request_id = str(request.id)
    if not resource_type or not resource_id or not tenant_id:
        raise ValueError("audit event requires resource_type, resource_id, and tenant_id")
    db.add(
        AuditEvent(
            tenant_id=tenant_id,
            actor_user_id=actor.user_id,
            actor_role=actor_role(actor),
            action=action,
            resource_type=resource_type,
            resource_id=resource_id,
            request_id=request_id,
            result=result,
            before_summary=before,
            after_summary=after,
            reason=reason or None,
        )
    )


def submit_skill_request(
    db: Session,
    actor: Actor,
    skill: Skill,
    target_scope: str,
    note: str = "",
) -> PublicationRequest:
    pending = (
        db.query(PublicationRequest.id)
        .filter(
            PublicationRequest.resource_type == RESOURCE_SKILL,
            PublicationRequest.resource_id == str(skill.id),
            PublicationRequest.status == PUBLICATION_PENDING,
        )
        .first()
    )
    if pending:
        raise HTTPException(409, "这个 Skill 已经有一份申请在审核中")

    request = PublicationRequest(
        resource_type=RESOURCE_SKILL,
        resource_id=str(skill.id),
        resource_version=skill.version or "1.0.0",
        tenant_id=skill.tenant_id,
        source_scope=skill_scope(skill),
        target_scope=target_scope,
        status=PUBLICATION_PENDING,
        requested_by=actor.user_id,
        request_note=note or None,
        risk_snapshot={"required_tools": skill.required_tools or []},
        resource_snapshot=skill_snapshot(skill),
        pending_key=f"{RESOURCE_SKILL}:{skill.id}",
    )
    db.add(request)
    try:
        db.flush()
        record_audit_event(
            db,
            actor,
            request,
            "publication.submitted",
            "success",
            after={"status": PUBLICATION_PENDING, "target_scope": target_scope},
            reason=note,
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "这个 Skill 已经有一份申请在审核中")
    db.refresh(request)
    return request


def submit_resource_request(
    db: Session,
    actor: Actor,
    resource_type: str,
    resource: Any,
    target_scope: str,
    note: str = "",
) -> PublicationRequest:
    if resource_type not in GENERIC_RESOURCE_TYPES:
        raise HTTPException(422, f"暂不支持资源类型 {resource_type!r}")
    pending = (
        db.query(PublicationRequest.id)
        .filter(
            PublicationRequest.resource_type == resource_type,
            PublicationRequest.resource_id == str(resource.id),
            PublicationRequest.status == PUBLICATION_PENDING,
        )
        .first()
    )
    if pending:
        raise HTTPException(409, "这个资源已经有一份申请在审核中")

    request = PublicationRequest(
        resource_type=resource_type,
        resource_id=str(resource.id),
        resource_version=(resource.updated_at.isoformat() if resource.updated_at else "current"),
        tenant_id=resource.tenant_id,
        source_scope=resource_scope(resource),
        target_scope=target_scope,
        status=PUBLICATION_PENDING,
        requested_by=actor.user_id,
        request_note=note or None,
        risk_snapshot={"enabled": bool(resource.enabled)},
        resource_snapshot=resource_snapshot(resource_type, resource),
        pending_key=f"{resource_type}:{resource.id}",
    )
    db.add(request)
    try:
        db.flush()
        record_audit_event(
            db,
            actor,
            request,
            "publication.submitted",
            "success",
            after={"status": PUBLICATION_PENDING, "target_scope": target_scope},
            reason=note,
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "这个资源已经有一份申请在审核中")
    db.refresh(request)
    return request


def submit_external_request(
    db: Session,
    actor: Actor,
    *,
    resource_type: str,
    resource_id: str,
    resource_version: str,
    tenant_id: str,
    source_scope: str,
    target_scope: str,
    note: str,
    risk_snapshot: dict[str, Any],
    resource_snapshot_data: dict[str, Any],
) -> PublicationRequest:
    if resource_type != RESOURCE_KNOWLEDGE_BASE:
        raise HTTPException(422, "不支持的外部资源类型")
    if target_scope not in (PUBLICATION_TARGET_TENANT, PUBLICATION_TARGET_GLOBAL):
        raise HTTPException(422, "target_scope 只能是 tenant 或 global")
    pending = (
        db.query(PublicationRequest.id)
        .filter(
            PublicationRequest.resource_type == resource_type,
            PublicationRequest.resource_id == str(resource_id),
            PublicationRequest.status == PUBLICATION_PENDING,
        )
        .first()
    )
    if pending:
        raise HTTPException(409, "这个资源已经有一份申请在审核中")
    request = PublicationRequest(
        resource_type=resource_type,
        resource_id=str(resource_id),
        resource_version=resource_version,
        tenant_id=tenant_id,
        source_scope=source_scope,
        target_scope=target_scope,
        status=PUBLICATION_PENDING,
        requested_by=actor.user_id,
        request_note=note or None,
        risk_snapshot=risk_snapshot,
        resource_snapshot=resource_snapshot_data,
        pending_key=f"{resource_type}:{resource_id}",
    )
    db.add(request)
    try:
        db.flush()
        record_audit_event(
            db,
            actor,
            request,
            "publication.submitted",
            "success",
            after={"status": PUBLICATION_PENDING, "target_scope": target_scope},
            reason=note,
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "这个资源已经有一份申请在审核中")
    db.refresh(request)
    return request


def _apply_knowledge_base_review(request: PublicationRequest) -> None:
    base = os.getenv("LEA_RAG_URL", "http://lea-rag:8300").rstrip("/")
    try:
        response = httpx.post(
            f"{base}/internal/publications/knowledge-bases/{request.resource_id}/review",
            json={
                "resource_version": request.resource_version,
                "target_scope": request.target_scope,
            },
            headers=internal_headers(),
            timeout=15,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(502, f"知识库服务不可达: {exc}") from exc
    if response.status_code >= 400:
        detail = response.text[:500]
        try:
            detail = response.json().get("detail", detail)
        except ValueError:
            pass
        raise HTTPException(response.status_code, detail or "知识库发布失败")


def _authorize_review(actor: Actor, request: PublicationRequest) -> None:
    if request.target_scope == PUBLICATION_TARGET_GLOBAL:
        if not actor.is_platform_admin:
            raise HTTPException(403, "上架到全平台需要平台管理员审核")
        return
    if not actor.is_tenant_admin:
        raise HTTPException(403, "需要租户管理员审核")
    if not actor.is_platform_admin and request.tenant_id != actor.tenant_id:
        raise HTTPException(404, "申请不存在")


def review_request(
    db: Session,
    actor: Actor,
    request_id: int,
    decision: str,
    note: str = "",
) -> PublicationRequest:
    request = (
        db.query(PublicationRequest)
        .filter(PublicationRequest.id == request_id)
        .with_for_update()
        .first()
    )
    if request is None:
        raise HTTPException(404, "申请不存在")
    _authorize_review(actor, request)
    if request.status != PUBLICATION_PENDING:
        raise HTTPException(409, f"这份申请已经是 {request.status}，不能重复处理")
    if decision not in (PUBLICATION_APPROVED, PUBLICATION_REJECTED):
        raise HTTPException(422, "decision 只能是 approved 或 rejected")

    if request.resource_type not in (
        RESOURCE_SKILL,
        RESOURCE_KNOWLEDGE_BASE,
        *GENERIC_RESOURCE_TYPES,
    ):
        raise HTTPException(422, f"暂不支持审核资源类型 {request.resource_type!r}")
    try:
        resource_id = int(request.resource_id)
    except ValueError:
        raise HTTPException(422, "Skill 资源 ID 无效")
    resource = None
    before_scope = request.source_scope
    if request.resource_type != RESOURCE_KNOWLEDGE_BASE:
        model = Skill if request.resource_type == RESOURCE_SKILL else RESOURCE_MODELS[request.resource_type]
        resource = db.query(model).filter(model.id == resource_id).first()
        if resource is None:
            raise HTTPException(410, "对应的资源已被删除")

        migrated = (request.risk_snapshot or {}).get("migrated_from")
        snapshot_changed = (
            not migrated
            and request.resource_snapshot
            and resource_snapshot(request.resource_type, resource) != request.resource_snapshot
        )
        if decision == PUBLICATION_APPROVED and (
            snapshot_changed or resource_scope(resource) != request.source_scope
        ):
            raise HTTPException(409, "资源在提交审批后发生了变化，请取消后重新提交")
        before_scope = resource_scope(resource)
    request.reviewed_by = actor.user_id
    request.reviewed_at = utcnow()
    request.review_note = note or None
    request.pending_key = None

    if decision == PUBLICATION_REJECTED:
        request.status = PUBLICATION_REJECTED
    else:
        from lea_config.services.governance import require_passing_check, upsert_publication

        if request.resource_type == RESOURCE_KNOWLEDGE_BASE:
            _apply_knowledge_base_review(request)
        else:
            require_passing_check(db, request.resource_type, resource)
        request.status = PUBLICATION_APPROVED
        if resource is not None:
            resource.owner_scope = OWNER_SCOPE_PLATFORM
            resource.owner_user_id = None
            if request.target_scope == PUBLICATION_TARGET_GLOBAL:
                resource.tenant_id = DEFAULT_TENANT_ID
        publication_tenant = (
            DEFAULT_TENANT_ID
            if request.target_scope == PUBLICATION_TARGET_GLOBAL
            else request.tenant_id
        )
        upsert_publication(
            db,
            actor,
            request.resource_type,
            request.resource_id,
            publication_tenant,
            request.target_scope,
        )

    after_scope = request.target_scope if decision == PUBLICATION_APPROVED else before_scope
    record_audit_event(
        db,
        actor,
        request,
        f"publication.{decision}",
        "success",
        before={"status": PUBLICATION_PENDING, "scope": before_scope},
        after={"status": request.status, "scope": after_scope},
        reason=note,
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "目标空间中存在同名资源，无法发布")
    db.refresh(request)
    return request


def cancel_request(
    db: Session,
    actor: Actor,
    request_id: int,
    note: str = "",
) -> PublicationRequest:
    request = (
        db.query(PublicationRequest)
        .filter(PublicationRequest.id == request_id)
        .with_for_update()
        .first()
    )
    if request is None:
        raise HTTPException(404, "申请不存在")
    if request.tenant_id != actor.tenant_id and not actor.is_platform_admin:
        raise HTTPException(404, "申请不存在")
    if request.requested_by != actor.user_id and not actor.is_platform_admin:
        raise HTTPException(403, "只有申请人可以取消申请")
    if request.status != PUBLICATION_PENDING:
        raise HTTPException(409, f"这份申请已经是 {request.status}，不能取消")

    request.status = PUBLICATION_CANCELED
    request.pending_key = None
    record_audit_event(
        db,
        actor,
        request,
        "publication.canceled",
        "success",
        before={"status": PUBLICATION_PENDING},
        after={"status": PUBLICATION_CANCELED},
        reason=note,
    )
    db.commit()
    db.refresh(request)
    return request


def cancel_pending_for_resource(
    db: Session,
    actor: Actor,
    resource_type: str,
    resource_id: str,
    reason: str,
) -> int:
    requests = (
        db.query(PublicationRequest)
        .filter(
            PublicationRequest.resource_type == resource_type,
            PublicationRequest.resource_id == resource_id,
            PublicationRequest.status == PUBLICATION_PENDING,
        )
        .with_for_update()
        .all()
    )
    for request in requests:
        request.status = PUBLICATION_CANCELED
        request.pending_key = None
        record_audit_event(
            db,
            actor,
            request,
            "publication.canceled",
            "success",
            before={"status": PUBLICATION_PENDING},
            after={"status": PUBLICATION_CANCELED},
            reason=reason,
        )
    return len(requests)


def validate_status(status: str) -> None:
    if status not in PUBLICATION_STATUSES:
        raise HTTPException(422, f"status 只能是 {PUBLICATION_STATUSES}")
