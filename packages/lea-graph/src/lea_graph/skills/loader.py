"""
SkillLoader — 从 skill.md 文件动态加载技能定义。

扫描指定目录下的 skill.md 文件，解析 YAML frontmatter + Markdown body，
生成 SkillDefinition 实例。支持热加载（重新扫描）。
"""

from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
from typing import Any

import structlog
import yaml

from lea_graph.skills.schema import ContextMode, ExecutionMode, ModelTier, SkillDefinition

logger = structlog.get_logger()


class SkillLoader:
    """从文件系统加载技能定义。"""

    def __init__(self, skill_dirs: list[str | Path] | None = None):
        self._dirs = [Path(d) for d in (skill_dirs or [])]
        self._cache: dict[str, SkillDefinition] = {}

    def add_directory(self, path: str | Path) -> None:
        self._dirs.append(Path(path))

    def load_all(self) -> dict[str, SkillDefinition]:
        """扫描所有目录，加载 skill.md 文件。返回 {name: SkillDefinition}。"""
        self._cache.clear()

        for skill_dir in self._dirs:
            if not skill_dir.exists():
                logger.warning("skill_dir_not_found", path=str(skill_dir))
                continue

            for md_file in skill_dir.rglob("skill.md"):
                try:
                    definition = self._parse_skill_md(md_file)
                    if definition.name in self._cache:
                        logger.warning("skill_name_conflict", name=definition.name, path=str(md_file))
                        continue
                    self._cache[definition.name] = definition
                    logger.info("skill_loaded", name=definition.name, path=str(md_file))
                except Exception as e:
                    logger.error("skill_load_error", path=str(md_file), error=str(e))

        return dict(self._cache)

    def _parse_skill_md(self, path: Path) -> SkillDefinition:
        """解析 skill.md — YAML frontmatter + Markdown body。"""
        content = path.read_text(encoding="utf-8")
        frontmatter, body = self._split_frontmatter(content)

        meta: dict[str, Any] = yaml.safe_load(frontmatter) or {}

        model_tier_raw = meta.get("model_tier", "mid")
        try:
            model_tier = ModelTier(model_tier_raw)
        except ValueError:
            model_tier = ModelTier.MID

        context_raw = meta.get("context", "shared")
        try:
            context = ContextMode(context_raw)
        except ValueError:
            context = ContextMode.SHARED

        exec_mode_raw = meta.get("execution_mode", "react")
        try:
            execution_mode = ExecutionMode(exec_mode_raw)
        except ValueError:
            execution_mode = ExecutionMode.REACT

        return SkillDefinition(
            name=meta.get("name", path.parent.name),
            display_name=meta.get("display_name", meta.get("name", "")),
            description=meta.get("description", ""),
            category=meta.get("category", "general"),
            trigger_keywords=meta.get("trigger_keywords", []),
            model_tier=model_tier,
            context=context,
            allowed_tools=meta.get("allowed_tools", []),
            system_prompt=body.strip(),
            source_path=str(path),
            enabled=meta.get("enabled", True),
            version=meta.get("version", "1.0.0"),
            execution_mode=execution_mode,
            pipeline_class=meta.get("pipeline_class", ""),
            is_builtin=True,
            resources=meta.get("resources", {}),
        )

    @staticmethod
    def _split_frontmatter(content: str) -> tuple[str, str]:
        """分离 YAML frontmatter 和 Markdown body。"""
        if not content.startswith("---"):
            return "", content

        parts = content.split("---", 2)
        if len(parts) < 3:
            return "", content

        return parts[1], parts[2]

    def load_tools_module(self, skill_dir: Path) -> list:
        """加载技能目录下的 tools.py 模块（如果存在）。"""
        tools_file = skill_dir / "tools.py"
        if not tools_file.exists():
            return []

        try:
            spec = importlib.util.spec_from_file_location(f"skill_tools_{skill_dir.name}", tools_file)
            if spec and spec.loader:
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                return getattr(module, "TOOLS", [])
        except Exception as e:
            logger.error("skill_tools_load_error", path=str(tools_file), error=str(e))
        return []

    @property
    def skills(self) -> dict[str, SkillDefinition]:
        return dict(self._cache)
