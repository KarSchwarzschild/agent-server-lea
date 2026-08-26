"""Research App — 学术综述生成（HITL + Reflection）。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter

from lea_graph.protocol import AgentInfo, AppManifest, FrontendRoute, SkillInfo

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool
    from langgraph.graph.state import CompiledStateGraph


class ResearchApp:

    @property
    def manifest(self) -> AppManifest:
        return AppManifest(
            name="research",
            display_name="学术研究",
            description="AI辅助学术综述生成：多源检索 → 文献分析 → 大纲 → 综述，支持 HITL 人机协作",
            agents=[
                AgentInfo(
                    name="review",
                    display_name="综述生成",
                    description="LangGraph HITL + Reflection 综述管道",
                    skill_names=["academic-search", "kb-search"],
                ),
            ],
            skills=[
                SkillInfo(
                    name="academic-search",
                    display_name="学术文献检索",
                    description="多源学术检索（万方/arXiv/Semantic Scholar）",
                    trigger_keywords=["论文", "文献", "检索", "搜索"],
                    category="research",
                    scope="global",
                ),
            ],
            routes=[
                FrontendRoute(path="/research", label="学术研究", icon="BookOpen"),
            ],
        )

    def build_agents(self, **kwargs) -> dict[str, CompiledStateGraph]:
        from lea_apps.research.agents.review_graph import build_review_graph

        return {"review": build_review_graph(checkpointer=kwargs.get("checkpointer"))}

    @property
    def skills(self) -> list[BaseTool]:
        from lea_apps.research.skills.research_tools import academic_search_tool

        return [academic_search_tool]

    @property
    def router(self) -> APIRouter:
        from lea_apps.research.api.routes import router

        return router

    def get_models(self) -> list[type]:
        from lea_apps.research.models.models import ReviewTask

        return [ReviewTask]


def create_app() -> ResearchApp:
    return ResearchApp()
