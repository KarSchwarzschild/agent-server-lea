"""
SkillPipeline — 确定性工作流执行框架。

Pipeline 模式 vs ReAct 模式：
- ReAct：LLM 自主决定调用哪些 Tool（灵活但不可预测）
- Pipeline：代码定义的确定性步骤流（可预测、可测试）

Pipeline 通过 yield SkillEvent 流式输出进度和结果。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool

    from lea_graph.skills.schema import SkillDefinition


@dataclass
class SkillEvent:
    """Pipeline 步骤产出的事件。

    type 取值：
    - progress: 进度提示（如"正在检索知识..."）
    - content: 流式文本内容（逐 chunk）
    - result: 结构化结果（如 JSON 数据）
    - error: 执行错误
    - done: 完成
    """

    type: str
    data: dict[str, Any] = field(default_factory=dict)


class SkillPipeline(ABC):
    """Pipeline 基类 — 所有确定性工作流继承此类。"""

    @abstractmethod
    async def run(
        self,
        definition: SkillDefinition,
        message: str,
        *,
        tools: dict[str, BaseTool] | None = None,
        llm_factory: Any | None = None,
        **kwargs: Any,
    ) -> AsyncIterator[SkillEvent]:
        """执行工作流，yield SkillEvent 流式输出。

        Args:
            definition: Skill 定义（含 system_prompt、model_tier 等）
            message: 用户输入
            tools: 允许使用的工具实例 {name: BaseTool}
            llm_factory: LLM 工厂函数，调用方式 llm_factory(purpose)
            **kwargs: 扩展参数（user_id, session_id 等）
        """
        yield  # type: ignore[misc]
