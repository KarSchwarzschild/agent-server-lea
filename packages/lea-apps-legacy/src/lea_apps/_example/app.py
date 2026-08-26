"""示例App — 演示完整的分产品结构。

这个App展示了 AppProtocol 的完整实现：
- 1个Agent（教育问答）
- 1个专属Skill（测验生成）
- 独立API（资源CRUD）
- 独立数据库模型
- 前端页面入口
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter
from langchain_core.tools import BaseTool
from langgraph.prebuilt import create_react_agent

from lea_graph.protocol import AgentInfo, AppManifest, FrontendRoute, SkillInfo

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph


class ExampleApp:
    """示例应用 — 用于开发调试和新App开发参考。"""

    @property
    def manifest(self) -> AppManifest:
        return AppManifest(
            name="_example",
            display_name="示例应用",
            description="演示完整的分产品结构：Agent + Skill + API + 数据库",
            agents=[
                AgentInfo(
                    name="qa-agent",
                    display_name="教育问答",
                    description="回答终身教育相关问题，可以搜索知识库和生成测验",
                    skill_names=["kb-search", "generate-quiz"],
                ),
            ],
            skills=[
                SkillInfo(
                    name="generate-quiz",
                    display_name="生成测验",
                    description="根据指定主题生成测验题",
                    trigger_keywords=["测验", "quiz", "出题", "考试"],
                    category="education",
                ),
            ],
            routes=[
                FrontendRoute(path="/example", label="示例应用", icon="apps"),
            ],
        )

    def build_agents(self, **kwargs) -> dict[str, CompiledStateGraph]:
        llm_factory = kwargs["llm"]
        platform_tools = kwargs.get("platform_tools", [])

        all_tools = list(platform_tools) + self.skills

        model = llm_factory("chat", streaming=True)
        agent = create_react_agent(
            model=model,
            tools=all_tools,
            prompt="你是终身教育智能助手。你可以搜索知识库获取文献资料，也可以生成测验题帮助学习。请用中文回答。",
        )
        return {"qa-agent": agent}

    @property
    def skills(self) -> list[BaseTool]:
        from lea_apps._example.skills.quiz import generate_quiz

        return [generate_quiz]

    @property
    def router(self) -> APIRouter:
        from lea_apps._example.api.routes import router

        return router

    def get_models(self) -> list[type]:
        from lea_apps._example.models.models import ExampleResource

        return [ExampleResource]


def create_app() -> ExampleApp:
    """工厂函数 — discover_apps() 调用此函数加载App。"""
    return ExampleApp()
