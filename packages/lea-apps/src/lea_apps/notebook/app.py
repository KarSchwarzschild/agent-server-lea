"""Notebook App — NotebookLM 风格的 AI 学习助手。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter

from lea_graph.protocol import AppManifest, FrontendRoute, SkillInfo

if TYPE_CHECKING:
    from langchain_core.tools import BaseTool
    from langgraph.graph.state import CompiledStateGraph


class NotebookApp:

    @property
    def manifest(self) -> AppManifest:
        return AppManifest(
            name="notebook",
            display_name="智能笔记",
            description="上传文档，AI 解析入库，基于文档的 RAG 对话、闪卡、思维导图、PPT 生成",
            agents=[],
            skills=[
                SkillInfo(
                    name="doc-search",
                    display_name="文档检索",
                    description="在用户上传的文档中检索相关内容",
                    trigger_keywords=["文档", "资料", "笔记", "上传"],
                    category="notebook",
                    scope="app",
                ),
            ],
            routes=[
                FrontendRoute(path="/notebook", label="智能笔记", icon="BookText"),
            ],
        )

    def build_agents(self, **kwargs) -> dict[str, CompiledStateGraph]:
        return {}

    @property
    def skills(self) -> list[BaseTool]:
        return []

    @property
    def router(self) -> APIRouter:
        from lea_apps.notebook.api.routes import router
        return router

    def get_models(self) -> list[type]:
        from lea_apps.notebook.models.models import (
            NotebookChunk,
            NotebookDocKnowledge,
            NotebookMessage,
            NotebookSession,
            NotebookSource,
        )
        return [NotebookSource, NotebookChunk, NotebookDocKnowledge, NotebookSession, NotebookMessage]


def create_app() -> NotebookApp:
    return NotebookApp()
