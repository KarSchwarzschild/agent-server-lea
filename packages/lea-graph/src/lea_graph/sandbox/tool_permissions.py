"""
工具权限校验 — per-skill 工具访问控制。

结合 SkillDefinition.allowed_tools 和 SecurityPolicy.blocked_tools，
双重校验工具调用权限。
"""

from __future__ import annotations

import structlog

from lea_graph.sandbox.security import SecurityPolicy

logger = structlog.get_logger()


class ToolCallDenied(Exception):
    def __init__(self, tool_name: str, reason: str):
        self.tool_name = tool_name
        super().__init__(f"Tool call denied: {tool_name} — {reason}")


class ToolPermissionChecker:
    """工具权限校验器。"""

    def __init__(self, policy: SecurityPolicy):
        self._policy = policy

    def check(self, tool_name: str, allowed_tools: list[str] | None = None) -> None:
        """校验工具调用权限。

        Args:
            tool_name: 要调用的工具名
            allowed_tools: 当前技能允许的工具列表（None = 允许所有非黑名单工具）
        """
        if self._policy.is_tool_blocked(tool_name):
            logger.warning("blocked_tool_call", tool=tool_name)
            raise ToolCallDenied(tool_name, "tool is in global blacklist")

        if allowed_tools is not None and tool_name not in allowed_tools:
            logger.warning("unauthorized_tool_call", tool=tool_name, allowed=allowed_tools)
            raise ToolCallDenied(tool_name, f"not in allowed list: {allowed_tools}")

    def filter_tools(self, tool_names: list[str], allowed_tools: list[str] | None = None) -> list[str]:
        """过滤出允许的工具列表。"""
        result = []
        for name in tool_names:
            if self._policy.is_tool_blocked(name):
                continue
            if allowed_tools is not None and name not in allowed_tools:
                continue
            result.append(name)
        return result
