"""
SkillRegistry — 技能注册表（运行时查询/热加载）。

集中管理所有技能定义和工具实例。支持：
- 按 name/category/keyword 查询
- 热加载（重新扫描目录）
- Redis Pub/Sub 广播：一个实例 reload 后通知所有实例同步
- 与 AppProtocol 的 SkillInfo 互操作
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable

import structlog
from langchain_core.tools import BaseTool

from lea_graph.protocol import SkillInfo
from lea_graph.skills.loader import SkillLoader
from lea_graph.skills.schema import SkillDefinition

logger = structlog.get_logger()

_RELOAD_CHANNEL = "lea:skill_reload"
_DEFAULT_CACHE_TTL = 60.0


class SkillRegistry:
    """技能注册表 — 全局单例，管理所有技能。

    分布式支持：
    - Redis Pub/Sub：CUD 操作后广播，所有实例即时刷新
    - TTL 缓存自愈：即使 Pub/Sub 消息丢失，缓存过期后自动从 DB 刷新
    """

    def __init__(self, redis_client=None, cache_ttl: float = _DEFAULT_CACHE_TTL):
        self._definitions: dict[str, SkillDefinition] = {}
        self._tools: dict[str, list[BaseTool]] = {}
        self._loader: SkillLoader | None = None
        self._redis = redis_client
        self._cache_ttl = cache_ttl
        self._cache_loaded_at: float = 0.0
        self._db_loader: Callable[[], list[SkillDefinition]] | None = None

    def register(self, definition: SkillDefinition, tools: list[BaseTool] | None = None) -> None:
        self._definitions[definition.name] = definition
        if tools:
            self._tools[definition.name] = tools
        logger.info("skill_registered", name=definition.name, tool_count=len(tools or []))

    def register_from_loader(self, loader: SkillLoader) -> int:
        """从 SkillLoader 批量注册。返回注册数量。"""
        self._loader = loader
        definitions = loader.load_all()
        for name, defn in definitions.items():
            self.register(defn)
        return len(definitions)

    def register_from_db(self, definitions: list[SkillDefinition]) -> int:
        """从数据库加载的定义批量注册。"""
        count = 0
        for defn in definitions:
            self._definitions[defn.name] = defn
            count += 1
        self._cache_loaded_at = time.monotonic()
        logger.info("skills_registered_from_db", count=count)
        return count

    def set_db_loader(self, loader: Callable[[], list[SkillDefinition]]) -> None:
        """设置 DB 加载回调（TTL 过期时自动调用刷新缓存）。"""
        self._db_loader = loader

    def _check_ttl(self) -> None:
        """如果缓存过期且有 db_loader，自动刷新。"""
        if self._db_loader is None or self._cache_ttl <= 0:
            return
        if time.monotonic() - self._cache_loaded_at > self._cache_ttl:
            try:
                definitions = self._db_loader()
                self.register_from_db(definitions)
                logger.debug("skill_cache_ttl_refresh", count=len(definitions))
            except Exception as e:
                logger.warning("skill_cache_ttl_refresh_failed", error=str(e))

    def get(self, name: str) -> SkillDefinition | None:
        self._check_ttl()
        return self._definitions.get(name)

    def get_tools(self, name: str) -> list[BaseTool]:
        return self._tools.get(name, [])

    def list_all(self) -> list[SkillDefinition]:
        self._check_ttl()
        return [d for d in self._definitions.values() if d.enabled]

    def find_by_category(self, category: str) -> list[SkillDefinition]:
        self._check_ttl()
        return [d for d in self._definitions.values() if d.enabled and d.category == category]

    def find_by_keyword(self, keyword: str) -> list[SkillDefinition]:
        """根据关键词匹配技能。"""
        self._check_ttl()
        results = []
        for defn in self._definitions.values():
            if not defn.enabled:
                continue
            if any(kw in keyword for kw in defn.trigger_keywords):
                results.append(defn)
        return results

    def to_skill_infos(self) -> list[SkillInfo]:
        """转换为 AppProtocol 兼容的 SkillInfo 列表。"""
        return [
            SkillInfo(
                name=d.name,
                display_name=d.display_name,
                description=d.description,
                trigger_keywords=d.trigger_keywords,
                category=d.category,
            )
            for d in self.list_all()
        ]

    def reload(self, *, broadcast: bool = True) -> int:
        """热加载 — 重新扫描目录，更新注册表。

        broadcast=True 时通过 Redis Pub/Sub 通知其他实例同步 reload。
        """
        if self._loader is None:
            return 0
        self._definitions.clear()
        self._tools.clear()
        count = self.register_from_loader(self._loader)

        if broadcast and self._redis:
            self._redis.publish(_RELOAD_CHANNEL, "reload")
            logger.info("skill_reload_broadcast_sent")

        return count

    async def listen_for_reload(self) -> None:
        """后台任务 — 订阅 Redis Pub/Sub，收到消息时自动 reload。

        启动方式（FastAPI lifespan 里）:
            asyncio.create_task(registry.listen_for_reload())
        """
        if not self._redis:
            return
        pubsub = self._redis.pubsub()
        pubsub.subscribe(_RELOAD_CHANNEL)
        logger.info("skill_reload_listener_started")

        while True:
            msg = pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if msg and msg["type"] == "message":
                logger.info("skill_reload_received")
                self.reload(broadcast=False)
            await asyncio.sleep(1)

    @property
    def skill_count(self) -> int:
        return len(self._definitions)
