"""Published-resource lifecycle and automatic governance checks."""

from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import HTTPException
from lea_authkit import Actor, internal_headers
from lea_config.models.agent import Agent
from lea_config.models.mcp_server import MCPServer
from lea_config.models.publication import (
    RESOURCE_CHECK_FAILED,
    RESOURCE_CHECK_PASSED,
    RESOURCE_PUBLICATION_ACTIVE,
    RESOURCE_PUBLICATION_ARCHIVED,
    RESOURCE_PUBLICATION_STATUSES,
    RESOURCE_PUBLICATION_SUSPENDED,
    ResourceCheck,
    ResourcePublication,
)
from lea_config.models.skill import Skill
from lea_config.models.tool import Tool
from lea_config.services.publication import (
    RESOURCE_AGENT,
    RESOURCE_KNOWLEDGE_BASE,
    RESOURCE_MCP_SERVER,
    RESOURCE_SKILL,
    RESOURCE_TOOL,
    record_audit_event,
    resource_snapshot,
)
from sqlalchemy.orm import Session

LOCAL_MODELS = {
    RESOURCE_AGENT: Agent,
    RESOURCE_SKILL: Skill,
    RESOURCE_TOOL: Tool,
    RESOURCE_MCP_SERVER: MCPServer,
}
CHECKED_RESOURCE_TYPES = (RESOURCE_TOOL, RESOURCE_MCP_SERVER)


def publication_dict(row: ResourcePublication) -> dict[str, Any]:
    return {
        "id": row.id,
        "resource_type": row.resource_type,
        "resource_id": row.resource_id,
        "tenant_id": row.tenant_id,
        "scope": row.scope,
        "status": row.status,
        "published_by": row.published_by,
        "published_at": row.published_at,
        "updated_by": row.updated_by,
        "updated_at": row.updated_at,
    }


def check_dict(row: ResourceCheck) -> dict[str, Any]:
    return {
        "id": row.id,
        "resource_type": row.resource_type,
        "resource_id": row.resource_id,
        "resource_version": row.resource_version,
        "tenant_id": row.tenant_id,
        "status": row.status,
        "summary": row.summary,
        "details": row.details or {},
        "checked_by": row.checked_by,
        "created_at": row.created_at,
    }


def upsert_publication(
    db: Session,
    actor: Actor,
    resource_type: str,
    resource_id: str,
    tenant_id: str,
    scope: str,
) -> ResourcePublication:
    row = (
        db.query(ResourcePublication)
        .filter_by(resource_type=resource_type, resource_id=str(resource_id))
        .first()
    )
    if row is None:
        row = ResourcePublication(
            resource_type=resource_type,
            resource_id=str(resource_id),
            tenant_id=tenant_id,
            scope=scope,
            status=RESOURCE_PUBLICATION_ACTIVE,
            published_by=actor.user_id,
            updated_by=actor.user_id,
        )
        db.add(row)
    else:
        row.tenant_id = tenant_id
        row.scope = scope
        row.status = RESOURCE_PUBLICATION_ACTIVE
        row.updated_by = actor.user_id
    return row


def load_publication_for_admin(
    db: Session, actor: Actor, resource_type: str, resource_id: str
) -> ResourcePublication:
    row = (
        db.query(ResourcePublication)
        .filter_by(resource_type=resource_type, resource_id=str(resource_id))
        .first()
    )
    if row is None:
        raise HTTPException(404, "发布记录不存在")
    if actor.is_platform_admin:
        return row
    if row.scope == "global" or row.tenant_id != actor.tenant_id:
        raise HTTPException(404, "发布记录不存在")
    return row


