"""
SkillExecutor — 技能执行引擎。

执行技能时：
1. 校验工具权限（只允许 allowed_tools 中的工具）
2. 绑定工具实例
3. 注入 system prompt 和上下文
4. 选择合适的 LLM（根据 model_tier）

支持两种执行模式：
- ReAct（默认）：单次 LLM ainvoke
- Pipeline：动态加载 SkillPipeline 子类，yield SkillEvent
"""

from __future__ import annotations

import importlib
from collections.abc import AsyncIterator
from typing import Any

import structlog
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.tools import BaseTool

from lea_graph.llm import get_chat_model
from lea_graph.skills.pipeline import SkillEvent, SkillPipeline
from lea_graph.skills.schema import ExecutionMode, ModelTier, SkillDefinition

logger = structlog.get_logger()

_TIER_TO_PURPOSE = {
    ModelTier.HIGH: "chat",
    ModelTier.MID: "routing",
    ModelTier.LOW: "fallback",
}


class ToolPermissionError(Exception):
    """技能尝试调用未授权的工具。"""

    def __init__(self, skill_name: str, tool_name: str, allowed: list[str]):
        self.skill_name = skill_name
        self.tool_name = tool_name
        super().__init__(
            f"Skill '{skill_name}' is not allowed to use tool '{tool_name}'. "
            f"Allowed: {allowed}"
        )


class SkillExecutor:
    """技能执行引擎。"""

    def __init__(self, all_tools: dict[str, BaseTool]):
        self._all_tools = all_tools

    def get_allowed_tools(self, definition: SkillDefinition) -> list[BaseTool]:
        """过滤出技能允许使用的工具。"""
        if not definition.allowed_tools:
            return list(self._all_tools.values())

        allowed = []
        for tool_name in definition.allowed_tools:
            tool = self._all_tools.get(tool_name)
            if tool:
                allowed.append(tool)
            else:
                logger.warning("allowed_tool_not_found", skill=definition.name, tool=tool_name)
        return allowed

    def validate_tool_call(self, definition: SkillDefinition, tool_name: str) -> None:
        """校验工具调用权限。"""
        if definition.allowed_tools and tool_name not in definition.allowed_tools:
            raise ToolPermissionError(definition.name, tool_name, definition.allowed_tools)

    def get_llm(self, definition: SkillDefinition):
        """根据 model_tier 获取合适的 LLM。"""
        purpose = _TIER_TO_PURPOSE.get(definition.model_tier, "chat")
        return get_chat_model(purpose=purpose)

    async def execute(
        self,
        definition: SkillDefinition,
        user_message: str,
        history: list[BaseMessage] | None = None,
    ) -> AIMessage:
        """执行技能 — 注入 system prompt，调用 LLM。"""
        llm = self.get_llm(definition)
        tools = self.get_allowed_tools(definition)

        if tools:
            llm = llm.bind_tools(tools)

        messages: list[BaseMessage] = []
        if definition.system_prompt:
            messages.append(SystemMessage(content=definition.system_prompt))
        if history:
            messages.extend(history)
        messages.append(HumanMessage(content=user_message))

        logger.info(
            "skill_executing",
            skill=definition.name,
            tier=definition.model_tier.value,
            tool_count=len(tools),
        )

        return await llm.ainvoke(messages)

    async def execute_pipeline(
        self,
        definition: SkillDefinition,
        message: str,
        **kwargs: Any,
    ) -> AsyncIterator[SkillEvent]:
        """Pipeline 模式执行 — 动态加载 pipeline_class 并运行。"""
        if not definition.pipeline_class:
            yield SkillEvent("error", {"message": f"Skill '{definition.name}' has no pipeline_class"})
            return

        pipeline_cls = self._load_pipeline_class(definition.pipeline_class)
        pipeline = pipeline_cls()
        tools = {t.name: t for t in self.get_allowed_tools(definition)}

        try:
            async for event in pipeline.run(
                definition,
                message,
                tools=tools,
                llm_factory=lambda purpose="chat": get_chat_model(
                    purpose=purpose, streaming=False, max_tokens=4096, timeout=300.0,
                ),
                **kwargs,
            ):
                yield event
        except Exception as exc:
            logger.error("pipeline_execution_error", skill=definition.name, error=str(exc))
            yield SkillEvent("error", {"message": str(exc)})

    @staticmethod
    def _load_pipeline_class(class_path: str) -> type[SkillPipeline]:
        """动态加载 Pipeline 类。"""
        module_path, class_name = class_path.rsplit(".", 1)
        module = importlib.import_module(module_path)
        cls = getattr(module, class_name)
        if not issubclass(cls, SkillPipeline):
            raise TypeError(f"{class_path} is not a SkillPipeline subclass")
        return cls
