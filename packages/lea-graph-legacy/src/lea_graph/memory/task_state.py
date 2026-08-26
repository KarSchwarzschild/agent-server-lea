"""任务中间结果存储 — Redis-based，多 Agent 共享。"""

from __future__ import annotations

import json

import structlog

logger = structlog.get_logger()


class TaskStateStore:
    """Redis-based task intermediate state, shared across agents.

    Key 格式: memory:task:{task_id}:{agent_id}
    """

    def __init__(self, redis_client):
        self._redis = redis_client

    async def save(self, task_id: str, agent_id: str, data: dict, ttl: int = 3600) -> None:
        key = f"memory:task:{task_id}:{agent_id}"
        await self._redis.set(key, json.dumps(data, ensure_ascii=False), ex=ttl)
        logger.debug("task_state_saved", task_id=task_id, agent_id=agent_id)

    async def load(self, task_id: str, agent_id: str) -> dict | None:
        key = f"memory:task:{task_id}:{agent_id}"
        raw = await self._redis.get(key)
        if raw:
            return json.loads(raw)
        return None

    async def load_all(self, task_id: str) -> list[dict]:
        pattern = f"memory:task:{task_id}:*"
        results = []
        async for key in self._redis.scan_iter(pattern):
            raw = await self._redis.get(key)
            if raw:
                data = json.loads(raw)
                agent_id = key.split(":")[-1] if isinstance(key, str) else key.decode().split(":")[-1]
                data["_agent_id"] = agent_id
                results.append(data)
        return results

    async def clear(self, task_id: str) -> int:
        pattern = f"memory:task:{task_id}:*"
        count = 0
        async for key in self._redis.scan_iter(pattern):
            await self._redis.delete(key)
            count += 1
        return count
