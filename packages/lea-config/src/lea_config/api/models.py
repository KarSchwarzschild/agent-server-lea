"""RetrievalModel CRUD 路由 — embedding / reranker / vlm / asr 模型注册表。

全局共享配置表，无 TenantMixin，admin-only。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from lea_authkit import Actor, require_actor
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from lea_config.api._auth_helpers import require_admin
from lea_config.database import get_db
from lea_config.models.retrieval_model import RetrievalModel

router = APIRouter(prefix="/retrieval/models", tags=["models"])

_ALLOWED_TYPES = ("embedding", "reranker", "vlm", "asr")


class ModelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    model_type: str = Field(pattern=r"^(embedding|reranker|vlm|asr)$")
    api_base: str = Field(min_length=1)
    api_key: str = ""
    model_name: str = ""
    dimension: int | None = None
    supports: list[str] = []
    tags: list[str] = []
    enabled: bool = True
    is_default: bool = False
    config: dict = {}


class ModelUpdate(BaseModel):
    api_base: str | None = None
    api_key: str | None = None
    model_name: str | None = None
    dimension: int | None = None
    supports: list[str] | None = None
    tags: list[str] | None = None
    enabled: bool | None = None
    is_default: bool | None = None
    config: dict | None = None


def _to_dict(m: RetrievalModel) -> dict:
    return {
        "id": m.id,
        "name": m.name,
        "model_type": m.model_type,
        "api_base": m.api_base,
        "model_name": m.model_name or "",
        "dimension": m.dimension,
        "supports": m.supports or [],
        "tags": m.tags or [],
        "enabled": m.enabled,
        "is_default": m.is_default,
    }


@router.get("")
def list_models(
    model_type: str | None = Query(None),
    enabled_only: bool = False,
    tag: str | None = None,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    require_admin(actor)
    q = db.query(RetrievalModel)
    if model_type:
        q = q.filter(RetrievalModel.model_type == model_type)
    if enabled_only:
        q = q.filter(RetrievalModel.enabled == True)  # noqa: E712
    rows = q.order_by(RetrievalModel.model_type, RetrievalModel.name).all()
    if tag:
        rows = [m for m in rows if tag in (m.tags or [])]
    return {"items": [_to_dict(m) for m in rows]}


@router.get("/{model_id}")
def get_model(
    model_id: int,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    require_admin(actor)
    m = db.query(RetrievalModel).get(model_id)
    if not m:
        raise HTTPException(404, "Model not found")
    return _to_dict(m)


@router.post("", status_code=201)
def create_model(
    body: ModelCreate,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    require_admin(actor)
    if db.query(RetrievalModel).filter(RetrievalModel.name == body.name).first():
        raise HTTPException(409, f"Model '{body.name}' already exists")

    if body.is_default:
        db.query(RetrievalModel).filter(
            RetrievalModel.model_type == body.model_type,
            RetrievalModel.is_default == True,  # noqa: E712
        ).update({"is_default": False})

    m = RetrievalModel(**body.model_dump())
    db.add(m)
    db.commit()
    db.refresh(m)
    return _to_dict(m)


@router.put("/{model_id}")
def update_model(
    model_id: int,
    body: ModelUpdate,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    require_admin(actor)
    m = db.query(RetrievalModel).get(model_id)
    if not m:
        raise HTTPException(404, "Model not found")
    updates = body.model_dump(exclude_unset=True)
    if updates.get("is_default"):
        db.query(RetrievalModel).filter(
            RetrievalModel.model_type == m.model_type,
            RetrievalModel.is_default == True,  # noqa: E712
            RetrievalModel.id != model_id,
        ).update({"is_default": False})
    for k, v in updates.items():
        setattr(m, k, v)
    db.commit()
    db.refresh(m)
    return _to_dict(m)


@router.delete("/{model_id}", status_code=204)
def delete_model(
    model_id: int,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    require_admin(actor)
    m = db.query(RetrievalModel).get(model_id)
    if not m:
        raise HTTPException(404, "Model not found")
    db.delete(m)
    db.commit()
