"""Skill 服务 — DB CRUD + 内置 Skill 同步（insert_only / upsert 双模式）。

Anthropic Skills 格式约定：
- skill.md 顶层 4 keys：name / description / license / metadata
- 平台字段全部在 DB（display_name/model_tier/required_tools/pipeline_class/status/...）
- Triggers 从 description 正则提取（不单独存表）
- Skill 内容（md + references/ + scripts/）打成 tar.gz 存 OBS，DB 只留 bundle_* 元数据
  → 见 lea_graph.skills.bundle_store.SkillBundleStore

sync_builtins 两种模式（保护 UI 编辑权）：
- insert_only（启动默认）：name+tenant 已存在 → 完全跳过
- upsert（admin API 手动触发）：仅覆盖 MD_DERIVED_COLS，永不覆盖 UI_EDITABLE_COLS

分布式安全：sync 用先查后写 + IntegrityError 保底；多实例并发插入靠 UNIQUE(tenant_id, name) 兜底。
"""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from lea_graph.skills.bundle_store import SkillBundleStore
from lea_graph.skills.schema import (
    SKILL_STATUS_PUBLISHED,
    SKILL_STATUS_VALUES,
    ModelTier,
    SkillDefinition,
    SkillScope,
)
from lea_server.core.tenant import DEFAULT_TENANT_ID
from lea_server.models.skill import Skill, SkillVersion

logger = structlog.get_logger()


# upsert 时 MD 派生字段（可覆盖）—— bundle_* 独立更新路径
_MD_DERIVED_COLS = ("description", "version", "pipeline_class")

# 永不被 sync 覆盖的字段（UI/管理员可编辑）
_UI_EDITABLE_COLS = (
    "display_name",
    "category",
    "model_tier",
    "required_tools",
    "status",
    "scope",
)

_SYNC_MODES = ("insert_only", "upsert")


def _defn_to_insert_row(defn: SkillDefinition, tenant_id: str) -> dict:
    """SkillDefinition → 全字段 insert row（用于首次插入）。

    bundle_* 字段留给 SG-4 seed（pack + push OBS 后回填），SG-2 阶段允许为空。
    """
    status = defn.status if defn.status in SKILL_STATUS_VALUES else SKILL_STATUS_PUBLISHED
    return {
        "name": defn.name,
        "display_name": defn.display_name or defn.name,
        "description": defn.description,
        "version": defn.version,
        "category": defn.category or "general",
        "model_tier": defn.model_tier.value if hasattr(defn.model_tier, "value") else str(defn.model_tier),
        "required_tools": list(defn.required_tools or []),
        "pipeline_class": defn.pipeline_class,
        "status": status,
        "scope": SkillScope.SYSTEM.value,
        "is_builtin": True,
        "tenant_id": tenant_id,
        # bundle_* 由 SG-4 seed 阶段填充
        "bundle_uri": getattr(defn, "bundle_uri", "") or "",
        "bundle_hash": getattr(defn, "bundle_hash", "") or "",
        "bundle_size": getattr(defn, "bundle_size", 0) or 0,
        "bundle_manifest": getattr(defn, "bundle_manifest", {}) or {},
    }


