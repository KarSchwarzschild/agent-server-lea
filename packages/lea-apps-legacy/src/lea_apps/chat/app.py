"""Chat App — 智能对话（迁移自旧项目Super Agent）。

这是第一个正式迁移的App，验证整套架构。
旧项目的 SuperAgentOrchestrator 手动实现的 intent→skill→LLM 循环，
在这里用 LangGraph create_react_agent 替代（内建ReAct循环）。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter
from langchain_core.tools import BaseTool

from lea_graph.protocol import AgentInfo, AppManifest, SkillInfo

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph


class ChatApp:
    """智能对话App — 基于多Skill的教育科研对话助手。"""

    @property
    def manifest(self) -> AppManifest:
        return AppManifest(
            name="chat",
            display_name="智能对话",
            description="基于多Skill的教育科研对话助手，可调用知识库搜索、学术搜索、网页搜索等工具",
            agents=[
                AgentInfo(
                    name="assistant",
                    display_name="对话助手",
                    description="回答教育科研问题，自动选择合适的工具获取信息",
                    skill_names=["kb-search", "academic-search", "web-search"],
                ),
            ],
            skills=[
                SkillInfo(
                    name="academic-search",
                    display_name="学术搜索",
                    description="搜索学术文献数据库",
                    trigger_keywords=["论文", "文献", "学术", "研究", "期刊", "引用"],
                    category="research",
                ),
                SkillInfo(
                    name="web-search",
                    display_name="网页搜索",
                    description="搜索网页获取最新信息",
                    trigger_keywords=["搜索", "查找", "网上", "最新", "新闻"],
                    category="search",
                ),
                SkillInfo(
                    name="document-generation",
                    display_name="文档生成",
                    description="生成PPT、Word等文档文件",
                    trigger_keywords=["PPT", "ppt", "演示", "幻灯片", "Word", "文档", "报告", "生成文件", "做一个"],
                    category="writing",
                ),
            ],
            routes=[],  # 对话走统一Chat入口，无独立页面
        )

    def build_agents(self, **kwargs) -> dict[str, CompiledStateGraph]:
        from lea_apps.chat.agents.assistant import build_chat_assistant

        return {
            "assistant": build_chat_assistant(
                llm_factory=kwargs["llm"],
                platform_tools=kwargs.get("platform_tools", []),
                app_skills=self.skills,
            )
        }

    @property
    def skills(self) -> list[BaseTool]:
        from lea_apps.chat.skills.academic import academic_search
        from lea_apps.chat.skills.web_search import web_search

        return [academic_search, web_search]

    @property
    def router(self) -> APIRouter:
        from lea_apps.chat.api.routes import router

        return router

    def get_models(self) -> list[type]:
        from lea_apps.chat.models.models import ChatMessage, ChatSession

        return [ChatSession, ChatMessage]


def create_app() -> ChatApp:
    return ChatApp()
