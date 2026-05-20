"""
App（分产品）协议定义 — 整个架构的"合同"。

每个App（如AI课堂、学术研究）必须实现 AppProtocol，
平台通过此协议发现、加载、注册App的Agent/Skill/API/数据库模型。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from fastapi import APIRouter
    from langchain_core.tools import BaseTool
    from langgraph.graph.state import CompiledStateGraph


@dataclass(frozen=True)
class SkillInfo:
    """Skill元信息（公共或App专属）。

    Skill = 可复用工具，用 LangChain @tool 定义。
    SkillInfo 仅描述元数据，实际实例通过 AppProtocol.skills 获取。
    """

    name: str
    display_name: str
    description: str
    trigger_keywords: list[str] = field(default_factory=list)
    category: str = "general"


@dataclass(frozen=True)
class AgentInfo:
    """Agent元信息。

    Agent = App内的AI执行单元，用 LangGraph StateGraph 实现。
    一个App可以有多个Agent（如教学助手 + 课堂编排器）。
    """

    name: str
    display_name: str
    description: str
    skill_names: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class FrontendRoute:
    """App前端页面入口，平台据此动态渲染侧栏菜单。"""

    path: str
    label: str
    icon: str = ""


@dataclass(frozen=True)
class AppManifest:
    """App = 分产品，完整子系统的声明清单。"""

    name: str
    display_name: str
    description: str
    version: str = "0.1.0"
    category: str = "general"
    agents: list[AgentInfo] = field(default_factory=list)
    skills: list[SkillInfo] = field(default_factory=list)
    routes: list[FrontendRoute] = field(default_factory=list)


@runtime_checkable
class AppProtocol(Protocol):
    """所有App（分产品）必须实现的协议。"""

    @property
    def manifest(self) -> AppManifest: ...

    def build_agents(self, **kwargs) -> dict[str, CompiledStateGraph]:
        """返回App内所有Agent的编译图。

        key = agent名称, value = LangGraph CompiledStateGraph。
        kwargs 由平台注入：
            llm: LLM工厂函数
            platform_tools: 公共Skill列表
            services: 平台共享服务（知识库、文件存储、记忆等）
        """
        ...

    @property
    def skills(self) -> list[BaseTool]:
        """App专属的Skill实例列表（LangChain @tool）。"""
        ...

    @property
    def router(self) -> APIRouter | None:
        """App独立页面的后端API路由。返回None表示无独立API。"""
        ...

    def get_models(self) -> list[type]:
        """App自己的SQLAlchemy数据库模型列表。平台启动时统一建表。"""
        ...
