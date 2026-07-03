"""
Skill 定义数据模型。

Skill = system_prompt + allowed_tools + 执行模式。
定义来源：skill.md 文件（内置）或数据库（用户/管理员创建）。

skill.md 格式：
    ---
    name: academic-search
    display_name: 学术文献检索
    category: research
    trigger_keywords: ["论文", "文献"]
    model_tier: high
    context: fork
    execution_mode: react
    allowed_tools: ["kb_search", "academic_search"]
    ---
    你是一个学术文献检索助手...（system prompt）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class ModelTier(str, Enum):
    HIGH = "high"
    MID = "mid"
    LOW = "low"


class ContextMode(str, Enum):
    FORK = "fork"
    SHARED = "shared"


class ExecutionMode(str, Enum):
    REACT = "react"
    PIPELINE = "pipeline"


class SkillScope(str, Enum):
    SYSTEM = "system"
    PUBLIC = "public"
    PRIVATE = "private"


@dataclass
class SkillDefinition:
    """技能定义 — 纯数据结构，不依赖 ORM。"""

    name: str
    display_name: str
    description: str = ""
    category: str = "general"
    trigger_keywords: list[str] = field(default_factory=list)
    model_tier: ModelTier = ModelTier.MID
    context: ContextMode = ContextMode.SHARED
    allowed_tools: list[str] = field(default_factory=list)
    system_prompt: str = ""
    source_path: str = ""
    enabled: bool = True

    id: int | None = None
    version: str = "1.0.0"
    execution_mode: ExecutionMode = ExecutionMode.REACT
    pipeline_class: str = ""
    scope: SkillScope = SkillScope.SYSTEM
    is_builtin: bool = False
    created_by: str | None = None
    resources: dict = field(default_factory=dict)
