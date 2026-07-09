"""
SkillLoader — 从 skill.md 加载技能定义（Anthropic Skills 格式）。

skill.md 顶层只接受 4 个 key：name / description / license / metadata。
平台字段全部在 metadata.* 下。其他顶层 key 会产生 warning 但兼容读取（Ph1 过渡）。

Triggers 从 description 的 "Triggers: A, B, C" 子句正则提取。
pipeline_class 不从 md 读，而是查 pipeline_registry（内置 skill 硬编码映射）。
references/ 与 scripts/ 目录扫描，路径存入 SkillDefinition，
内容由 SkillService 按需读入 skill_resources 表。
"""

from __future__ import annotations

import importlib
import importlib.util
import re
from pathlib import Path
from typing import Any

import structlog
import yaml

from lea_graph.skills.pipeline_registry import get_pipeline_class
from lea_graph.skills.schema import (
    SKILL_STATUS_PUBLISHED,
    ModelTier,
    SkillDefinition,
)

logger = structlog.get_logger()


# Anthropic Skills 标准顶层 key —— 其他 key 触发 warning
_ANTHROPIC_TOP_LEVEL_KEYS = frozenset({"name", "description", "license", "metadata"})

# description 中 "Triggers: A, B, C" 子句提取
# 匹配到 . 或换行或字符串结尾为止；中英逗号/顿号都作为分隔
_TRIGGERS_PATTERN = re.compile(
    r"Triggers?\s*[:：]\s*(.+?)(?:[。.\n]|$)",
    re.IGNORECASE,
)
_TRIGGER_SPLIT = re.compile(r"[,，、;；]")


