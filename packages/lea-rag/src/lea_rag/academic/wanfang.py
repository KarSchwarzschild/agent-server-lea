"""万方 API 客户端 — HMAC-SHA256 签名认证。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
import uuid

import httpx
import structlog

from lea_rag.academic.schema import Paper, PaperSource, PaperType, SearchResult

logger = structlog.get_logger(__name__)

_BASE_URL = "https://api.wanfangdata.com.cn"


class WanfangClient:

    def __init__(self, app_key: str, app_secret: str):
        self._app_key = app_key
        self._app_secret = app_secret

    def _sign(
        self, method: str, path: str, headers: dict[str, str], body: bytes
    ) -> tuple[str, str, str]:
        parts: list[str] = [method.upper()]

        content_md5 = (
            base64.b64encode(hashlib.md5(body).digest()).decode() if body else ""
        )
        parts.append(content_md5)
        parts.append(headers.get("Content-Type", ""))
        parts.append("")

        ca = {k: v for k, v in headers.items()
              if k.startswith("X-Ca-") and k not in ("X-Ca-Signature", "X-Ca-Signature-Headers")}
        sorted_keys = sorted(ca)
        for k in sorted_keys:
            parts.append(f"{k}:{ca[k]}")
        parts.append(path)

        sig = base64.b64encode(
            hmac.new(
                self._app_secret.encode(), "\n".join(parts).encode(), hashlib.sha256
            ).digest()
        ).decode()
        return sig, content_md5, ",".join(sorted_keys)

    async def search(
        self,
        keyword: str,
        paper_type: PaperType = PaperType.HIGH,
        page: int = 1,
        timeout: int = 30,
    ) -> SearchResult:
        path = "/topic_read/paper"
        body = json.dumps(
            {"keyword": keyword, "page": page, "type": paper_type.value},
            ensure_ascii=False,
        ).encode()

        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "X-Ca-Version": "1",
            "X-Ca-AppKey": self._app_key,
            "X-Ca-Timestamp": str(int(time.time() * 1000)),
            "X-Ca-Nonce": str(uuid.uuid4()),
        }
        sig, md5, sig_headers = self._sign("POST", path, headers, body)
        headers["X-Ca-Signature"] = sig
        headers["X-Ca-Signature-Headers"] = sig_headers
        if md5:
            headers["Content-MD5"] = md5

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                resp = await client.post(f"{_BASE_URL}{path}", headers=headers, content=body)
                if resp.status_code == 200:
                    return self._parse(resp.json())
                logger.warning("wanfang_http_error", status=resp.status_code)
                return SearchResult(source=PaperSource.WANFANG, success=False, error=resp.text[:200])
        except httpx.TimeoutException:
            return SearchResult(source=PaperSource.WANFANG, success=False, error="timeout")
        except Exception as exc:
            logger.exception("wanfang_error")
            return SearchResult(source=PaperSource.WANFANG, success=False, error=str(exc))

    @staticmethod
    def _parse(data: dict) -> SearchResult:
        papers: list[Paper] = []
        total = 0
        if "pageInfo" in data:
            info = data["pageInfo"]
            total = info.get("totalCount", 0)
            for item in info.get("pageDatas", []):
                p = item.get("periodical", {})
                pid = p.get("id", "")
                url = p.get("url", "") or (f"https://d.wanfangdata.com.cn/periodical/{pid}" if pid else "")
                papers.append(Paper(
                    title=p.get("title", ""),
                    authors=p.get("creators") or p.get("authors", []),
                    abstract=p.get("abstracts", ""),
                    keywords=p.get("keywords", []),
                    year=p.get("publishYear"),
                    journal=p.get("periodicalTitle", "") or p.get("srcDatabase", ""),
                    doi=p.get("doi", ""),
                    url=url,
                    cited_count=p.get("citedCount", 0),
                    source=PaperSource.WANFANG,
                    paper_id=pid,
                ))
        return SearchResult(papers=papers, total_count=total, source=PaperSource.WANFANG)
