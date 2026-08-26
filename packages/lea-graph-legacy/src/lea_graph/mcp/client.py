"""MCP Client — 连接管理员配置的外部 MCP 服务器。"""

from __future__ import annotations

import structlog
from langchain_core.tools import BaseTool

logger = structlog.get_logger()


class MCPClientManager:
    """管理外部 MCP 服务器连接，返回 LangChain BaseTool 列表。"""

    def __init__(self) -> None:
        self._client = None
        self._tools: list[BaseTool] = []

    async def connect(self, server_configs: dict[str, dict]) -> None:
        if not server_configs:
            logger.info("mcp_client_skip", reason="no servers configured")
            return
        try:
            from langchain_mcp_adapters.client import MultiServerMCPClient

            self._client = MultiServerMCPClient(server_configs)
            await self._client.__aenter__()
            self._tools = self._client.get_tools()
            logger.info(
                "mcp_client_connected",
                servers=list(server_configs.keys()),
                tool_count=len(self._tools),
                tools=[t.name for t in self._tools],
            )
        except ImportError:
            logger.warning(
                "mcp_client_skip",
                reason="langchain-mcp-adapters not installed",
            )
        except Exception as exc:
            logger.error("mcp_client_connect_failed", error=str(exc))

    def get_tools(self) -> list[BaseTool]:
        return list(self._tools)

    async def disconnect(self) -> None:
        if self._client:
            try:
                await self._client.__aexit__(None, None, None)
            except Exception:
                pass
            self._client = None
            self._tools = []
            logger.info("mcp_client_disconnected")


_instance: MCPClientManager | None = None


def init_mcp_client() -> MCPClientManager:
    global _instance
    _instance = MCPClientManager()
    return _instance


def get_mcp_client() -> MCPClientManager | None:
    return _instance
