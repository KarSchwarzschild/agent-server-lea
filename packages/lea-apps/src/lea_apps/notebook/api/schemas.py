"""Notebook Pydantic schemas。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class SourceOut(BaseModel):
    id: int
    filename: str
    file_type: str
    file_size: int
    total_pages: int
    status: str
    summary: str = ""
    key_topics: list[str] = []
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class SourceDetail(SourceOut):
    parsed_content: str = ""


class SourceStatus(BaseModel):
    id: int
    status: str
    celery_task_id: str = ""


class ChatRequest(BaseModel):
    question: str
    source_ids: list[int] | None = None
    kb_ids: list[int] | None = None
    document_ids: list[int] | None = None
    session_id: int | None = None


class SessionOut(BaseModel):
    id: int
    title: str
    source_ids: list[int]
    created_at: datetime | None = None

    model_config = {"from_attributes": True}


class MessageOut(BaseModel):
    id: int
    role: str
    content: str
    sources: list[dict] = []
    created_at: datetime | None = None

    model_config = {"from_attributes": True}
