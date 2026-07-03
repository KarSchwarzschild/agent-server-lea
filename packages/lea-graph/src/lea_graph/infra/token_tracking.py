"""Token 用量追踪 — @track_tokens 装饰器。

用法：在任何包含 LLM 调用的 async 函数上加 @track_tokens，
函数内所有 ainvoke() 的 token 消耗会自动累加并写入 Redis。

    @track_tokens
    async def my_node(state: dict) -> dict:
        llm = get_chat_model("chat")
        resp = await llm.ainvoke(messages)   # 自动追踪
        return {"result": resp.content}

原理：ContextVar 累加器 + LangChain AsyncCallbackHandler。
回调挂在 get_chat_model() 返回的模型上，无活跃上下文时为 no-op。
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass, field
from functools import wraps
from typing import Any

import structlog
from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.outputs import LLMResult

logger = structlog.get_logger()


@dataclass
class TokenAccumulator:
    tenant_id: str = ""
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    calls: int = 0


_token_acc: contextvars.ContextVar[TokenAccumulator | None] = contextvars.ContextVar(
    "_token_acc", default=None
)


class _TokenCallback(AsyncCallbackHandler):
    """LangChain 回调 — on_llm_end 时从 LLMResult 提取 token_usage 写入 ContextVar。

    全局单例，无活跃 ContextVar 时静默跳过，零开销。
    """

    async def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        acc = _token_acc.get()
        if acc is None:
            return

        prompt_tokens = 0
        completion_tokens = 0
        total_tokens = 0

        # 路径 1: 非流式 — llm_output.token_usage
        usage = (response.llm_output or {}).get("token_usage", {})
        if usage:
            prompt_tokens = usage.get("prompt_tokens", 0)
            completion_tokens = usage.get("completion_tokens", 0)
            total_tokens = usage.get("total_tokens", 0)

        # 路径 2: 流式 — generations[].message.usage_metadata
        if total_tokens == 0 and response.generations:
            for gen_list in response.generations:
                for gen in gen_list:
                    msg = getattr(gen, "message", None)
                    um = getattr(msg, "usage_metadata", None) if msg else None
                    if um:
                        prompt_tokens += um.get("input_tokens", 0)
                        completion_tokens += um.get("output_tokens", 0)
                        total_tokens += um.get("total_tokens", 0)

        acc.input_tokens += prompt_tokens
        acc.output_tokens += completion_tokens
        acc.total_tokens += total_tokens
        acc.calls += 1
        if not acc.model:
            acc.model = (response.llm_output or {}).get("model_name", "")


token_callback = _TokenCallback()


def track_tokens(fn):
    """装饰器：追踪被装饰函数内所有 LLM 调用的 token 用量。

    自动从第一个参数（state dict）提取 tenant_id。
    函数结束后将累计 token 写入 Redis（via consume_tokens）和 OTel 指标。
    """

    @wraps(fn)
    async def wrapper(*args, **kwargs):
        tenant_id = ""
        if args and isinstance(args[0], dict):
            tenant_id = args[0].get("tenant_id", "")

        acc = TokenAccumulator(tenant_id=tenant_id)
        tok = _token_acc.set(acc)
        try:
            result = await fn(*args, **kwargs)
            if acc.total_tokens > 0:
                await _flush(acc, fn.__name__)
            return result
        finally:
            _token_acc.reset(tok)

    return wrapper


async def _flush(acc: TokenAccumulator, source: str = "") -> None:
    if not acc.tenant_id or acc.total_tokens == 0:
        return

    from lea_graph.infra.rate_limiter import get_rate_limiter

    limiter = get_rate_limiter()
    if limiter:
        try:
            await limiter.consume_tokens(acc.tenant_id, acc.total_tokens)
        except Exception as e:
            logger.warning("token_flush_redis_error", error=str(e))

    try:
        from lea_graph.infra.tracing import record_tokens

        record_tokens(acc.model, acc.input_tokens, acc.output_tokens, acc.tenant_id)
    except Exception:
        pass

    logger.info(
        "tokens_tracked",
        source=source,
        tenant=acc.tenant_id,
        model=acc.model,
        input=acc.input_tokens,
        output=acc.output_tokens,
        total=acc.total_tokens,
        calls=acc.calls,
    )
