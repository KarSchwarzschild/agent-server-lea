"""配额检查 + 限流 + 用量记录。"""

from __future__ import annotations

import redis
import structlog

from lea_parser.config import get_redis_settings

logger = structlog.get_logger()

_redis_client: redis.Redis | None = None
_redis_available = True


def _get_redis() -> redis.Redis | None:
    global _redis_client, _redis_available
    if not _redis_available:
        return None
    if _redis_client is None:
        settings = get_redis_settings()
        try:
            _redis_client = redis.from_url(settings.url, decode_responses=True)
            _redis_client.ping()
        except Exception as e:
            logger.warning("redis_unavailable", error=str(e))
            _redis_available = False
            return None
    return _redis_client
