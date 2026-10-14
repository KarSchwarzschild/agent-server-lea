"""Published-resource lifecycle for the administration console."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from lea_authkit import Actor, require_actor
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from lea_config.api._auth_helpers import require_admin
from lea_config.database import get_db
from lea_config.models.publication import ResourcePublication
from lea_config.services.governance import publication_dict, transition_publication

router = APIRouter(prefix="/publications", tags=["publications"])


class PublicationActionBody(BaseModel):
    reason: str = Field(min_length=1, max_length=1000)


@router.get("")
def list_publications(
    resource_type: str | None = None,
    status: str | None = None,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    require_admin(actor)
    query = db.query(ResourcePublication)
    if not actor.is_platform_admin:
        query = query.filter(ResourcePublication.tenant_id == actor.tenant_id)
    if resource_type:
        query = query.filter(ResourcePublication.resource_type == resource_type)
    if status:
        query = query.filter(ResourcePublication.status == status)
    total = query.count()
    rows = (
        query.order_by(ResourcePublication.updated_at.desc(), ResourcePublication.id.desc())
        .offset((page - 1) * size)
        .limit(size)
        .all()
    )
    return {
        "items": [publication_dict(row) for row in rows],
        "total": total,
        "page": page,
        "size": size,
    }


def _transition(
    resource_type: str,
    resource_id: str,
    target_status: str,
    body: PublicationActionBody,
    db: Session,
    actor: Actor,
):
    require_admin(actor)
    return publication_dict(
        transition_publication(
            db, actor, resource_type, resource_id, target_status, body.reason
        )
    )


@router.post("/{resource_type}/{resource_id}/suspend")
def suspend_publication(
    resource_type: str,
    resource_id: str,
    body: PublicationActionBody,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    return _transition(resource_type, resource_id, "suspended", body, db, actor)


@router.post("/{resource_type}/{resource_id}/resume")
def resume_publication(
    resource_type: str,
    resource_id: str,
    body: PublicationActionBody,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    return _transition(resource_type, resource_id, "active", body, db, actor)


@router.post("/{resource_type}/{resource_id}/archive")
def archive_publication(
    resource_type: str,
    resource_id: str,
    body: PublicationActionBody,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    return _transition(resource_type, resource_id, "archived", body, db, actor)
