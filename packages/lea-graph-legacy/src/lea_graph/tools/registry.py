"""工具注册表 — 自动扫描 tools/ 目录收集 BaseTool 实例。"""

from __future__ import annotations

import importlib
import inspect
import pkgutil

import structlog
from langchain_core.tools import BaseTool

logger = structlog.get_logger()


class ToolRegistry:
    """平台工具注册表。自动扫描模块收集 BaseTool，也支持手动注册。"""

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        if tool.name in self._tools:
            return
        self._tools[tool.name] = tool
        logger.info("tool_registered", name=tool.name)

    def get_all(self) -> list[BaseTool]:
        return list(self._tools.values())

    def get(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    @property
    def names(self) -> list[str]:
        return list(self._tools.keys())


_registry = ToolRegistry()


def get_tool_registry() -> ToolRegistry:
    return _registry


def discover_tools(package_path: list[str], package_name: str) -> None:
    """扫描包目录，import 所有子模块，收集 BaseTool 实例到 registry。"""
    for info in pkgutil.iter_modules(package_path):
        if info.name == "registry":
            continue
        try:
            module = importlib.import_module(f"{package_name}.{info.name}")
        except Exception:
            logger.warning("tool_module_import_failed", module=info.name)
            continue
        for _, obj in inspect.getmembers(module):
            if isinstance(obj, BaseTool) and obj.name not in _registry._tools:
                _registry.register(obj)
