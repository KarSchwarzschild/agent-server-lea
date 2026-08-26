"""
LangChain @tool → MCP Tool 转换器。

将 LangChain 的 BaseTool 转换为 MCP 协议的 Tool 格式，
使外部 Agent 可以通过 MCP 调用平台能力。
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import BaseTool


def langchain_tool_to_mcp_schema(tool: BaseTool) -> dict[str, Any]:
    """将 LangChain tool 的 schema 转换为 MCP Tool JSON-Schema。"""
    input_schema = {"type": "object", "properties": {}, "required": []}

    if hasattr(tool, "args_schema") and tool.args_schema:
        schema = tool.args_schema.model_json_schema()
        input_schema["properties"] = schema.get("properties", {})
        input_schema["required"] = schema.get("required", [])

    return {
        "name": tool.name,
        "description": tool.description or "",
        "inputSchema": input_schema,
    }


async def invoke_langchain_tool(tool: BaseTool, arguments: dict[str, Any]) -> list[dict]:
    """通过 MCP 调用 LangChain tool，返回 MCP content 格式。"""
    try:
        if hasattr(tool, "ainvoke"):
            result = await tool.ainvoke(arguments)
        else:
            result = tool.invoke(arguments)

        if isinstance(result, str):
            text = result
        else:
            text = json.dumps(result, ensure_ascii=False, default=str)

        return [{"type": "text", "text": text}]
    except Exception as e:
        return [{"type": "text", "text": f"Error: {e}", "isError": True}]
