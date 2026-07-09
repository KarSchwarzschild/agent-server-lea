"""
Skill 定义数据模型。

Skill = system_prompt(Anthropic Skills md body) + 元数据 + 可选 pipeline 实现。

来源：
- 内置：skill.md（Anthropic 4-key 格式）+ pipeline_registry.BUILTIN_PIPELINES
- 用户：数据库（skills 表）

Anthropic Skills skill.md 格式（顶层只允许 4 个 key）：
    ---
    name: slide-generator
    description: 根据主题生成 SVG 演示文稿。Triggers: PPT, 演示文稿, 幻灯片
    license: MIT
    metadata:
      display_name: 演示文稿生成
      category: document
      model_tier: high
      required_tools: []
    ---
    你是一位演示文稿设计专家...（→ system_prompt）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

# ── DB status enum（对齐 lea_server.models.skill 的 SKILL_STATUS_*）──
SKILL_STATUS_DRAFT = "draft"
SKILL_STATUS_PUBLISHED = "published"
SKILL_STATUS_DISABLED = "disabled"
SKILL_STATUS_ARCHIVED = "archived"
SKILL_STATUS_VALUES = (
    SKILL_STATUS_DRAFT,
    SKILL_STATUS_PUBLISHED,
    SKILL_STATUS_DISABLED,
    SKILL_STATUS_ARCHIVED,
)


class ModelTier(str, Enum):
    HIGH = "high"
    MID = "mid"
    LOW = "low"


class ContextMode(str, Enum):
    """已 deprecated（Ph1 架构不再区分 fork/shared，agent 自持上下文）。

    保留 enum 以防有旧代码 import；loader 会忽略 skill.md 里的 context 字段。
    """

    FORK = "fork"
    SHARED = "shared"


class ExecutionMode(str, Enum):
    """执行模式由 loader 从 pipeline_class 派生：非空 → PIPELINE，空 → REACT。

    skill.md 不再显式声明。
    """

    REACT = "react"
    PIPELINE = "pipeline"


class SkillScope(str, Enum):
    SYSTEM = "system"
    PUBLIC = "public"
    PRIVATE = "private"


@dataclass
class SkillDefinition:
    """技能定义 — 纯数据结构，不依赖 ORM。

    字段分两组：
    1. Anthropic + 平台核心：name/description/license/system_prompt/status 等
    2. 兼容旧字段（保留给 executor/runner）：allowed_tools/enabled/trigger_keywords/
       execution_mode/pipeline_class。由 loader 从新字段镜像回填，DB 存的以新字段为准。
    """

    # ── Anthropic 4-key 对齐 ──────────────────────────────────
    name: str = ""
    description: str = ""  # 含 "Triggers: ..." 子句
    license: str = ""

    # ── metadata.* 平台字段 ───────────────────────────────────
    display_name: str = ""
    category: str = "general"
    version: str = "1.0.0"
    model_tier: ModelTier = ModelTier.MID
    required_tools: list[str] = field(default_factory=list)
    status: str = SKILL_STATUS_PUBLISHED
    tenant_id: str = "default"

    # ── body 与执行 ──────────────────────────────────────────
    system_prompt: str = ""  # skill.md body 全文
    pipeline_class: str = ""  # 由 pipeline_registry 填入，非空 → pipeline 执行

    # ── 生命周期 ─────────────────────────────────────────────
    scope: SkillScope = SkillScope.SYSTEM
    is_builtin: bool = False
    created_by: str | None = None
    id: int | None = None

    # ── 附件：referennces/scripts 相对路径清单（内容按需从磁盘/DB 读）──
    bundle_dir: str = ""  # skill.md 所在目录绝对路径（loader 从 OBS pull 后解压到此）
    references_paths: list[str] = field(default_factory=list)
    scripts_paths: list[str] = field(default_factory=list)
    source_path: str = ""  # skill.md 文件绝对路径

    # ── OBS Bundle 元数据（SG-2 起，运行时唯一真源）──
    bundle_uri: str = ""      # s3://bucket/skills/{sha256}.tar.gz
    bundle_hash: str = ""     # sha256 (hex)
    bundle_size: int = 0
    bundle_manifest: dict = field(default_factory=dict)  # {rel_path: file_sha256}

    # ── 兼容层（旧代码仍读这些属性；由 __post_init__ 从新字段镜像）──
    allowed_tools: list[str] = field(default_factory=list)
    enabled: bool = True
    execution_mode: ExecutionMode = ExecutionMode.REACT
    trigger_keywords: list[str] = field(default_factory=list)
    context: ContextMode = ContextMode.SHARED  # deprecated（不再影响执行）
    resources: dict = field(default_factory=dict)  # deprecated（→ references/scripts_paths）

    def __post_init__(self) -> None:
        # required_tools ↔ allowed_tools 双向镜像（loader 只填 required_tools）
        if self.required_tools and not self.allowed_tools:
            self.allowed_tools = list(self.required_tools)
        elif self.allowed_tools and not self.required_tools:
            self.required_tools = list(self.allowed_tools)

        # status → enabled 派生（registry 里 list_all 过滤用）
        self.enabled = self.status == SKILL_STATUS_PUBLISHED

        # pipeline_class → execution_mode 派生
        self.execution_mode = (
            ExecutionMode.PIPELINE if self.pipeline_class else ExecutionMode.REACT
        )
