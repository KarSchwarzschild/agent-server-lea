"""内置 Skill Pipeline 注册表 —— skill_name → Python 类路径映射。

设计动机：
- skill.md 遵循 Anthropic Skills 纯格式（只有 name/description/license/metadata），
  不允许在 md 里写 `pipeline_class: xxx` 这种 Python 实现耦合。
- 平台侧仍需要知道"哪些内置 skill 是 pipeline 模式、对应哪个 Python 类"。
- 用户/管理员自定义的 skill 走 ReAct 模式，不进这张表。

用法：
- Loader 解析 skill.md 时，用 `get_pipeline_class(name)` 查表，填 SkillDefinition.pipeline_class
- SkillService.sync_builtins 同步 DB 时，也用同一张表写 skills.pipeline_class
- 新增内置 pipeline 只需在此处登记一行 + skill.md 存在即可
"""

from __future__ import annotations

BUILTIN_PIPELINES: dict[str, str] = {
    "slide-generator": "lea_graph.skills.pipelines.slide_generator.SlideGeneratorPipeline",
    "slide-editor": "lea_graph.skills.pipelines.slide_editor.SlideEditorPipeline",
}


def get_pipeline_class(skill_name: str) -> str:
    """按 skill_name 查内置 pipeline 类路径。未登记返回空串（→ ReAct 模式）。"""
    return BUILTIN_PIPELINES.get(skill_name, "")


def is_builtin_pipeline(skill_name: str) -> bool:
    return skill_name in BUILTIN_PIPELINES


def list_builtin_pipeline_names() -> list[str]:
    return sorted(BUILTIN_PIPELINES.keys())
