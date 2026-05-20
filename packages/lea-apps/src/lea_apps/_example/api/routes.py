"""示例App的独立API — 资源CRUD。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from lea_server.database import get_db

router = APIRouter(tags=["example"])


class ResourceCreate(BaseModel):
    title: str
    content: str = ""


class ResourceResponse(BaseModel):
    id: int
    title: str
    content: str
    created_by: str | None = None

    model_config = {"from_attributes": True}


@router.get("/resources")
async def list_resources(db: Session = Depends(get_db)):
    from lea_apps._example.models.models import ExampleResource

    return db.query(ExampleResource).order_by(ExampleResource.id.desc()).all()


@router.post("/resources", response_model=ResourceResponse)
async def create_resource(data: ResourceCreate, db: Session = Depends(get_db)):
    from lea_apps._example.models.models import ExampleResource

    resource = ExampleResource(title=data.title, content=data.content)
    db.add(resource)
    db.commit()
    db.refresh(resource)
    return resource
