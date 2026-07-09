"""
LLM 工厂 — 统一的模型创建入口。

支持多模型配置（chat/routing/fallback），streaming默认开启。
三级降级链：在线 API → 本地模型 API → 友好错误。
"""

from __future__ import annotations

from functools import lru_cache

import structlog
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from pydantic_settings import BaseSettings

logger = structlog.get_logger()


class LLMSettings(BaseSettings):
    """LLM相关配置，从环境变量 LEA_ 前缀读取。"""

    tenant_api_base: str = "https://tenantapi-ai.ouchn.edu.cn"
    tenant_api_key: str = ""

    model_chat: str = "qwen3-max"
    model_routing: str = "qwen3-max"
    model_fallback: str = "qwen3-max"

    # 本地模型（自部署 Ollama/vLLM，降级用）
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
    "fallback": "model_fallback",
}


def _create_model(
    model_name: str,
    api_base: str,
    api_key: str,
    *,
    streaming: bool = True,
    temperature: float = 0.7,
    max_tokens: int | None = None,
    timeout: float = 120.0,
) -> ChatOpenAI:
    from lea_graph.infra.token_tracking import token_callback

    return ChatOpenAI(
        model=model_name,
        openai_api_key=api_key,
        openai_api_base=api_base,
        streaming=streaming,
        stream_usage=True,
        temperature=temperature,
        max_tokens=max_tokens,
        request_timeout=timeout,
        callbacks=[token_callback],
    )


def get_chat_model(
    purpose: str = "chat",
    *,
    streaming: bool = True,
    temperature: float = 0.7,
    max_tokens: int | None = None,
    timeout: float = 120.0,
    with_fallback: bool = True,
) -> BaseChatModel:
    """创建 ChatOpenAI 实例，自动挂载降级链。

    降级链（with_fallback=True 时）：
      在线 API (tenant_api_base) → 本地模型 API (local_api_base)

    with_fallback=False 可跳过降级链（用于测试或已有外层 fallback 逻辑时）。
    """
    settings = _get_llm_settings()
    attr = _MODEL_MAP.get(purpose, "model_chat")
    model_name = getattr(settings, attr)

    api_base = f"{settings.tenant_api_base}/v1"
    api_key = settings.tenant_api_key or "sk-placeholder"

    primary = _create_model(
        model_name, api_base, api_key,
        streaming=streaming, temperature=temperature,
        max_tokens=max_tokens, timeout=timeout,
    )

    if not with_fallback or not settings.local_api_base:
        return primary

    local = _create_model(
        settings.local_model,
        f"{settings.local_api_base}/v1",
        settings.local_api_key,
        streaming=streaming, temperature=temperature,
        max_tokens=max_tokens, timeout=timeout,
    )

    logger.debug(
        "llm_fallback_chain",
        primary=model_name,
        fallback=settings.local_model,
    )
    return primary.with_fallbacks([local])
