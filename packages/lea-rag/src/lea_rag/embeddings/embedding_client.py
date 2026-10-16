"""通用 Embedding 客户端 — 兼容多种 API 格式。

支持的 API 协议（自动探测）：
1. OpenAI 格式: POST {api_base}/v1/embeddings  body: {"input": [...], "model": "..."}
   返回: {"data": [{"embedding": [...]}]}
2. TEI/Infinity 格式: POST {api_base}/embed  body: {"inputs": [...], "model": "..."}
   返回: {"data": [{"embedding": [...]}]} 或 {"dense": [[...]]}

如果 api_base 以 /v1/embeddings 结尾 → 直接 POST 到该地址，OpenAI 格式
否则 → POST {api_base}/embed，TEI 格式
"""

from __future__ import annotations

import base64
import time
from typing import Any

import httpx
import structlog

from lea_rag.config import EmbeddingSettings

logger = structlog.get_logger()

_settings: EmbeddingSettings | None = None


def _get_settings() -> EmbeddingSettings:
    global _settings
    if _settings is None:
        _settings = EmbeddingSettings()
        if not _settings.api_key:
            from lea_rag.config import get_tenant_api_key
            _settings.api_key = get_tenant_api_key()
    return _settings


class EmbeddingClient:
    """通用 Embedding 客户端 — 兼容 OpenAI / TEI / Infinity 格式。"""

    def __init__(self, api_base: str | None = None, model: str | None = None, api_key: str | None = None):
        cfg = _get_settings()
        self.api_base = (api_base or cfg.api_base).rstrip("/")
        self.model = model or cfg.model
        self.dimension = cfg.dimension
        self._api_key = api_key or cfg.api_key
        self._client: httpx.Client | None = None
        self._aclient: httpx.AsyncClient | None = None

    @property
    def _is_openai_format(self) -> bool:
        return "/v1/embeddings" in self.api_base or "/embeddings" in self.api_base

    @property
    def _url(self) -> str:
        if self._is_openai_format:
            return self.api_base if self.api_base.endswith("/embeddings") else f"{self.api_base}/v1/embeddings"
        return f"{self.api_base}/embed"

    @property
    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self._api_key and self._api_key != "EMPTY":
            h["Authorization"] = f"Bearer {self._api_key}"
        return h

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=60.0, headers=self._headers)
        return self._client

    @property
    def aclient(self) -> httpx.AsyncClient:
        if self._aclient is None:
            self._aclient = httpx.AsyncClient(timeout=60.0, headers=self._headers)
        return self._aclient

    def _build_payload(self, inputs: list[Any]) -> dict:
        if self._is_openai_format:
            return {"input": inputs, "model": self.model}
        return {"inputs": inputs, "model": self.model}

    def _parse_response(self, data: dict) -> list[list[float]]:
        if "data" in data:
            return [item["embedding"] for item in data["data"]]
        if "dense" in data:
            return data["dense"]
        raise ValueError(f"Unexpected response format: {list(data.keys())}")

    # 指标埋在 _post / _apost 这两个咽喉点上，而不是在 embed_texts / embed_images /
    # aembed_texts / aembed_images 四个入口各记一次：所有调用最终都收口到这里，
    # 埋在咽喉上就不会漏，将来加新的 embed_xxx 入口也自动覆盖。
    #
    # 同一个 import 放在函数体里是为了避开循环导入 —— metrics 不依赖 embeddings，
    # 但 lea_rag.metrics 在模块级会建一堆 Collector，而这个模块可能被 Celery
    # worker 以不同顺序加载。放函数里最稳，且只付一次字典查找的代价。
    def _post(self, inputs: list[Any]) -> list[list[float]]:
        from lea_rag.metrics import record_embedding

        started = time.perf_counter()
        try:
            resp = self.client.post(self._url, json=self._build_payload(inputs))
            resp.raise_for_status()
            out = self._parse_response(resp.json())
        except Exception:
            record_embedding("error", time.perf_counter() - started)
            raise
        record_embedding("success", time.perf_counter() - started)
        return out

    async def _apost(self, inputs: list[Any]) -> list[list[float]]:
        from lea_rag.metrics import record_embedding

        started = time.perf_counter()
        try:
            resp = await self.aclient.post(self._url, json=self._build_payload(inputs))
            resp.raise_for_status()
            out = self._parse_response(resp.json())
        except Exception:
            record_embedding("error", time.perf_counter() - started)
            raise
        record_embedding("success", time.perf_counter() - started)
        return out

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            return self._post(texts)
        except Exception as exc:
            logger.error("embed_texts_failed", error=str(exc), count=len(texts))
            raise

    def embed_text(self, text: str) -> list[float]:
        return self.embed_texts([text])[0]

    def embed_images(self, images: list[bytes]) -> list[list[float]]:
        if not images:
            return []
        inputs = [{"image": base64.b64encode(img).decode()} for img in images]
        try:
            return self._post(inputs)
        except Exception as exc:
            logger.error("embed_images_failed", error=str(exc), count=len(images))
            raise

    async def aembed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            return await self._apost(texts)
        except Exception as exc:
            logger.error("embed_texts_failed", error=str(exc), count=len(texts))
            raise

    async def aembed_text(self, text: str) -> list[float]:
        result = await self.aembed_texts([text])
        return result[0]

    async def aembed_images(self, images: list[bytes]) -> list[list[float]]:
        if not images:
            return []
        inputs = [{"image": base64.b64encode(img).decode()} for img in images]
        try:
            return await self._apost(inputs)
        except Exception as exc:
            logger.error("embed_images_failed", error=str(exc), count=len(images))
            raise


# 向后兼容别名
JinaEmbedding = EmbeddingClient
