"""App 注册表 — 发现、加载、注册所有App。

启动时扫描 lea_apps 包，加载所有实现了 AppProtocol 的 App，
收集其 Agent/Skill/API路由/数据库模型。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog
from fastapi import APIRouter

from lea_graph.llm import get_chat_model
from lea_graph.protocol import AppManifest, AppProtocol
from lea_graph.services.memory_store import MemoryStore
from lea_graph.tools import PLATFORM_TOOLS

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph

logger = structlog.get_logger()


class AppRegistry:
    """平台App注册表。"""

    def __init__(self, services: dict[str, Any] | None = None):
        self.apps: dict[str, AppProtocol] = {}
        self.manifests: dict[str, AppManifest] = {}
        self.app_agents: dict[str, dict[str, CompiledStateGraph]] = {}
        self.api_routers: list[tuple[str, APIRouter]] = []
        self.all_db_models: list[type] = []
        self._services = services or {}

    def discover_and_load(self) -> list[AppProtocol]:
        """扫描 lea_apps，加载所有App。"""
        from lea_apps import discover_apps

        apps = discover_apps()

        for app in apps:
            name = app.manifest.name
            try:
                self.apps[name] = app
                self.manifests[name] = app.manifest

                models = app.get_models()
                self.all_db_models.extend(models)

                agents = app.build_agents(
                    llm=get_chat_model,
                    platform_tools=PLATFORM_TOOLS,
                    services=self._services,
                )
                self.app_agents[name] = agents

                if app.router:
                    self.api_routers.append((name, app.router))

                agent_count = len(app.manifest.agents)
                skill_count = len(app.manifest.skills)
                route_count = len(app.manifest.routes)
                logger.info(
                    "app_loaded",
                    app=name,
                    display_name=app.manifest.display_name,
                    agents=agent_count,
                    skills=skill_count,
                    routes=route_count,
                )
            except Exception as exc:
                logger.error("app_load_failed", app=name, error=str(exc))

        logger.info("registry_complete", total_apps=len(self.apps))
        return apps

    def get_app_skills_map(self) -> dict[str, dict[str, list[str]]]:
        """构建路由用的 {app: {agent: [keywords]}} 映射。"""
        result: dict[str, dict[str, list[str]]] = {}
        for name, manifest in self.manifests.items():
            agents_map: dict[str, list[str]] = {}
            all_keywords = []
            for skill_info in manifest.skills:
                all_keywords.extend(skill_info.trigger_keywords)
            for agent_info in manifest.agents:
                agents_map[agent_info.name] = all_keywords
            result[name] = agents_map
        return result
