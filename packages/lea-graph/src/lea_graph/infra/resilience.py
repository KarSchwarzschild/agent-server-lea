"""
LLM 可靠性层 — 重试、熔断、Fallback。

- 重试: tenacity 指数退避 + jitter，应对临时性网络/限流错误
- 熔断: pybreaker CircuitBreaker，连续失败后暂停请求避免雪崩
- Fallback: 主模型不可用时自动切换到备选模型
"""

from __future__ import annotations

import httpx
import structlog
from pybreaker import CircuitBreaker, CircuitBreakerError
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    wait_random,
)

logger = structlog.get_logger()

# ========== 重试策略 ==========
llm_retry = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=30) + wait_random(0, 2),
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.HTTPStatusError)),
    before_sleep=lambda retry_state: logger.warning(
        "llm_retry",
        attempt=retry_state.attempt_number,
        error=str(retry_state.outcome.exception()) if retry_state.outcome else None,
    ),
)

# ========== 熔断器 ==========
llm_circuit_breaker = CircuitBreaker(
    fail_max=5,
    reset_timeout=60,
    name="llm_breaker",
)


async def call_with_fallback(primary_model, fallback_model, messages, **kwargs):
    """先尝试主模型，熔断后自动切换到 fallback 模型。

    Args:
        primary_model: 主 ChatOpenAI 实例
        fallback_model: 备选 ChatOpenAI 实例
        messages: 消息列表
        **kwargs: 传递给 ainvoke 的额外参数
    """
    try:

        @llm_retry
        async def _call():
            return await llm_circuit_breaker.call_async(primary_model.ainvoke, messages, **kwargs)

        return await _call()
    except (CircuitBreakerError, Exception) as exc:
        logger.warning("llm_fallback", primary_error=str(exc), fallback_model=fallback_model.model_name)
        return await fallback_model.ainvoke(messages, **kwargs)
