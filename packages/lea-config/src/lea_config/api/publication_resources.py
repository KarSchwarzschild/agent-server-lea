"""User-side publication submission for config resources."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from lea_authkit import Actor, require_actor
from pydantic import BaseModel
from sqlalchemy.orm import Session

from lea_config.database import DEFAULT_TENANT_ID, get_db
from lea_config.models.agent import Agent
from lea_config.models.mcp_server import MCPServer
from lea_config.models.publication import PUBLICATION_TARGETS, PublicationRequest
from lea_config.models.tool import Tool
from lea_config.services.publication import (
    RESOURCE_AGENT,
    RESOURCE_MCP_SERVER,
    RESOURCE_TOOL,
    cancel_request,
    publication_request_dict,
    resource_scope,
    submit_resource_request,
)

router = APIRouter(tags=["publication-submissions"])


class ResourcePublicationBody(BaseModel):
    target_scope: str
    note: str = ""


class ResourcePublicationCancelBody(BaseModel):
    note: str = ""


def _load_resource(db: Session, actor: Actor, resource_type: str, resource_id: int):
    models = {
        RESOURCE_AGENT: Agent,
        RESOURCE_TOOL: Tool,
        RESOURCE_MCP_SERVER: MCPServer,
    }
    model = models[resource_type]
    resource = db.query(model).filter(model.id == resource_id).first()
    if resource is None:
        raise HTTPException(404, "资源不存在")
    if actor.is_platform_admin:
        return resource
    if resource.tenant_id not in (actor.tenant_id, DEFAULT_TENANT_ID):
        raise HTTPException(404, "资源不存在")
    if resource.owner_scope == "user" and resource.owner_user_id != actor.user_id:
        if not actor.is_tenant_admin:
            raise HTTPException(403, "只能申请自己创建的资源")
    if resource.owner_scope == "platform" and resource.tenant_id == DEFAULT_TENANT_ID:
        if not actor.is_platform_admin:
            raise HTTPException(403, "全平台资源不能由租户用户发起发布申请")
    return resource


def _cancel(resource_type: str, resource_id: int, request_id: int, note: str, db: Session, actor: Actor):
    """用户侧撤回自己的申请。

    这里**故意不加** require_admin：撤销的授权判断全在 cancel_request 里（必须是
    申请人，或平台管理员）。通用管理路由 /publication-requests/{id}/cancel 两道都加，
    于是申请人倒在角色门上、管理员倒在"只有申请人"上，实际只有平台管理员能撤。
    这条路由就是补那个缺口，形状跟 skills.py 的 cancel_skill_publication_request 一致。
    """
    request = (
        db.query(PublicationRequest)
        .filter(
            PublicationRequest.id == request_id,
            PublicationRequest.resource_type == resource_type,
            PublicationRequest.resource_id == str(resource_id),
        )
        .first()
    )
    if request is None:
        raise HTTPException(404, "申请不存在")
    # 租户归属 / 申请人 / 状态三道检查都在 cancel_request 里，跨租户同样返回 404。
    return cancel_request(db, actor, request_id, note)


def _submit(resource_type: str, resource_id: int, body: ResourcePublicationBody, db: Session, actor: Actor):
    if body.target_scope not in PUBLICATION_TARGETS:
        raise HTTPException(422, "target_scope 只能是 tenant 或 global")
    resource = _load_resource(db, actor, resource_type, resource_id)
    current = resource_scope(resource)
    if current == "global":
        raise HTTPException(409, "资源已经是全平台可见")
    if current == "tenant" and body.target_scope == "tenant":
        raise HTTPException(409, "资源已经是租户内可见")
    return submit_resource_request(db, actor, resource_type, resource, body.target_scope, body.note)


@router.post("/agents/{resource_id}/publication-requests", status_code=201)
def submit_agent_publication(
    resource_id: int,
    body: ResourcePublicationBody,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    return publication_request_dict(_submit(RESOURCE_AGENT, resource_id, body, db, actor))


@router.post("/agents/{resource_id}/publication-requests/{request_id}/cancel")
def cancel_agent_publication(
    resource_id: int,
    request_id: int,
    body: ResourcePublicationCancelBody,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    return publication_request_dict(_cancel(RESOURCE_AGENT, resource_id, request_id, body.note, db, actor))


@router.post("/tools/{resource_id}/publication-requests", status_code=201)
def submit_tool_publication(
    resource_id: int,
    body: ResourcePublicationBody,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    return publication_request_dict(_submit(RESOURCE_TOOL, resource_id, body, db, actor))


@router.post("/tools/{resource_id}/publication-requests/{request_id}/cancel")
def cancel_tool_publication(
    resource_id: int,
    request_id: int,
    body: ResourcePublicationCancelBody,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    return publication_request_dict(_cancel(RESOURCE_TOOL, resource_id, request_id, body.note, db, actor))


@router.post("/mcp-servers/{resource_id}/publication-requests", status_code=201)
def submit_mcp_publication(
    resource_id: int,
    body: ResourcePublicationBody,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    return publication_request_dict(_submit(RESOURCE_MCP_SERVER, resource_id, body, db, actor))


@router.post("/mcp-servers/{resource_id}/publication-requests/{request_id}/cancel")
def cancel_mcp_publication(
    resource_id: int,
    request_id: int,
    body: ResourcePublicationCancelBody,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    return publication_request_dict(_cancel(RESOURCE_MCP_SERVER, resource_id, request_id, body.note, db, actor))
