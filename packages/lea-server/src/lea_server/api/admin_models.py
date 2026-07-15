"""Admin 模型注册表 API — 管理 embedding / reranker / vlm / asr 模型。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from lea_server.core.deps import admin_required
from lea_server.database import get_db
from lea_server.models.knowledge import RetrievalModel
from lea_server.models.models import User

router = APIRouter(prefix="/admin/retrieval/models", tags=["admin-models"])


class ModelCreateRequest(BaseModel):
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


class ModelUpdateRequest(BaseModel):
    api_base: str | None = None
    api_key: str | None = None
    model_name: str | None = None
    dimension: int | None = None
    supports: list[str] | None = None
    tags: list[str] | None = None
    enabled: bool | None = None
    is_default: bool | None = None
    config: dict | None = None


class ModelOut(BaseModel):
    id: int
    name: str
    model_type: str
    api_base: str
    model_name: str = ""
    dimension: int | None = None
    supports: list[str] = []
    tags: list[str] = []
    enabled: bool = True
    is_default: bool = False


def _model_to_out(m: RetrievalModel) -> ModelOut:
    return ModelOut(
        id=m.id, name=m.name, model_type=m.model_type,
        api_base=m.api_base, model_name=m.model_name or "",
        dimension=m.dimension, supports=m.supports or [],
        tags=m.tags or [], enabled=m.enabled, is_default=m.is_default,
    )


@router.post("", response_model=ModelOut)
def create_model(
    body: ModelCreateRequest,
    db: Session = Depends(get_db),
    _user: User = Depends(admin_required),
):
    existing = db.query(RetrievalModel).filter(
        RetrievalModel.name == body.name
    ).first()
    if existing:
        raise HTTPException(409, f"模型 '{body.name}' 已存在")

    if body.is_default:
        db.query(RetrievalModel).filter(
            RetrievalModel.model_type == body.model_type,
            RetrievalModel.is_default == True,
        ).update({"is_default": False})

    model = RetrievalModel(**body.model_dump())
    db.add(model)
    db.commit()
    db.refresh(model)
    return _model_to_out(model)


@router.get("", response_model=list[ModelOut])
def list_models(
    model_type: str | None = None,
    enabled_only: bool = False,
    tag: str | None = None,
    db: Session = Depends(get_db),
    _user: User = Depends(admin_required),
):
    q = db.query(RetrievalModel)
    if model_type:
        q = q.filter(RetrievalModel.model_type == model_type)
    if enabled_only:
        q = q.filter(RetrievalModel.enabled == True)
    models = q.order_by(RetrievalModel.model_type, RetrievalModel.name).all()
    if tag:
        models = [m for m in models if tag in (m.tags or [])]
    return [_model_to_out(m) for m in models]


@router.get("/{model_id}", response_model=ModelOut)
def get_model(
    model_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(admin_required),
):
    model = db.query(RetrievalModel).get(model_id)
    if not model:
        raise HTTPException(404, "模型不存在")
    return _model_to_out(model)


@router.put("/{model_id}", response_model=ModelOut)
def update_model(
    model_id: int,
    body: ModelUpdateRequest,
    db: Session = Depends(get_db),
    _user: User = Depends(admin_required),
):
    model = db.query(RetrievalModel).get(model_id)
    if not model:
        raise HTTPException(404, "模型不存在")

    updates = body.model_dump(exclude_none=True)
    if updates.get("is_default"):
        db.query(RetrievalModel).filter(
            RetrievalModel.model_type == model.model_type,
            RetrievalModel.is_default == True,
            RetrievalModel.id != model_id,
        ).update({"is_default": False})

    for k, v in updates.items():
        setattr(model, k, v)
    db.commit()
    db.refresh(model)
    return _model_to_out(model)


@router.delete("/{model_id}")
def delete_model(
    model_id: int,
    db: Session = Depends(get_db),
    _user: User = Depends(admin_required),
):
    model = db.query(RetrievalModel).get(model_id)
    if not model:
        raise HTTPException(404, "模型不存在")
    db.delete(model)
    db.commit()
    return {"message": f"已删除模型 '{model.name}'"}