class SkillService:
    def __init__(self, db: Session, redis_client=None):
        self.db = db
        self._redis = redis_client

    # ── 同步内置技能（启动 / 管理员触发）────────────────────────
    def sync_builtins(
        self,
        definitions: list[SkillDefinition],
        *,
        mode: str = "insert_only",
        tenant_id: str = DEFAULT_TENANT_ID,
        bundle_store: SkillBundleStore | None = None,
    ) -> dict[str, int]:
        """将 skill.md 派生的 SkillDefinition 同步到 DB。

        如果传了 ``bundle_store``：每个 skill 目录会 pack + push OBS，然后：
        - 首次插入：把 bundle_* 直接写进 skills 表 + 插入 SkillVersion (is_current)
        - 已存在且 bundle_hash 变了：更新 skills.bundle_* + 把老 SkillVersion 置 is_current=False + 插新版本
        - 已存在且 bundle_hash 没变：跳过（内容寻址，不动 DB）

        Returns: {inserted, updated, skipped, failed, bundles_pushed, bundles_unchanged}
        """
        if mode not in _SYNC_MODES:
            raise ValueError(f"Invalid mode '{mode}', must be one of {_SYNC_MODES}")

        stats = {
            "inserted": 0,
            "updated": 0,
            "skipped": 0,
            "failed": 0,
            "bundles_pushed": 0,
            "bundles_unchanged": 0,
        }
        if not definitions:
            logger.info("skill_sync_no_definitions")
            return stats

        for defn in definitions:
            try:
                # 步骤 1：如果 bundle_store 可用，先 pack + push OBS 拿到 hash
                if bundle_store is not None and defn.bundle_dir:
                    self._pack_and_push(defn, bundle_store, stats)

                existing = (
                    self.db.query(Skill)
                    .filter(Skill.tenant_id == tenant_id, Skill.name == defn.name)
                    .first()
                )
                if existing is None:
                    skill = self._insert_new(defn, tenant_id)
                    if defn.bundle_hash:
                        self._record_new_version(skill.id, defn, change_log="initial seed")
                    stats["inserted"] += 1
                elif mode == "insert_only":
                    # bundle_* 独立于 insert_only 语义：内容变了必须更新，否则运行时读旧 bundle
                    if defn.bundle_hash and existing.bundle_hash != defn.bundle_hash:
                        self._apply_bundle_change(existing, defn, change_log="bundle updated (insert_only mode)")
                        stats["updated"] += 1
                    else:
                        stats["skipped"] += 1
                else:  # upsert
                    self._update_md_derived(existing, defn)
                    if defn.bundle_hash and existing.bundle_hash != defn.bundle_hash:
                        self._apply_bundle_change(existing, defn, change_log="bundle updated (upsert mode)")
                    stats["updated"] += 1
            except IntegrityError as e:
                # 并发场景：另一个实例已插入 → 视为 skipped
                self.db.rollback()
                logger.warning("skill_sync_integrity_race", name=defn.name, error=str(e))
                stats["skipped"] += 1
            except Exception as e:
                self.db.rollback()
                logger.error("skill_sync_failed", name=defn.name, error=str(e))
                stats["failed"] += 1

        self.db.commit()
        logger.info("builtin_skills_synced", mode=mode, tenant=tenant_id, **stats)
        return stats

    def _pack_and_push(
        self,
        defn: SkillDefinition,
        store: SkillBundleStore,
        stats: dict[str, int],
    ) -> None:
        """Pack skill 目录 → push OBS → 回填 defn.bundle_*。幂等（同 hash 不重传）。"""
        from pathlib import Path

        bundle = store.pack(Path(defn.bundle_dir))
        # push 内部 HEAD 检查决定实际是否上传
        was_present_before = self._object_exists(store, bundle.object_key)
        uri = store.push(bundle)
        if was_present_before:
            stats["bundles_unchanged"] += 1
        else:
            stats["bundles_pushed"] += 1
        defn.bundle_uri = uri
        defn.bundle_hash = bundle.sha256
        defn.bundle_size = bundle.size
        defn.bundle_manifest = dict(bundle.manifest)

    @staticmethod
    def _object_exists(store: SkillBundleStore, key: str) -> bool:
        try:
            store._client.head_object(Bucket=store._bucket, Key=key)  # type: ignore[attr-defined]
            return True
        except Exception:
            return False

    def _record_new_version(self, skill_id: int, defn: SkillDefinition, *, change_log: str) -> None:
        sv = SkillVersion(
            skill_id=skill_id,
            version_num=defn.version or "1.0.0",
            bundle_uri=defn.bundle_uri,
            bundle_hash=defn.bundle_hash,
            bundle_size=defn.bundle_size,
            bundle_manifest=dict(defn.bundle_manifest or {}),
            is_current=True,
            change_log=change_log,
        )
        self.db.add(sv)
        self.db.flush()
        logger.info(
            "skill_version_recorded",
            skill_id=skill_id,
            version=sv.version_num,
            hash=sv.bundle_hash,
        )

    def _apply_bundle_change(self, skill: Skill, defn: SkillDefinition, *, change_log: str) -> None:
        """更新 skills 主表 bundle_* + 把旧 SkillVersion is_current=False + 插入新 SkillVersion。"""
        # 老版本降位
        self.db.query(SkillVersion).filter(
            SkillVersion.skill_id == skill.id,
            SkillVersion.is_current.is_(True),
        ).update({"is_current": False})
        # 主表 bundle_* 覆盖
        skill.bundle_uri = defn.bundle_uri
        skill.bundle_hash = defn.bundle_hash
        skill.bundle_size = defn.bundle_size
        skill.bundle_manifest = dict(defn.bundle_manifest or {})
        # 新版本行
        self._record_new_version(skill.id, defn, change_log=change_log)

    def _insert_new(self, defn: SkillDefinition, tenant_id: str) -> Skill:
        """首次插入 —— 全字段。bundle_* 由 SG-4 seed 填充。"""
        row = _defn_to_insert_row(defn, tenant_id)
        skill = Skill(**row)
        self.db.add(skill)
        self.db.flush()  # 拿 id
        logger.info(
            "skill_inserted",
            name=defn.name,
            id=skill.id,
            pipeline=bool(defn.pipeline_class),
            has_bundle=bool(row["bundle_hash"]),
        )
        return skill

    def _update_md_derived(self, skill: Skill, defn: SkillDefinition) -> None:
        """upsert 模式：只覆盖 MD 派生字段，UI 可编辑字段保留。"""
        changes = {}
        for col in _MD_DERIVED_COLS:
            new_val = getattr(defn, col, None)
            if new_val is None:
                continue
            if getattr(skill, col) != new_val:
                setattr(skill, col, new_val)
                changes[col] = True
        # bundle_* 走 SG-4 的 seed 逻辑，这里不复盖
        if changes:
            logger.info("skill_md_updated", name=defn.name, id=skill.id, cols=list(changes.keys()))
        self.db.flush()

    # ── Bundle 元数据回写（SG-4 seed / SG-5 admin 上传都走这个）────
    def update_bundle(
        self,
        skill_id: int,
        *,
        bundle_uri: str,
        bundle_hash: str,
        bundle_size: int,
        bundle_manifest: dict | None = None,
    ) -> Skill | None:
        skill = self.get_skill(skill_id)
        if skill is None:
            return None
        skill.bundle_uri = bundle_uri
        skill.bundle_hash = bundle_hash
        skill.bundle_size = bundle_size
        skill.bundle_manifest = bundle_manifest or {}
        self.db.flush()
        logger.info(
            "skill_bundle_updated",
            id=skill_id,
            name=skill.name,
            hash=bundle_hash,
            size=bundle_size,
        )
        return skill

    # ── 查询 ────────────────────────────────────────────────────
    def list_skills(
        self,
        *,
        user_id: str | None = None,
        user_roles: list[str] | None = None,
        scope: str | None = None,
        category: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[Skill], int]:
        query = self.db.query(Skill)

        if status is not None:
            query = query.filter(Skill.status == status)
        else:
            # 默认只显示 published
            query = query.filter(Skill.status == SKILL_STATUS_PUBLISHED)

        if scope:
            query = query.filter(Skill.scope == scope)
        elif user_id:
            query = query.filter(
                or_(
                    Skill.scope.in_([SkillScope.SYSTEM.value, SkillScope.PUBLIC.value]),
                    and_(Skill.scope == SkillScope.PRIVATE.value, Skill.created_by == user_id),
                )
            )

        if category:
            query = query.filter(Skill.category == category)

        skills = query.order_by(Skill.id).all()

        # 角色过滤（allowed_roles 为空=不限，非空=白名单）
        if user_roles is not None:
            role_set = set(user_roles)
            skills = [
                s for s in skills
                if not s.allowed_roles or set(s.allowed_roles) & role_set
            ]

        total = len(skills)
        start = (page - 1) * size
        return skills[start:start + size], total

    def get_skill(self, skill_id: int) -> Skill | None:
        return self.db.query(Skill).filter(Skill.id == skill_id).first()

    def get_skill_by_name(
        self, name: str, tenant_id: str = DEFAULT_TENANT_ID
    ) -> Skill | None:
        return (
            self.db.query(Skill)
            .filter(Skill.name == name, Skill.tenant_id == tenant_id)
            .first()
        )

    # ── CUD（管理员 / 用户）─────────────────────────────────────
    def create_skill(
        self,
        data: dict[str, Any],
        created_by: str | None = None,
        tenant_id: str = DEFAULT_TENANT_ID,
    ) -> Skill:
        """创建 skill 元数据行。bundle 内容通过 SG-5 admin bundle 上传 API 后续挂载。"""
        skill = Skill(
            name=data["name"],
            display_name=data.get("display_name") or data["name"],
            description=data.get("description", ""),
            version=data.get("version", "1.0.0"),
            category=data.get("category", "general"),
            model_tier=data.get("model_tier", "mid"),
            required_tools=data.get("required_tools", []),
            pipeline_class=data.get("pipeline_class", ""),
            status=data.get("status", SKILL_STATUS_PUBLISHED),
            scope=data.get("scope", SkillScope.PRIVATE.value),
            is_builtin=False,
            created_by=created_by,
            tenant_id=tenant_id,
            bundle_uri=data.get("bundle_uri", ""),
            bundle_hash=data.get("bundle_hash", ""),
            bundle_size=data.get("bundle_size", 0),
            bundle_manifest=data.get("bundle_manifest", {}),
        )
        self.db.add(skill)
        try:
            self.db.flush()
        except IntegrityError:
            self.db.rollback()
            raise ValueError(f"Skill name '{data['name']}' already exists in tenant '{tenant_id}'")
        self.db.refresh(skill)
        self.notify_change()
        return skill

    def update_skill(self, skill_id: int, data: dict[str, Any]) -> Skill | None:
        skill = self.get_skill(skill_id)
        if skill is None:
            return None

        updatable = (
            "display_name",
            "description",
            "version",
            "category",
            "model_tier",
            "required_tools",
            "pipeline_class",
            "status",
            "scope",
        )
        for field_name in updatable:
            if field_name in data:
                setattr(skill, field_name, data[field_name])

        self.db.flush()
        self.notify_change()
        return skill

    def delete_skill(self, skill_id: int) -> bool:
        skill = self.get_skill(skill_id)
        if skill is None:
            return False
        if skill.is_builtin:
            raise ValueError("Cannot delete built-in skill (use disable/archive instead)")
        self.db.delete(skill)
        self.db.flush()
        self.notify_change()
        return True

    # ── 广播 ────────────────────────────────────────────────────
    def notify_change(self) -> None:
        if self._redis is None:
            return
        try:
            self._redis.publish("lea:skill_reload", "changed")
            logger.debug("skill_change_broadcast_sent")
        except Exception as e:
            logger.warning("skill_change_broadcast_failed", error=str(e))

    # ── ORM → dataclass ─────────────────────────────────────────
    @staticmethod
    def to_definition(skill: Skill) -> SkillDefinition:
        try:
            model_tier = ModelTier(skill.model_tier or "mid")
        except ValueError:
            model_tier = ModelTier.MID

        try:
            scope = SkillScope(skill.scope or "system")
        except ValueError:
            scope = SkillScope.SYSTEM

        return SkillDefinition(
            id=skill.id,
            name=skill.name,
            description=skill.description or "",
            display_name=skill.display_name or skill.name,
            category=skill.category or "general",
            version=skill.version or "1.0.0",
            model_tier=model_tier,
            required_tools=list(skill.required_tools or []),
            status=skill.status or SKILL_STATUS_PUBLISHED,
            tenant_id=skill.tenant_id or DEFAULT_TENANT_ID,
            pipeline_class=skill.pipeline_class or "",
            scope=scope,
            is_builtin=skill.is_builtin,
            created_by=skill.created_by,
            # bundle 元数据从 DB 带回（SG-3 SkillLoader 用来 pull OBS 得 system_prompt / scripts / references）
            bundle_uri=skill.bundle_uri or "",
            bundle_hash=skill.bundle_hash or "",
            bundle_size=skill.bundle_size or 0,
            bundle_manifest=dict(skill.bundle_manifest or {}),
        )
