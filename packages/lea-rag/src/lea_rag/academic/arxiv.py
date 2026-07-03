"""arXiv API 客户端 — Atom XML 解析。"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import httpx
import structlog

from lea_rag.academic.schema import Paper, PaperSource, SearchResult

logger = structlog.get_logger(__name__)

_BASE_URL = "https://export.arxiv.org/api/query"
_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "opensearch": "http://a9.com/-/spec/opensearch/1.1/",
}


class ArxivClient:

    def __init__(self, proxy: str | None = None):
        self._proxy = proxy or None

    async def search(
        self,
        keyword: str,
        max_results: int = 10,
        start: int = 0,
        timeout: int = 30,
    ) -> SearchResult:
        params = {
            "search_query": f"all:{keyword}",
            "start": start,
            "max_results": max_results,
            "sortBy": "relevance",
            "sortOrder": "descending",
        }
        try:
            async with httpx.AsyncClient(timeout=timeout, proxy=self._proxy) as client:
                resp = await client.get(_BASE_URL, params=params)
                if resp.status_code == 200:
                    return self._parse(resp.text)
                return SearchResult(source=PaperSource.ARXIV, success=False, error=f"HTTP {resp.status_code}")
        except httpx.TimeoutException:
            return SearchResult(source=PaperSource.ARXIV, success=False, error="timeout")
        except Exception as exc:
            logger.exception("arxiv_error")
            return SearchResult(source=PaperSource.ARXIV, success=False, error=str(exc))

    @staticmethod
    def _parse(xml_text: str) -> SearchResult:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return SearchResult(source=PaperSource.ARXIV, success=False, error="XML parse error")

        total_elem = root.find("opensearch:totalResults", _NS)
        total = int(total_elem.text) if total_elem is not None and total_elem.text else 0

        papers: list[Paper] = []
        for entry in root.findall("atom:entry", _NS):
            title_el = entry.find("atom:title", _NS)
            summary_el = entry.find("atom:summary", _NS)
            published_el = entry.find("atom:published", _NS)
            id_el = entry.find("atom:id", _NS)

            authors = [
                a.find("atom:name", _NS).text
                for a in entry.findall("atom:author", _NS)
                if a.find("atom:name", _NS) is not None and a.find("atom:name", _NS).text
            ]

            year = None
            if published_el is not None and published_el.text:
                try:
                    year = int(published_el.text[:4])
                except ValueError:
                    pass

            url = (id_el.text or "") if id_el is not None else ""
            paper_id = url.split("/abs/")[-1] if "/abs/" in url else ""

            papers.append(Paper(
                title=(title_el.text or "").strip().replace("\n", " "),
                authors=authors,
                abstract=(summary_el.text or "").strip().replace("\n", " ") if summary_el is not None else "",
                year=year,
                url=url,
                source=PaperSource.ARXIV,
                paper_id=paper_id,
            ))

        return SearchResult(papers=papers, total_count=total, source=PaperSource.ARXIV)
