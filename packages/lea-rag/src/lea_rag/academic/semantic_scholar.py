"""Semantic Scholar API 客户端。"""

from __future__ import annotations

import httpx
import structlog

from lea_rag.academic.schema import Paper, PaperSource, SearchResult

logger = structlog.get_logger(__name__)

_BASE_URL = "https://api.semanticscholar.org/graph/v1"


class SemanticScholarClient:

    def __init__(self, api_key: str | None = None, proxy: str | None = None):
        self._api_key = api_key or None
        self._proxy = proxy or None

    async def search(
        self,
        keyword: str,
        limit: int = 10,
        offset: int = 0,
        timeout: int = 30,
    ) -> SearchResult:
        params = {
            "query": keyword,
            "limit": limit,
            "offset": offset,
            "fields": "title,authors,abstract,year,venue,citationCount,externalIds,url",
        }
        headers = {}
        if self._api_key:
            headers["x-api-key"] = self._api_key

        try:
            async with httpx.AsyncClient(timeout=timeout, proxy=self._proxy) as client:
                resp = await client.get(f"{_BASE_URL}/paper/search", params=params, headers=headers)
                if resp.status_code == 200:
                    return self._parse(resp.json())
                return SearchResult(
                    source=PaperSource.SEMANTIC_SCHOLAR, success=False,
                    error=f"HTTP {resp.status_code}: {resp.text[:200]}",
                )
        except httpx.TimeoutException:
            return SearchResult(source=PaperSource.SEMANTIC_SCHOLAR, success=False, error="timeout")
        except Exception as exc:
            logger.exception("semantic_scholar_error")
            return SearchResult(source=PaperSource.SEMANTIC_SCHOLAR, success=False, error=str(exc))

    @staticmethod
    def _parse(data: dict) -> SearchResult:
        papers: list[Paper] = []
        for item in data.get("data", []):
            authors = [a["name"] for a in item.get("authors", []) if a.get("name")]
            ext_ids = item.get("externalIds") or {}
            papers.append(Paper(
                title=item.get("title", ""),
                authors=authors,
                abstract=item.get("abstract") or "",
                year=item.get("year"),
                journal=item.get("venue", ""),
                doi=ext_ids.get("DOI", ""),
                url=item.get("url", ""),
                cited_count=item.get("citationCount", 0),
                source=PaperSource.SEMANTIC_SCHOLAR,
                paper_id=item.get("paperId", ""),
            ))
        return SearchResult(
            papers=papers,
            total_count=data.get("total", 0),
            source=PaperSource.SEMANTIC_SCHOLAR,
        )
