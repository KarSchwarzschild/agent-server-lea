"""跨App记忆存储 — 基于Redis，支持按user_id + key存取。"""

from __future__ import annotations

import json
from typing import Any

import structlog

logger = structlog.get_logger()


class MemoryStore:
    """跨App记忆存储。

    不同App保存的用户偏好、学习进度等记忆可以被其他App读取，
    实现"资源互通"。
    """

    def __init__(self, redis_client=None):
        self._redis = redis_client

    def _key(self, user_id: str, key: str, app_name: str | None = None) -> str:
        prefix = f"lea:memory:{user_id}"
        if app_name:
            return f"{prefix}:{app_name}:{key}"
        return f"{prefix}:global:{key}"

    async def save(
        self,
        user_id: str,
        key: str,
        value: Any,
        app_name: str | None = None,
        ttl: int | None = None,
    ) -> None:
        """保存记忆。app_name=None 表示全局记忆。"""
        if self._redis is None:
            logger.warning("memory_store_no_redis", action="save")
            return

        redis_key = self._key(user_id, key, app_name)
        data = json.dumps(value, ensure_ascii=False)

        if ttl:
            await self._redis.setex(redis_key, ttl, data)
        else:
            await self._redis.set(redis_key, data)

    async def get(self, user_id: str, key: str, app_name: str | None = None) -> Any | None:
        """读取记忆。"""
        if self._redis is None:
            return None

        redis_key = self._key(user_id, key, app_name)
        data = await self._redis.get(redis_key)
        if data:
            return json.loads(data)
        return None

    async def delete(self, user_id: str, key: str, app_name: str | None = None) -> None:
        """删除记忆。"""
        if self._redis is None:
            return

        redis_key = self._key(user_id, key, app_name)
        await self._redis.delete(redis_key)
