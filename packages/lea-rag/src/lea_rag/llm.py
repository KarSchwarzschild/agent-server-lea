"""LLM 工厂（精简版）— 仅供检索服务内部使用（query_optimizer, extractor）。"""

from __future__ import annotations

from functools import lru_cache

import structlog
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from pydantic_settings import BaseSettings

logger = structlog.get_logger()


class LLMSettings(BaseSettings):
    tenant_api_base: str = "https://tenantapi-ai.ouchn.edu.cn"
    tenant_api_key: str = ""
    model_chat: str = "qwen3-max"
    model_routing: str = "qwen3-max"
    local_api_base: str = ""
    local_api_key: str = "sk-local"
    local_model: str = "qwen3:32b"

    model_config = {"env_prefix": "LEA_", "env_file": ".env", "extra": "ignore"}


@lru_cache
def _get_llm_settings() -> LLMSettings:
    return LLMSettings()


_MODEL_MAP = {
    "chat": "model_chat",
    "routing": "model_routing",
}


def get_chat_model(
    purpose: str = "chat",
    *,
    streaming: bool = True,
    temperature: float = 0.7,
    max_tokens: int | None = None,
    timeout: float = 120.0,
) -> BaseChatModel:
    settings = _get_llm_settings()
    attr = _MODEL_MAP.get(purpose, "model_chat")
    model_name = getattr(settings, attr)

    api_base = f"{settings.tenant_api_base}/v1"
    api_key = settings.tenant_api_key or "sk-placeholder"

    primary = ChatOpenAI(
        model=model_name,
        openai_api_key=api_key,
        openai_api_base=api_base,
        streaming=streaming,
        stream_usage=True,
        temperature=temperature,
        max_tokens=max_tokens,
        request_timeout=timeout,
    )

    if not settings.local_api_base:
        return primary

    local = ChatOpenAI(
        model=settings.local_model,
        openai_api_key=settings.local_api_key,
        openai_api_base=f"{settings.local_api_base}/v1",
        streaming=streaming,
        stream_usage=True,
        temperature=temperature,
        max_tokens=max_tokens,
        request_timeout=timeout,
    )
    return primary.with_fallbacks([local])
