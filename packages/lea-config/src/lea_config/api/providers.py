"""Tenant-scoped LLM provider management."""

from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query
from lea_authkit import Actor, require_actor
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from lea_config.api._auth_helpers import require_admin
from lea_config.database import get_db
from lea_config.models.llm_provider import LLMProvider
from lea_config.services.publication import record_audit_event

router = APIRouter(prefix="/providers", tags=["providers"])


def _provider_host_allowlist() -> set[str]:
    return {
        item.strip().lower()
        for item in os.getenv("LEA_PROVIDER_HOST_ALLOWLIST", "").split(",")
        if item.strip()
    }


def _reject_provider_ip(value: str) -> None:
    address = ipaddress.ip_address(value)
    if not address.is_global:
        raise HTTPException(400, "Provider URL resolves to a non-public address")


def _validate_provider_url(url: str, *, resolve: bool) -> None:
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise HTTPException(400, "Provider URL must be an absolute http(s) URL")
    if parsed.username or parsed.password:
        raise HTTPException(400, "Provider URL must not contain credentials")
    host = parsed.hostname.lower()
    if host in _provider_host_allowlist():
        return
    if host == "localhost" or host.endswith(".localhost"):
        raise HTTPException(400, "Provider URL must not target localhost")
    try:
        _reject_provider_ip(host)
        return
    except ValueError:
        pass
    if not resolve:
        return
    try:
        addresses = socket.getaddrinfo(host, parsed.port or 443, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise HTTPException(400, f"Provider hostname could not be resolved: {exc}") from exc
    for address in {item[4][0] for item in addresses}:
        _reject_provider_ip(address)


def _validate_provider_transport(url: str, api_key: str) -> None:
    parsed = urlsplit(url)
    if (
        api_key
        and parsed.scheme != "https"
        and (parsed.hostname or "").lower() not in _provider_host_allowlist()
    ):
        raise HTTPException(400, "Provider API keys require HTTPS")


class ProviderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    base_url: str = Field(min_length=1, max_length=500)
    api_key: str = ""
    models: str = ""
    model_limits: dict[str, int] | None = None
    is_default: bool = False
    enabled: bool = True
    reason: str = Field(min_length=1, max_length=1000)


class ProviderUpdate(BaseModel):
    name: str | None = None
    base_url: str | None = None
    api_key: str | None = None
    models: str | None = None
    model_limits: dict[str, int] | None = None
    is_default: bool | None = None
    enabled: bool | None = None
    reason: str = Field(min_length=1, max_length=1000)


def _to_dict(row: LLMProvider) -> dict:
    return {
        "id": row.id,
        "tenant_id": row.tenant_id,
        "name": row.name,
        "base_url": row.base_url,
        "has_api_key": bool(row.api_key),
        "models": [item.strip() for item in (row.models or "").split(",") if item.strip()],
        "model_limits": row.model_limits or {},
        "is_default": bool(row.is_default),
        "enabled": bool(row.enabled),
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def _load(db: Session, actor: Actor, provider_id: int) -> LLMProvider:
    query = db.query(LLMProvider).filter(LLMProvider.id == provider_id)
    if not actor.is_platform_admin:
        query = query.filter(LLMProvider.tenant_id == actor.tenant_id)
    row = query.first()
    if row is None:
        raise HTTPException(404, "Provider not found")
    return row


@router.get("")
def list_providers(
    db: Session = Depends(get_db), actor: Actor = Depends(require_actor)
):
    require_admin(actor)
    query = db.query(LLMProvider)
    if not actor.is_platform_admin:
        query = query.filter(LLMProvider.tenant_id == actor.tenant_id)
    return {"items": [_to_dict(row) for row in query.order_by(LLMProvider.id).all()]}


@router.post("", status_code=201)
def create_provider(
    body: ProviderCreate,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    require_admin(actor)
    _validate_provider_url(body.base_url, resolve=False)
    _validate_provider_transport(body.base_url, body.api_key)
    if body.is_default and not body.enabled:
        raise HTTPException(409, "A disabled provider cannot be the default")
    replaced_default_ids = []
    if body.is_default:
        replaced_default_ids = [
            item[0]
            for item in db.query(LLMProvider.id).filter_by(
                tenant_id=actor.tenant_id, is_default=True
            )
        ]
        db.query(LLMProvider).filter_by(tenant_id=actor.tenant_id).update(
            {"is_default": False}
        )
    row = LLMProvider(
        tenant_id=actor.tenant_id, **body.model_dump(exclude={"reason"})
    )
    db.add(row)
    db.flush()
    after = _to_dict(row)
    record_audit_event(
        db,
        actor,
        action="provider.create",
        resource_type="provider",
        resource_id=str(row.id),
        tenant_id=row.tenant_id,
        before={"replaced_default_provider_ids": replaced_default_ids},
        after=after,
        reason=body.reason,
    )
    db.commit()
    db.refresh(row)
    response = _to_dict(row)
    response["impact"] = {"replaced_default_provider_ids": replaced_default_ids}
    return response


@router.put("/{provider_id}")
def update_provider(
    provider_id: int,
    body: ProviderUpdate,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    require_admin(actor)
    row = _load(db, actor, provider_id)
    before = _to_dict(row)
    updates = body.model_dump(exclude_unset=True, exclude={"reason"})
    if "base_url" in updates:
        _validate_provider_url(updates["base_url"], resolve=False)
    _validate_provider_transport(
        updates.get("base_url", row.base_url), updates.get("api_key", row.api_key or "")
    )
    next_enabled = updates.get("enabled", bool(row.enabled))
    next_default = updates.get("is_default", bool(row.is_default))
    if next_default and not next_enabled:
        raise HTTPException(409, "A disabled provider cannot be the default")
    replaced_default_ids = []
    if updates.get("is_default"):
        replaced_default_ids = [
            item[0]
            for item in db.query(LLMProvider.id).filter(
                LLMProvider.tenant_id == row.tenant_id,
                LLMProvider.id != row.id,
                LLMProvider.is_default.is_(True),
            )
        ]
        db.query(LLMProvider).filter(
            LLMProvider.tenant_id == row.tenant_id,
            LLMProvider.id != row.id,
        ).update({"is_default": False})
    before["replaced_default_provider_ids"] = replaced_default_ids
    for key, value in updates.items():
        setattr(row, key, value)
    db.flush()
    record_audit_event(
        db,
        actor,
        action="provider.update",
        resource_type="provider",
        resource_id=str(row.id),
        tenant_id=row.tenant_id,
        before=before,
        after=_to_dict(row),
        reason=body.reason,
    )
    db.commit()
    db.refresh(row)
    response = _to_dict(row)
    response["impact"] = {"replaced_default_provider_ids": replaced_default_ids}
    return response


@router.delete("/{provider_id}", status_code=204)
def delete_provider(
    provider_id: int,
    reason: str = Query(min_length=1, max_length=1000),
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    require_admin(actor)
    row = _load(db, actor, provider_id)
    _validate_provider_url(row.base_url, resolve=True)
    _validate_provider_transport(row.base_url, row.api_key or "")
    if row.enabled or row.is_default:
        record_audit_event(
            db,
            actor,
            action="provider.delete",
            result="blocked",
            resource_type="provider",
            resource_id=str(row.id),
            tenant_id=row.tenant_id,
            before=_to_dict(row),
            reason=reason,
        )
        db.commit()
        raise HTTPException(
            409, "Disable the provider and remove its default status before deletion"
        )
    before = _to_dict(row)
    record_audit_event(
        db,
        actor,
        action="provider.delete",
        resource_type="provider",
        resource_id=str(row.id),
        tenant_id=row.tenant_id,
        before=before,
        reason=reason,
    )
    db.delete(row)
    db.commit()


@router.post("/{provider_id}/test")
async def test_provider(
    provider_id: int,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    require_admin(actor)
    row = _load(db, actor, provider_id)
    headers = {"Authorization": f"Bearer {row.api_key}"} if row.api_key else {}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(row.base_url.rstrip("/") + "/models", headers=headers)
        result = {
            "ok": response.status_code < 400,
            "status_code": response.status_code,
            "message": "连接成功" if response.status_code < 400 else response.text[:200],
        }
    except httpx.HTTPError as exc:
        result = {"ok": False, "status_code": None, "message": str(exc)}
    record_audit_event(
        db,
        actor,
        action="provider.test",
        result="success" if result["ok"] else "failed",
        resource_type="provider",
        resource_id=str(row.id),
        tenant_id=row.tenant_id,
        after={"ok": result["ok"], "status_code": result["status_code"]},
    )
    db.commit()
    return result
