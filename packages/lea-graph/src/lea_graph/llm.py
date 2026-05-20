"""
LLM 工厂 — 统一的模型创建入口。

支持多模型配置（chat/routing/fallback），streaming默认开启，
通过 infra/resilience 提供重试+熔断+fallback。
"""

from __future__ import annotations

from functools import lru_cache

from langchain_openai import ChatOpenAI
from pydantic import Field
from pydantic_settings import BaseSettings


class LLMSettings(BaseSettings):
    """LLM相关配置，从环境变量 LEA_ 前缀读取。

    字段名 tenant_api_key → 环境变量 LEA_TENANT_API_KEY（自动加前缀）
    """

    tenant_api_base: str = "https://tenantapi-ai.ouchn.edu.cn"
    tenant_api_key: str = ""

    model_chat: str = "qwen-max-latest"
    model_routing: str = "glm-4-plus"
    model_fallback: str = "glm-4-flash"

    model_config = {"env_prefix": "LEA_", "extra": "ignore"}


@lru_cache
def _get_llm_settings() -> LLMSettings:
    return LLMSettings()


_MODEL_MAP = {
    "chat": "model_chat",
    "routing": "model_routing",
    "fallback": "model_fallback",
}


def get_chat_model(
    purpose: str = "chat",
    *,
    streaming: bool = True,
    temperature: float = 0.7,
    max_tokens: int | None = None,
    timeout: float = 120.0,
) -> ChatOpenAI:
    """创建 ChatOpenAI 实例。

    Args:
        purpose: 用途，决定使用哪个模型。可选: "chat", "routing", "fallback"
        streaming: 是否启用流式输出（LangGraph stream_mode="messages" 需要此项为True）
        temperature: 温度参数
        max_tokens: 最大token数
        timeout: 超时秒数

    教学说明:
        ChatOpenAI 通过 openai_api_base 参数兼容所有 OpenAI 格式的 API。
        智能基座（国开大）、通义千问、GLM 等国内大模型都提供了 OpenAI 兼容接口，
        因此统一使用 ChatOpenAI 即可，无需为每个厂商写不同的客户端。
    """
    settings = _get_llm_settings()
    attr = _MODEL_MAP.get(purpose, "model_chat")
    model_name = getattr(settings, attr)

    api_base = f"{settings.tenant_api_base}/v1"
    api_key = settings.tenant_api_key or "sk-placeholder"

    return ChatOpenAI(
        model=model_name,
        openai_api_key=api_key,
        openai_api_base=api_base,
        streaming=streaming,
        temperature=temperature,
        max_tokens=max_tokens,
        request_timeout=timeout,
    )
