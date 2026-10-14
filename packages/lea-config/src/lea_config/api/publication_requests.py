"""Role-scoped publication queue for users and administrators."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from lea_authkit import Actor, require_actor
from pydantic import BaseModel
from sqlalchemy.orm import Session

from lea_config.api._auth_helpers import require_admin
from lea_config.database import get_db
from lea_config.models.publication import PUBLICATION_PENDING, PublicationRequest
from lea_config.services.publication import (
    cancel_request,
    load_visible_request,
    publication_request_dict,
    review_request,
    scoped_requests,
    validate_status,
)

router = APIRouter(prefix="/publication-requests", tags=["publication-requests"])


class PublicationReviewBody(BaseModel):
    decision: str
    note: str = ""


class PublicationCancelBody(BaseModel):
    note: str = ""


@router.get("")
def list_publication_requests(
    status: str = Query(PUBLICATION_PENDING),
    resource_type: str | None = None,
    target_scope: str | None = None,
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    require_admin(actor)
    validate_status(status)
    query = scoped_requests(db, actor).filter_by(status=status)
    if resource_type:
        query = query.filter_by(resource_type=resource_type)
    if target_scope:
        query = query.filter_by(target_scope=target_scope)
    total = query.count()
    rows = (
        query.order_by(PublicationRequest.created_at.desc(), PublicationRequest.id.desc())
        .offset((page - 1) * size)
        .limit(size)
        .all()
    )
    return {
        "requests": [publication_request_dict(row) for row in rows],
        "total": total,
        "page": page,
        "size": size,
    }


@router.get("/{request_id}")
def get_publication_request(
    request_id: int,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    require_admin(actor)
    return publication_request_dict(load_visible_request(db, actor, request_id))


@router.post("/{request_id}/review")
def review_publication_request(
    request_id: int,
    body: PublicationReviewBody,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    require_admin(actor)
    return publication_request_dict(
        review_request(db, actor, request_id, body.decision, body.note)
    )


@router.post("/{request_id}/cancel")
def cancel_publication_request(
    request_id: int,
    body: PublicationCancelBody,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    require_admin(actor)
    return publication_request_dict(cancel_request(db, actor, request_id, body.note))
