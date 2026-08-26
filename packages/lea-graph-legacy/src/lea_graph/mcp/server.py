"""
MCP Server 实现。

使用 mcp Python SDK，支持 stdio 和 SSE transport。
自动注册平台所有 @tool 为 MCP Tool。

使用方式：
    # stdio（Claude Desktop 配置）
    python -m lea_graph.mcp

    # SSE（HTTP 访问）
    python -m lea_graph.mcp --transport sse --port 8888
"""

from __future__ import annotations

import structlog
from mcp.server import Server
from mcp.types import Tool

from lea_graph.mcp.tools import invoke_langchain_tool, langchain_tool_to_mcp_schema
from lea_graph.tools import PLATFORM_TOOLS

logger = structlog.get_logger()


def create_mcp_server(name: str = "lea-platform") -> Server:
    """创建 MCP Server 实例，注册平台所有工具。"""
    server = Server(name)

    tool_map = {tool.name: tool for tool in PLATFORM_TOOLS}
    tool_schemas = [langchain_tool_to_mcp_schema(tool) for tool in PLATFORM_TOOLS]

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        return [
            Tool(
                name=schema["name"],
                description=schema["description"],
                inputSchema=schema["inputSchema"],
            )
            for schema in tool_schemas
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[dict]:
        if name not in tool_map:
            return [{"type": "text", "text": f"Unknown tool: {name}", "isError": True}]

        logger.info("mcp_tool_call", tool=name, args_keys=list(arguments.keys()))
        return await invoke_langchain_tool(tool_map[name], arguments)

    return server
