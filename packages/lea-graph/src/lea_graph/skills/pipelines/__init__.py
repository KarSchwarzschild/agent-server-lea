"""Skill Pipeline 实现包 — 确定性工作流集合。"""

from lea_graph.skills.pipelines.slide_editor import SlideEditorPipeline
from lea_graph.skills.pipelines.slide_generator import SlideGeneratorPipeline

__all__ = ["SlideGeneratorPipeline", "SlideEditorPipeline"]
