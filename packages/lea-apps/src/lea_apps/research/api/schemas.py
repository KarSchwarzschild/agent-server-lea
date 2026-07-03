"""综述 API Pydantic 模型。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class ReviewCreate(BaseModel):
    title: str
    topic: str
    requirements: str = ""


class ReviewOut(BaseModel):
    id: int
    title: str
    topic: str
    requirements: str
    status: str
    phase: str
    outline: str
    review_content: str
    error: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ReviewResume(BaseModel):
    """HITL 恢复 — 用户审批或修改后继续。"""
    approved: bool = True
    feedback: str = ""
