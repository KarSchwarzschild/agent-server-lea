"""多源聚合检索 — 并发搜索 + 去重 + 排序。"""

from __future__ import annotations

import asyncio

import structlog

from lea_rag.academic.arxiv import ArxivClient
from lea_rag.academic.schema import Paper, PaperSource, PaperType, SearchResult
from lea_rag.academic.semantic_scholar import SemanticScholarClient
from lea_rag.academic.wanfang import WanfangClient
from lea_rag.config import AcademicSettings

logger = structlog.get_logger(__name__)


class AcademicSearchClient:
    """统一学术检索 — 聚合万方 / arXiv / Semantic Scholar。"""

    def __init__(self, settings: AcademicSettings | None = None):
        s = settings or AcademicSettings()
        self._settings = s
        self._wanfang = WanfangClient(s.wanfang_app_key, s.wanfang_app_secret) if s.wanfang_enabled else None
        self._arxiv = ArxivClient(proxy=s.proxy or None) if s.arxiv_enabled else None
        self._s2 = (
            SemanticScholarClient(api_key=s.semantic_scholar_api_key or None, proxy=s.proxy or None)
            if s.semantic_scholar_enabled else None
        )

    async def search(
        self,
        keyword: str,
        *,
        max_per_source: int = 10,
        paper_type: PaperType = PaperType.HIGH,
        deduplicate: bool = True,
    ) -> SearchResult:
        """并发搜索所有启用的数据源，合并去重后按引用量排序。"""
        if not self._settings.enabled:
            return SearchResult(source=PaperSource.MERGED, success=False, error="academic search disabled")

        tasks: list[asyncio.Task] = []
        labels: list[str] = []

        if self._wanfang:
            tasks.append(asyncio.create_task(self._wanfang.search(keyword, paper_type)))
            labels.append("wanfang")
        if self._arxiv:
            tasks.append(asyncio.create_task(self._arxiv.search(keyword, max_per_source)))
            labels.append("arxiv")
        if self._s2:
            tasks.append(asyncio.create_task(self._s2.search(keyword, max_per_source)))
            labels.append("s2")

        if not tasks:
            return SearchResult(source=PaperSource.MERGED, success=False, error="no sources enabled")

        results = await asyncio.gather(*tasks, return_exceptions=True)

        all_papers: list[Paper] = []
        total = 0
        errors: list[str] = []

        for label, res in zip(labels, results):
            if isinstance(res, Exception):
                errors.append(f"{label}: {res}")
                continue
            if res.success:
                all_papers.extend(res.papers)
                total += res.total_count
            else:
                errors.append(f"{label}: {res.error}")

        if deduplicate:
            all_papers = _deduplicate(all_papers)

        all_papers.sort(key=lambda p: p.cited_count, reverse=True)

        return SearchResult(
            papers=all_papers,
            total_count=total,
            source=PaperSource.MERGED,
            success=len(errors) < len(labels),
            error="; ".join(errors) if errors else "",
        )

    async def search_wanfang(self, keyword: str, **kw) -> SearchResult:
        if not self._wanfang:
            return SearchResult(source=PaperSource.WANFANG, success=False, error="disabled")
        return await self._wanfang.search(keyword, **kw)

    async def search_arxiv(self, keyword: str, **kw) -> SearchResult:
        if not self._arxiv:
            return SearchResult(source=PaperSource.ARXIV, success=False, error="disabled")
        return await self._arxiv.search(keyword, **kw)

    async def search_semantic_scholar(self, keyword: str, **kw) -> SearchResult:
        if not self._s2:
            return SearchResult(source=PaperSource.SEMANTIC_SCHOLAR, success=False, error="disabled")
        return await self._s2.search(keyword, **kw)


def _deduplicate(papers: list[Paper]) -> list[Paper]:
    seen: set[str] = set()
    unique: list[Paper] = []
    for p in papers:
        key = _normalize_title(p.title)
        if key and key not in seen:
            seen.add(key)
            unique.append(p)
    return unique


def _normalize_title(title: str) -> str:
    t = title.lower().strip()
    for ch in ".,;:!?\"'()[]{}<>":
        t = t.replace(ch, "")
    t = " ".join(t.split())
    return t if len(t) > 10 else ""
