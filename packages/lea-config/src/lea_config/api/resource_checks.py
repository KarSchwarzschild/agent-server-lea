"""Automatic checks for publishable Tool and MCP resources."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from lea_authkit import Actor, require_actor
from sqlalchemy.orm import Session

from lea_config.api._auth_helpers import require_admin
from lea_config.database import get_db
from lea_config.models.publication import ResourceCheck
from lea_config.services.governance import check_dict, run_check
from lea_config.services.publication import RESOURCE_MCP_SERVER, RESOURCE_TOOL

router = APIRouter(tags=["resource-checks"])


@router.post("/tools/{resource_id}/checks", status_code=201)
async def check_tool(
    resource_id: int,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    require_admin(actor)
    return check_dict(await run_check(db, actor, RESOURCE_TOOL, resource_id))


@router.post("/mcp-servers/{resource_id}/checks", status_code=201)
async def check_mcp_server(
    resource_id: int,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    require_admin(actor)
    return check_dict(await run_check(db, actor, RESOURCE_MCP_SERVER, resource_id))


@router.get("/resources/{resource_type}/{resource_id}/checks")
def list_resource_checks(
    resource_type: str,
    resource_id: str,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    require_admin(actor)
    query = db.query(ResourceCheck).filter_by(
        resource_type=resource_type, resource_id=resource_id
    )
    if not actor.is_platform_admin:
        query = query.filter(ResourceCheck.tenant_id == actor.tenant_id)
    total = query.count()
    rows = (
        query.order_by(ResourceCheck.created_at.desc(), ResourceCheck.id.desc())
        .offset((page - 1) * size)
        .limit(size)
        .all()
    )
    return {
        "items": [check_dict(row) for row in rows],
        "total": total,
        "page": page,
        "size": size,
    }
