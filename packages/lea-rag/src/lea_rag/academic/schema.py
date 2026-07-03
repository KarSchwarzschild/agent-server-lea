"""学术检索数据模型。"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class PaperSource(str, Enum):
    WANFANG = "wanfang"
    ARXIV = "arxiv"
    SEMANTIC_SCHOLAR = "semantic_scholar"
    MERGED = "merged"


class PaperType(str, Enum):
    HIGH = "HIGH"
    NEW = "NEW"
    DEGREE = "DEGREE"
    REVIEW = "REVIEW"


class Paper(BaseModel):
    title: str
    authors: list[str] = Field(default_factory=list)
    abstract: str = ""
    keywords: list[str] = Field(default_factory=list)
    year: int | None = None
    journal: str = ""
    doi: str = ""
    url: str = ""
    cited_count: int = 0
    source: PaperSource = PaperSource.MERGED
    paper_id: str = ""


class SearchResult(BaseModel):
    papers: list[Paper] = Field(default_factory=list)
    total_count: int = 0
    source: PaperSource = PaperSource.MERGED
    success: bool = True
    error: str = ""
