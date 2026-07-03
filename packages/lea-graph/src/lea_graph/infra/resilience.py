"""
LLM 可靠性层 — 重试、熔断、Fallback 模型链。

- 重试: tenacity 指数退避 + jitter
- 熔断: Redis-backed 分布式熔断器（CLOSED → OPEN → HALF_OPEN 状态机）
- Fallback: 按优先级依次尝试多个模型
"""

from __future__ import annotations

import time
from typing import Any

import httpx
import structlog
from langchain_core.messages import BaseMessage
from tenacity import (
    RetryCallState,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    wait_random,
)

logger = structlog.get_logger()


# ── Exceptions ───────────────────────────────────────────


class CircuitOpenError(Exception):
    def __init__(self, name: str):
        self.name = name
        super().__init__(f"Circuit breaker '{name}' is open")


class AllModelsFailedError(Exception):
    def __init__(self, errors: list[tuple[str, Exception]]):
        self.errors = errors
        models = [f"{name}: {type(e).__name__}" for name, e in errors]
        super().__init__(f"All models failed: {', '.join(models)}")


# ── Retry ────────────────────────────────────────────────


RETRYABLE_EXCEPTIONS = (
    httpx.TimeoutException,
    httpx.ConnectError,
    ConnectionError,
    TimeoutError,
)


def _log_retry(retry_state: RetryCallState) -> None:
    exc = retry_state.outcome.exception() if retry_state.outcome else None
    logger.warning(
        "llm_retry",
        attempt=retry_state.attempt_number,
        error=str(exc) if exc else None,
    )


llm_retry = retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=30) + wait_random(0, 2),
    retry=retry_if_exception_type(RETRYABLE_EXCEPTIONS),
    before_sleep=_log_retry,
    reraise=True,
)


# ── Redis Circuit Breaker (fully async) ─────────────────


class RedisCircuitBreaker:
    """分布式熔断器 — 全异步 Redis 原子操作实现。

    状态机:
      CLOSED    ──(连续失败 >= fail_max)──→ OPEN
      OPEN      ──(超过 reset_timeout)───→ HALF_OPEN
      HALF_OPEN ──(探测成功)─────────────→ CLOSED
      HALF_OPEN ──(探测失败)─────────────→ OPEN

    多实例安全:
      - INCR 原子计数失败次数
      - SETNX 保证 HALF_OPEN 只有一个实例发探测请求
      - Pipeline 批量操作保证状态一致性
    """

    def __init__(
        self,
        name: str,
        redis_client,
        fail_max: int = 5,
        reset_timeout: int = 60,
    ):
        self._name = name
        self._redis = redis_client
        self._fail_max = fail_max
        self._reset_timeout = reset_timeout

    def _key(self, suffix: str) -> str:
        return f"lea:breaker:{self._name}:{suffix}"

    async def get_state(self) -> str:
        raw = await self._redis.get(self._key("state"))
        if raw is None:
            return "closed"
        s = raw.decode() if isinstance(raw, bytes) else raw
        if s == "open":
            opened_at = await self._redis.get(self._key("opened_at"))
            if opened_at and time.time() - float(opened_at) > self._reset_timeout:
                return "half_open"
        return s

    async def record_success(self) -> None:
        pipe = self._redis.pipeline()
        pipe.set(self._key("state"), "closed")
        pipe.set(self._key("fail_count"), 0)
        pipe.delete(self._key("opened_at"))
        pipe.delete(self._key("probe_lock"))
        await pipe.execute()

    async def record_failure(self) -> None:
        count = await self._redis.incr(self._key("fail_count"))
        if count >= self._fail_max:
            pipe = self._redis.pipeline()
            pipe.set(self._key("state"), "open")
            pipe.set(self._key("opened_at"), str(time.time()))
            pipe.set(self._key("fail_count"), 0)
            await pipe.execute()

    async def call_async(self, func, *args, **kwargs):
        current = await self.get_state()

        if current == "open":
            raise CircuitOpenError(self._name)

        if current == "half_open":
            got_probe = await self._redis.set(
                self._key("probe_lock"),
                "1",
                nx=True,
                ex=self._reset_timeout,
            )
            if not got_probe:
                raise CircuitOpenError(self._name)

        try:
            result = await func(*args, **kwargs)
            await self.record_success()
            return result
        except Exception:
            await self.record_failure()
            raise


# ── Breaker Registry ─────────────────────────────────────


_breakers: dict[str, RedisCircuitBreaker] = {}
_redis_client = None


def init_breaker_registry(redis_client) -> None:
    global _redis_client
    _redis_client = redis_client


def get_breaker(name: str, fail_max: int = 5, reset_timeout: int = 60) -> RedisCircuitBreaker:
    if name not in _breakers:
        if _redis_client is None:
            raise RuntimeError("Call init_breaker_registry(redis_client) first")
        _breakers[name] = RedisCircuitBreaker(
            name=name,
            redis_client=_redis_client,
            fail_max=fail_max,
            reset_timeout=reset_timeout,
        )
    return _breakers[name]


# ── High-level API ───────────────────────────────────────


async def invoke_with_retry(model, messages: list[BaseMessage], **kwargs) -> Any:
    breaker = get_breaker(getattr(model, "model_name", "default"))

    @llm_retry
    async def _call():
        return await breaker.call_async(model.ainvoke, messages, **kwargs)

    return await _call()


async def invoke_with_fallback(
    messages: list[BaseMessage],
    models: list,
    **kwargs,
) -> Any:
    errors: list[tuple[str, Exception]] = []

    for model in models:
        model_name = getattr(model, "model_name", str(model))
        breaker = get_breaker(model_name)

        current_state = await breaker.get_state()
        if current_state == "open":
            logger.info("model_circuit_open", model=model_name)
            errors.append((model_name, CircuitOpenError(model_name)))
            continue

        try:

            @llm_retry
            async def _call(m=model):
                return await breaker.call_async(m.ainvoke, messages, **kwargs)

            result = await _call()
            if errors:
                logger.info("fallback_succeeded", model=model_name, prior_failures=len(errors))
            return result

        except CircuitOpenError as e:
            logger.warning("model_circuit_broken", model=model_name)
            errors.append((model_name, e))
        except Exception as e:
            logger.warning("model_failed", model=model_name, error=str(e))
            errors.append((model_name, e))

    raise AllModelsFailedError(errors)


async def call_with_fallback(primary_model, fallback_model, messages, **kwargs):
    return await invoke_with_fallback(messages, [primary_model, fallback_model], **kwargs)


async def get_breaker_status() -> dict[str, dict]:
    result = {}
    for name, breaker in _breakers.items():
        fail_count_raw = await breaker._redis.get(breaker._key("fail_count"))
        result[name] = {
            "state": await breaker.get_state(),
            "fail_count": int(fail_count_raw or 0),
            "fail_max": breaker._fail_max,
            "reset_timeout": breaker._reset_timeout,
        }
    return result
