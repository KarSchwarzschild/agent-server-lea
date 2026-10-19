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

    @staticmethod
    def _degenerate_reason(inputs: list[Any], vectors: list[list[float]]) -> str | None:
        """检查"上游回了 200、但向量是废的"这种假成功。返回原因，正常时返回 None。

        为什么需要这个：上游中转 API 不可用或 key 过期时，**不一定**会回非 2xx。
        回 200 带一组空向量或全零向量，raise_for_status() 拿不到异常，
        _parse_response 也拿不到异常 —— 于是 record_embedding("success") 照记，
        检索照常返回 200，只是结果是按零向量算出来的垃圾。这比"搜不到"更糟：
        搜不到至少是沉默，这个是**自信地给错答案**。

        只查三件和模型无关、怎么看都是错的事：

          条数不符    发了 N 条文本回来 M 条，对不上就没法 zip，后面全是错位
          全零向量    embedding 不可能是零向量（余弦相似度对它无定义）
          空向量      输入非空却回了零条

        **刻意不查维度**：embedding_dim 是每个知识库自己的（db/models.py 默认
        1024），而 EmbeddingSettings.dimension 是 1536，不同模型本来就不一样。
        拿全局常量去判维度只会造出假警报。维度不对的后果 Milvus 自己会报
        （"length of float data should divide the dim"），不需要在这里猜。
        """
        if not vectors:
            return "empty_response" if inputs else None
        if len(vectors) != len(inputs):
            return f"count_mismatch(sent={len(inputs)},got={len(vectors)})"
        for i, v in enumerate(vectors):
            if not v:
                return f"empty_vector(index={i})"
            # any() 短路，正常向量第一个非零元素就返回，代价可忽略。
            if not any(v):
                return f"all_zero_vector(index={i},dim={len(v)})"
        return None

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
        # degenerate 和 success 互斥，一次调用只记一笔 —— 否则总量会被记两次，
        # 分母虚高，比率式告警就被稀释了（search_kb 的 partial/success 踩过）。
        reason = self._degenerate_reason(inputs, out)
        if reason:
            logger.error("embedding_degenerate", reason=reason, model=self.model,
                         url=self._url, count=len(inputs))
        record_embedding("degenerate" if reason else "success",
                         time.perf_counter() - started)
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
        reason = self._degenerate_reason(inputs, out)
        if reason:
            logger.error("embedding_degenerate", reason=reason, model=self.model,
                         url=self._url, count=len(inputs))
        record_embedding("degenerate" if reason else "success",
                         time.perf_counter() - started)
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
