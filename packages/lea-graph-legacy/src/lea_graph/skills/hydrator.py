"""SkillDefinition · Bundle 水合器。

从 DB SkillService.to_definition 拿到的 SkillDefinition 只带元数据（bundle_uri/hash 等），
不含 system_prompt / references_paths / scripts_paths / bundle_dir 这些运行时字段。

hydrate_bundle 负责把 OBS bundle 拉到本地 cache，读 skill.md body 填 system_prompt，
扫 references/ + scripts/ 填路径清单。cache 命中零 IO，miss 时才走一次 OBS。

调用点：chat.py 走 skill 路径前 hydrate 一次；hydrated definition 送给 supervisor / runner。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import structlog
import yaml

from lea_graph.skills.bundle_store import SkillBundleStore
from lea_graph.skills.pipeline_registry import get_pipeline_class
from lea_graph.skills.schema import ModelTier, SkillDefinition

logger = structlog.get_logger()


_ANTHROPIC_TOP_LEVEL_KEYS = frozenset({"name", "description", "license", "metadata"})
_TRIGGERS_PATTERN = re.compile(
    r"Triggers?\s*[:：]\s*(.+?)(?:[。.\n]|$)",
    re.IGNORECASE,
)
_TRIGGER_SPLIT = re.compile(r"[,，、;；]")


# 进程单例（避免每次 hydrate 都重开 boto3 client + head bucket）
_default_store: SkillBundleStore | None = None


def get_default_store() -> SkillBundleStore | None:
    """返回进程默认 SkillBundleStore；构造失败（缺依赖 / OBS 不通）返回 None。"""
    global _default_store
    if _default_store is None:
        try:
            _default_store = SkillBundleStore.from_env()
        except Exception as e:
            logger.warning("skill_bundle_store_init_failed", error=str(e))
            return None
    return _default_store


def hydrate_bundle(
    defn: SkillDefinition,
    *,
    store: SkillBundleStore | None = None,
) -> SkillDefinition:
    """把 SkillDefinition 的运行时字段从 OBS bundle 补齐。

    - defn.bundle_uri 空 / 空 hash：直接返回 defn（内置 skill fallback 本地 dir 场景）
    - store 为 None：尝试拿默认 store；仍失败则返回未 hydrate 的 defn（warn）
    - 已 hydrate（system_prompt 非空 且 bundle_dir 存在）：视为幂等直接返回

    修改 defn in-place 并返回同一对象。
    """
    if not defn.bundle_uri or not defn.bundle_hash:
        return defn

    # 幂等 short-circuit：如果 bundle_dir 已经指到 cache 且 skill.md 在，视为已 hydrate
    if defn.bundle_dir and Path(defn.bundle_dir).is_dir() and defn.system_prompt:
        return defn

    if store is None:
        store = get_default_store()
    if store is None:
        logger.warning(
            "skill_hydrate_no_store",
            name=defn.name,
            uri=defn.bundle_uri,
        )
        return defn

    try:
        cache_root = store.pull(defn.bundle_uri, defn.bundle_hash)
    except Exception as e:
        logger.error(
            "skill_hydrate_pull_failed",
            name=defn.name,
            uri=defn.bundle_uri,
            error=str(e),
        )
        return defn

    # cache_root/{top}/{skill.md, references/, scripts/}
    top = _find_bundle_top(cache_root)
    if top is None:
        logger.error("skill_hydrate_top_missing", name=defn.name, cache=str(cache_root))
        return defn

    skill_md = top / "skill.md"
    if not skill_md.exists():
        logger.error("skill_hydrate_skill_md_missing", name=defn.name, path=str(skill_md))
        return defn

    try:
        _apply_bundle_to_defn(defn, skill_md, top)
    except Exception as e:
        logger.error("skill_hydrate_parse_failed", name=defn.name, error=str(e))
        return defn

    logger.debug(
        "skill_hydrated",
        name=defn.name,
        hash=defn.bundle_hash,
        refs=len(defn.references_paths),
        scripts=len(defn.scripts_paths),
    )
    return defn


def _find_bundle_top(cache_root: Path) -> Path | None:
    """cache_root/xxx/ 是我们的 bundle 顶层目录。返回第一个含 skill.md 的子目录。"""
    for child in cache_root.iterdir():
        if child.is_dir() and (child / "skill.md").exists():
            return child
    return None


def _apply_bundle_to_defn(defn: SkillDefinition, skill_md: Path, top_dir: Path) -> None:
    """从 skill.md 提取 system_prompt + description 里的 Triggers，扫子目录填路径清单。"""
    content = skill_md.read_text(encoding="utf-8")
    frontmatter, body = _split_frontmatter(content)
    meta: dict[str, Any] = yaml.safe_load(frontmatter) if frontmatter else {}
    if not isinstance(meta, dict):
        meta = {}

    md_meta = meta.get("metadata") if isinstance(meta.get("metadata"), dict) else {}

    # 1. system_prompt = skill.md body 全文（strip）
    defn.system_prompt = body.strip()

    # 2. description（DB 已有，但如果空可以从 md 补）
    if not defn.description:
        defn.description = str(meta.get("description", "") or "")

    # 3. Triggers → trigger_keywords（SkillDefinition 的兼容层）
    if not defn.trigger_keywords:
        defn.trigger_keywords = _extract_triggers(defn.description)

    # 4. bundle_dir / references_paths / scripts_paths
    defn.bundle_dir = str(top_dir)
    defn.references_paths = _list_relative_files(top_dir / "references", top_dir)
    defn.scripts_paths = _list_relative_files(top_dir / "scripts", top_dir)

    # 5. pipeline_class 优先 registry（DB 应该已经存了）；如果 DB 空、md 里有 metadata.pipeline_class 也拿一下
    if not defn.pipeline_class:
        defn.pipeline_class = get_pipeline_class(defn.name) or str(md_meta.get("pipeline_class") or "")

    # 6. model_tier（DB 是权威，但 md 也可以兜底）
    if not defn.model_tier or defn.model_tier == ModelTier.MID:
        tier_raw = str(md_meta.get("model_tier", "") or "")
        if tier_raw:
            try:
                defn.model_tier = ModelTier(tier_raw)
            except ValueError:
                pass


def _split_frontmatter(content: str) -> tuple[str, str]:
    if not content.startswith("---"):
        return "", content
    parts = content.split("---", 2)
    if len(parts) < 3:
        return "", content
    return parts[1], parts[2]


def _extract_triggers(description: str) -> list[str]:
    m = _TRIGGERS_PATTERN.search(description)
    if not m:
        return []
    raw = m.group(1).strip()
    return [t.strip() for t in _TRIGGER_SPLIT.split(raw) if t.strip()]


def _list_relative_files(target_dir: Path, base_dir: Path) -> list[str]:
    if not target_dir.exists() or not target_dir.is_dir():
        return []
    return sorted(
        p.relative_to(base_dir).as_posix()
        for p in target_dir.rglob("*")
        if p.is_file()
    )
