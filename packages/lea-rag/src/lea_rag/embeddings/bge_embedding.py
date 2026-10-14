"""BGE-M3 Embedding — 对接自部署的 BGE-M3 服务（dense + sparse）。"""

from __future__ import annotations

import httpx
import structlog
from llama_index.core.embeddings import BaseEmbedding
from pydantic import Field

from lea_rag.config import EmbeddingSettings

logger = structlog.get_logger()


class BGEEmbedding(BaseEmbedding):
    """对接 BGE-M3 HTTP 服务的 LlamaIndex Embedding。

    BGE-M3 服务接口：POST /embed  body: {"texts": [...]}
    返回：{"dense": [[...]], "sparse": [{...}]}
    """

    api_base: str = Field(default="http://localhost:8012")
    _client: httpx.Client | None = None

    def __init__(self, api_base: str | None = None, **kwargs):
        cfg = EmbeddingSettings()
        base = api_base or cfg.api_base
        super().__init__(model_name="bge-m3", embed_batch_size=32, api_base=base, **kwargs)

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=60.0)
        return self._client

    def _get_text_embedding(self, text: str) -> list[float]:
        return self._get_text_embeddings([text])[0]

    def _get_text_embeddings(self, texts: list[str]) -> list[list[float]]:
        try:
            resp = self.client.post(f"{self.api_base}/embed", json={"texts": texts})
            resp.raise_for_status()
            data = resp.json()
            return data["dense"]
        except Exception as exc:
            logger.error("bge_embed_failed", error=str(exc))
            raise

    def _get_query_embedding(self, query: str) -> list[float]:
        return self._get_text_embedding(query)

    async def _aget_text_embedding(self, text: str) -> list[float]:
        return self._get_text_embedding(text)

    async def _aget_query_embedding(self, query: str) -> list[float]:
        return self._get_text_embedding(query)