class SkillLoader:
    """从文件系统加载技能定义。"""

    def __init__(self, skill_dirs: list[str | Path] | None = None):
        self._dirs = [Path(d) for d in (skill_dirs or [])]
        self._cache: dict[str, SkillDefinition] = {}

    def add_directory(self, path: str | Path) -> None:
        self._dirs.append(Path(path))

    def load_all(self) -> dict[str, SkillDefinition]:
        """扫描所有目录，加载 skill.md。返回 {name: SkillDefinition}。"""
        self._cache.clear()

        for skill_dir in self._dirs:
            if not skill_dir.exists():
                logger.warning("skill_dir_not_found", path=str(skill_dir))
                continue

            for md_file in skill_dir.rglob("skill.md"):
                try:
                    definition = self._parse_skill_md(md_file)
                    if definition.name in self._cache:
                        logger.warning(
                            "skill_name_conflict",
                            name=definition.name,
                            path=str(md_file),
                        )
                        continue
                    self._cache[definition.name] = definition
                    logger.info(
                        "skill_loaded",
                        name=definition.name,
                        path=str(md_file),
                        pipeline=bool(definition.pipeline_class),
                        refs=len(definition.references_paths),
                        scripts=len(definition.scripts_paths),
                    )
                except Exception as e:
                    logger.error("skill_load_error", path=str(md_file), error=str(e))

        return dict(self._cache)

    def _parse_skill_md(self, path: Path) -> SkillDefinition:
        content = path.read_text(encoding="utf-8")
        frontmatter, body = self._split_frontmatter(content)
        meta: dict[str, Any] = yaml.safe_load(frontmatter) or {}

        # 1. Anthropic 白名单校验（超出的 key 只 warning，兼容读取）
        unknown_top = set(meta.keys()) - _ANTHROPIC_TOP_LEVEL_KEYS
        if unknown_top:
            logger.warning(
                "skill_md_non_anthropic_top_level_keys",
                path=str(path),
                keys=sorted(unknown_top),
                hint="move to metadata.* per Anthropic Skills format",
            )

        # 2. 4 个顶层字段
        name = str(meta.get("name") or path.parent.name)
        description = str(meta.get("description", "") or "")
        license_ = str(meta.get("license", "") or "")
        md_meta: dict[str, Any] = meta.get("metadata") or {}
        if not isinstance(md_meta, dict):
            logger.warning("skill_md_metadata_not_dict", path=str(path), type=type(md_meta).__name__)
            md_meta = {}

        # 3. metadata.* 与 deprecated 顶层字段兼容读取
        def _pick(key: str, default: Any = None, *, alt_keys: tuple[str, ...] = ()) -> Any:
            if key in md_meta:
                return md_meta[key]
            for alt in alt_keys:
                if alt in md_meta:
                    return md_meta[alt]
            # deprecated 顶层字段兼容
            if key in meta and key not in _ANTHROPIC_TOP_LEVEL_KEYS:
                logger.warning(
                    "skill_md_deprecated_top_level_field",
                    path=str(path),
                    field=key,
                    hint=f"move to metadata.{key}",
                )
                return meta[key]
            for alt in alt_keys:
                if alt in meta and alt not in _ANTHROPIC_TOP_LEVEL_KEYS:
                    logger.warning(
                        "skill_md_deprecated_top_level_field",
                        path=str(path),
                        field=alt,
                        hint=f"move to metadata.{key}",
                    )
                    return meta[alt]
            return default

        display_name = str(_pick("display_name", name))
        category = str(_pick("category", "general"))
        version = str(_pick("version", "1.0.0"))
        status = str(_pick("status", SKILL_STATUS_PUBLISHED))

        # required_tools 主字段；兼容旧 allowed_tools
        required_tools_raw = _pick("required_tools", [], alt_keys=("allowed_tools",)) or []
        required_tools = [str(t) for t in required_tools_raw if t]

        model_tier_raw = str(_pick("model_tier", "mid"))
        try:
            model_tier = ModelTier(model_tier_raw)
        except ValueError:
            logger.warning(
                "skill_md_invalid_model_tier",
                path=str(path),
                value=model_tier_raw,
                fallback="mid",
            )
            model_tier = ModelTier.MID

        # 4. Triggers 从 description 提取
        trigger_keywords = self._extract_triggers(description)
        if not trigger_keywords:
            # 兜底：如果 description 无 Triggers 子句，试 metadata.trigger_keywords（deprecated）
            legacy = _pick("trigger_keywords", []) or []
            if legacy:
                logger.warning(
                    "skill_md_triggers_via_deprecated_field",
                    name=name,
                    hint="add 'Triggers: X, Y, Z' to description",
                )
                trigger_keywords = [str(t) for t in legacy if t]

        # 5. pipeline_class 从 registry 查（skill.md 不再声明）
        pipeline_class = get_pipeline_class(name)
        # 如果 md 里显式写了（deprecated），警告但优先 registry
        md_pipeline = _pick("pipeline_class", "")
        if md_pipeline and md_pipeline != pipeline_class:
            logger.warning(
                "skill_md_pipeline_class_ignored",
                name=name,
                md_value=md_pipeline,
                registry_value=pipeline_class,
                hint="pipeline_class is registry-managed; remove from skill.md",
            )

        # 6. references/scripts 目录扫描 —— 相对 bundle_dir 的路径
        bundle_dir = path.parent
        references_paths = _list_relative_files(bundle_dir / "references", bundle_dir)
        scripts_paths = _list_relative_files(bundle_dir / "scripts", bundle_dir)

        return SkillDefinition(
            name=name,
            description=description,
            license=license_,
            display_name=display_name,
            category=category,
            version=version,
            model_tier=model_tier,
            required_tools=required_tools,
            status=status,
            system_prompt=body.strip(),
            pipeline_class=pipeline_class,
            is_builtin=True,
            source_path=str(path),
            bundle_dir=str(bundle_dir),
            references_paths=references_paths,
            scripts_paths=scripts_paths,
            trigger_keywords=trigger_keywords,  # 兼容 registry 老 API
        )

    @staticmethod
    def _extract_triggers(description: str) -> list[str]:
        """从 description 抓 'Triggers: A, B, C' 子句 → 关键词列表。"""
        m = _TRIGGERS_PATTERN.search(description)
        if not m:
            return []
        raw = m.group(1).strip()
        return [t.strip() for t in _TRIGGER_SPLIT.split(raw) if t.strip()]

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
        """加载技能目录下的 tools.py 模块（如果存在）。Ph1 保留兼容，未来搬 sandbox。"""
        tools_file = skill_dir / "tools.py"
        if not tools_file.exists():
            return []
        try:
            spec = importlib.util.spec_from_file_location(
                f"skill_tools_{skill_dir.name}", tools_file
            )
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


def _list_relative_files(target_dir: Path, base_dir: Path) -> list[str]:
    """扫描 target_dir 下所有文件，返回相对 base_dir 的 POSIX 路径。"""
    if not target_dir.exists() or not target_dir.is_dir():
        return []
    return sorted(
        p.relative_to(base_dir).as_posix()
        for p in target_dir.rglob("*")
        if p.is_file()
    )
