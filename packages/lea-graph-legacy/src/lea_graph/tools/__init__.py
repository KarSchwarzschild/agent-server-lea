"""平台工具注册表 — 自动扫描 tools/ 目录。"""

from lea_graph.tools.registry import (
    ToolRegistry,
    discover_tools,
    get_tool_registry,
)

# 自动扫描当前包，收集所有 @tool 装饰的 BaseTool 实例
discover_tools(__path__, __name__)


def get_platform_tools():
    return get_tool_registry().get_all()


# 向后兼容
PLATFORM_TOOLS = get_platform_tools()

__all__ = ["get_tool_registry", "get_platform_tools", "PLATFORM_TOOLS"]