def _remote_kb_action(resource_id: str, action: str) -> None:
    base = os.getenv("LEA_RAG_URL", "http://lea-rag:8300").rstrip("/")
    try:
        response = httpx.post(
            f"{base}/internal/publications/knowledge-bases/{resource_id}/{action}",
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
        raise HTTPException(response.status_code, detail or "知识库状态更新失败")


def transition_publication(
    db: Session,
    actor: Actor,
    resource_type: str,
    resource_id: str,
    target_status: str,
    reason: str,
) -> ResourcePublication:
    if target_status not in RESOURCE_PUBLICATION_STATUSES:
        raise HTTPException(422, "无效的发布状态")
    if not reason.strip():
        raise HTTPException(422, "必须填写操作原因")

    row = load_publication_for_admin(db, actor, resource_type, resource_id)
    previous = row.status
    allowed = {
        RESOURCE_PUBLICATION_ACTIVE: {
            RESOURCE_PUBLICATION_SUSPENDED,
            RESOURCE_PUBLICATION_ARCHIVED,
        },
        RESOURCE_PUBLICATION_SUSPENDED: {
            RESOURCE_PUBLICATION_ACTIVE,
            RESOURCE_PUBLICATION_ARCHIVED,
        },
        RESOURCE_PUBLICATION_ARCHIVED: set(),
    }
    if target_status not in allowed.get(previous, set()):
        raise HTTPException(409, f"不能从 {previous} 变更为 {target_status}")

    action = {
        RESOURCE_PUBLICATION_ACTIVE: "resume",
        RESOURCE_PUBLICATION_SUSPENDED: "suspend",
        RESOURCE_PUBLICATION_ARCHIVED: "archive",
    }[target_status]
    if resource_type == RESOURCE_KNOWLEDGE_BASE:
        _remote_kb_action(resource_id, action)
    else:
        model = LOCAL_MODELS.get(resource_type)
        if model is None:
            raise HTTPException(422, "不支持的资源类型")
        resource = db.query(model).filter(model.id == int(resource_id)).first()
        if resource is None:
            raise HTTPException(410, "对应资源已被删除")
        resource.enabled = target_status == RESOURCE_PUBLICATION_ACTIVE

    row.status = target_status
    row.updated_by = actor.user_id
    record_audit_event(
        db,
        actor,
        action=f"publication.{action}",
        result="success",
        resource_type=row.resource_type,
        resource_id=row.resource_id,
        tenant_id=row.tenant_id,
        before={"status": previous},
        after={"status": target_status},
        reason=reason,
    )
    db.commit()
    db.refresh(row)
    return row


def latest_check(
    db: Session, resource_type: str, resource_id: str
) -> ResourceCheck | None:
    return (
        db.query(ResourceCheck)
        .filter_by(resource_type=resource_type, resource_id=str(resource_id))
        .order_by(ResourceCheck.created_at.desc(), ResourceCheck.id.desc())
        .first()
    )


def require_passing_check(db: Session, resource_type: str, resource: Any) -> None:
    if resource_type not in CHECKED_RESOURCE_TYPES:
        return
    version = resource.updated_at.isoformat() if resource.updated_at else "current"
    check = latest_check(db, resource_type, str(resource.id))
    if check is None or check.resource_version != version or check.status != RESOURCE_CHECK_PASSED:
        raise HTTPException(409, "资源必须先通过最新版本的自动检查")


async def run_check(
    db: Session, actor: Actor, resource_type: str, resource_id: int
) -> ResourceCheck:
    if resource_type not in CHECKED_RESOURCE_TYPES:
        raise HTTPException(422, "该资源类型不支持自动检查")
    model = LOCAL_MODELS[resource_type]
    resource = db.query(model).filter(model.id == resource_id).first()
    if resource is None:
        raise HTTPException(404, "资源不存在")
    if not actor.is_platform_admin and resource.tenant_id != actor.tenant_id:
        raise HTTPException(404, "资源不存在")

    checks: list[dict[str, Any]] = []
    if resource_type == RESOURCE_TOOL:
        schema_ok = isinstance(resource.schema_json, dict) and bool(resource.schema_json)
        checks.append({"name": "schema", "passed": schema_ok})
        endpoint_ok = resource.execution_type != "api" or bool(
            resource.endpoint and resource.endpoint.startswith(("http://", "https://"))
        )
        checks.append({"name": "endpoint", "passed": endpoint_ok})
    else:
        from lea_config.api.mcp_servers import _discover_remote_tools

        discovered = await _discover_remote_tools(resource)
        checks.append(
            {
                "name": "connection",
                "passed": bool(discovered.get("ok")),
                "error": discovered.get("error", ""),
                "tool_count": len(discovered.get("tools") or []),
            }
        )

    passed = all(item["passed"] for item in checks)
    version = resource.updated_at.isoformat() if resource.updated_at else "current"
    row = ResourceCheck(
        resource_type=resource_type,
        resource_id=str(resource.id),
        resource_version=version,
        tenant_id=resource.tenant_id,
        status=RESOURCE_CHECK_PASSED if passed else RESOURCE_CHECK_FAILED,
        summary="自动检查通过" if passed else "自动检查未通过",
        details={"checks": checks, "snapshot": resource_snapshot(resource_type, resource)},
        checked_by=actor.user_id,
    )
    db.add(row)
    record_audit_event(
        db,
        actor,
        action="resource.check",
        result="success" if passed else "failed",
        resource_type=row.resource_type,
        resource_id=row.resource_id,
        tenant_id=row.tenant_id,
        after={"status": row.status, "checks": checks},
    )
    db.commit()
    db.refresh(row)
    return row
