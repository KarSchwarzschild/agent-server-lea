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
from langgraph.checkpoint.memory import MemorySaver

logger = structlog.get_logger()


def create_memory_checkpointer() -> MemorySaver:
    """创建内存 Checkpointer（开发/测试用）。"""
    return MemorySaver()


@asynccontextmanager
async def create_redis_checkpointer(redis_url: str) -> AsyncIterator:
    """创建 Redis Checkpointer（生产用）。

    需要安装 langgraph-checkpoint-redis 包。
    如果 Redis 不可用，fallback 到内存 Checkpointer。
    """
    try:
        from langgraph.checkpoint.redis.aio import AsyncRedisSaver

        async with AsyncRedisSaver.from_conn_string(redis_url) as saver:
            logger.info("redis_checkpointer_ready", url=redis_url.split("@")[-1])
            yield saver
    except ImportError:
        logger.warning("redis_checkpointer_unavailable", reason="langgraph-checkpoint-redis not installed")
        yield create_memory_checkpointer()
    except Exception as exc:
        logger.warning("redis_checkpointer_fallback", error=str(exc))
        yield create_memory_checkpointer()
