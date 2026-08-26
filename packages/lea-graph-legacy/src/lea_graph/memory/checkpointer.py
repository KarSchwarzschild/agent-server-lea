"""记忆/状态持久化 — Redis Checkpointer。

LangGraph 通过 Checkpointer 自动管理对话状态的持久化：
- 每次 graph.ainvoke/astream 时自动保存状态到 Redis
- 下次同一 thread_id 的请求自动恢复上下文
- thread_id 格式: {user_id}:{session_id}
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator

import structlog

logger = structlog.get_logger()


@asynccontextmanager
async def create_redis_checkpointer(redis_url: str) -> AsyncIterator:
    """创建 Redis Checkpointer — Redis 必须可用，不降级。"""
    from langgraph.checkpoint.redis.aio import AsyncRedisSaver

    async with AsyncRedisSaver.from_conn_string(redis_url) as saver:
        logger.info("redis_checkpointer_ready", url=redis_url.split("@")[-1])
        yield saver
