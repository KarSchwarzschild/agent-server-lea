"""MCP 配置 — 从环境变量读取外部 MCP 服务器列表。"""

from __future__ import annotations

import json
from functools import lru_cache

from pydantic_settings import BaseSettings


class MCPSettings(BaseSettings):
    mcp_servers: str = ""

    model_config = {"env_prefix": "LEA_", "extra": "ignore"}

    def get_server_configs(self) -> dict:
        if not self.mcp_servers:
            return {}
        return json.loads(self.mcp_servers)


@lru_cache
def get_mcp_settings() -> MCPSettings:
    return MCPSettings()
