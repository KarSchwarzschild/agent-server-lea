"""Skill 服务 — DB CRUD + 内置 Skill 同步。

分布式安全设计：
- sync_builtins: 用 DB 原生 upsert，多实例并发启动安全
- create_skill: 捕获 IntegrityError，友好提示重名
- CUD 操作后通过 notify_change() 触发 Redis Pub/Sub 广播
"""

from __future__ import annotations

from typing import Any

import structlog
from sqlalchemy import or_, and_, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from lea_graph.skills.schema import (
    ContextMode,
    ExecutionMode,
    ModelTier,
    SkillDefinition,
    SkillScope,
)
from lea_server.models.skill import Skill

logger = structlog.get_logger()

_UPSERT_COLUMNS = [
    "display_name", "description", "version", "category",
    "execution_mode", "pipeline_class", "context", "model_tier",
    "allowed_tools", "trigger_keywords", "system_prompt", "resources",
]


def _defn_to_row(defn: SkillDefinition) -> dict:
    return {
        "name": defn.name,
        "display_name": defn.display_name,
        "description": defn.description,
        "version": defn.version,
        "category": defn.category,
        "execution_mode": defn.execution_mode.value,
        "pipeline_class": defn.pipeline_class,
        "context": defn.context.value,
        "model_tier": defn.model_tier.value,
        "allowed_tools": defn.allowed_tools,
        "trigger_keywords": defn.trigger_keywords,
        "system_prompt": defn.system_prompt,
        "resources": defn.resources,
        "scope": SkillScope.SYSTEM.value,
        "is_builtin": True,
        "enabled": True,
    }


class SkillService:
    def __init__(self, db: Session, redis_client=None):
        self.db = db
        self._redis = redis_client

    def sync_builtins(self, definitions: list[SkillDefinition]) -> int:
        """同步内置 Skill（from skill.md）到数据库。

        使用 DB 原生 upsert（INSERT ... ON DUPLICATE KEY UPDATE / ON CONFLICT），
        多实例并发启动完全安全，无竞态条件。
        """
        if not definitions:
            return 0

        engine_name = self.db.bind.dialect.name if self.db.bind else ""
        count = 0

        for defn in definitions:
            row = _defn_to_row(defn)
            try:
                if engine_name == "mysql":
                    self._upsert_mysql(row)
                else:
                    self._upsert_sqlite(row)
                count += 1
            except Exception as e:
                logger.error("skill_upsert_failed", name=defn.name, error=str(e))

        self.db.flush()
        logger.info("builtin_skills_synced", count=count)
        return count

    def _upsert_mysql(self, row: dict) -> None:
        """MySQL: INSERT ... ON DUPLICATE KEY UPDATE。"""
        from sqlalchemy.dialects.mysql import insert
        stmt = insert(Skill).values(**row)
        update_dict = {col: stmt.inserted[col] for col in _UPSERT_COLUMNS}
        update_dict["is_builtin"] = True
        update_dict["scope"] = SkillScope.SYSTEM.value
        stmt = stmt.on_duplicate_key_update(**update_dict)
        self.db.execute(stmt)

    def _upsert_sqlite(self, row: dict) -> None:
        """SQLite: INSERT ... ON CONFLICT DO UPDATE。"""
        from sqlalchemy.dialects.sqlite import insert
        stmt = insert(Skill).values(**row)
        update_dict = {col: stmt.excluded[col] for col in _UPSERT_COLUMNS}
        update_dict["is_builtin"] = True
        update_dict["scope"] = SkillScope.SYSTEM.value
        stmt = stmt.on_conflict_do_update(index_elements=["name"], set_=update_dict)
        self.db.execute(stmt)

    def list_skills(
        self,
        *,
        user_id: str | None = None,
        scope: str | None = None,
        category: str | None = None,
        enabled_only: bool = True,
        page: int = 1,
        size: int = 50,
    ) -> tuple[list[Skill], int]:
        """分页查询 Skill。"""
        query = self.db.query(Skill)

        if enabled_only:
            query = query.filter(Skill.enabled == True)  # noqa: E712

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

        total = query.count()
        skills = query.order_by(Skill.id).offset((page - 1) * size).limit(size).all()
        return skills, total

    def get_skill(self, skill_id: int) -> Skill | None:
        return self.db.query(Skill).filter(Skill.id == skill_id).first()

    def get_skill_by_name(self, name: str) -> Skill | None:
        return self.db.query(Skill).filter(Skill.name == name).first()

    def create_skill(self, data: dict[str, Any], created_by: str | None = None) -> Skill:
        skill = Skill(
            name=data["name"],
            display_name=data["display_name"],
            description=data.get("description", ""),
            version=data.get("version", "1.0.0"),
            category=data.get("category", "general"),
            execution_mode=data.get("execution_mode", "react"),
            pipeline_class=data.get("pipeline_class", ""),
            context=data.get("context", "fork"),
            model_tier=data.get("model_tier", "mid"),
            allowed_tools=data.get("allowed_tools", []),
            trigger_keywords=data.get("trigger_keywords", []),
            system_prompt=data.get("system_prompt", ""),
            resources=data.get("resources", {}),
            scope=data.get("scope", "private"),
            is_builtin=False,
            enabled=True,
            created_by=created_by,
        )
        self.db.add(skill)
        try:
            self.db.flush()
        except IntegrityError:
            self.db.rollback()
            raise ValueError(f"Skill name '{data['name']}' already exists")
        self.db.refresh(skill)
        self.notify_change()
        return skill

    def update_skill(self, skill_id: int, data: dict[str, Any]) -> Skill | None:
        skill = self.get_skill(skill_id)
        if skill is None:
            return None

        updatable = [
            "display_name", "description", "version", "category",
            "execution_mode", "pipeline_class", "context", "model_tier",
            "allowed_tools", "trigger_keywords", "system_prompt",
            "resources", "scope", "enabled",
        ]
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
            raise ValueError("Cannot delete built-in skill")
        self.db.delete(skill)
        self.db.flush()
        self.notify_change()
        return True

    def notify_change(self) -> None:
        """通过 Redis Pub/Sub 通知所有实例刷新 Skill 缓存。"""
        if self._redis is None:
            return
        try:
            self._redis.publish("lea:skill_reload", "changed")
            logger.debug("skill_change_broadcast_sent")
        except Exception as e:
            logger.warning("skill_change_broadcast_failed", error=str(e))

    @staticmethod
    def to_definition(skill: Skill) -> SkillDefinition:
        """ORM → dataclass。"""
        try:
            model_tier = ModelTier(skill.model_tier)
        except ValueError:
            model_tier = ModelTier.MID

        try:
            context = ContextMode(skill.context)
        except ValueError:
            context = ContextMode.FORK

        try:
            execution_mode = ExecutionMode(skill.execution_mode)
        except ValueError:
            execution_mode = ExecutionMode.REACT

        try:
            scope = SkillScope(skill.scope)
        except ValueError:
            scope = SkillScope.SYSTEM

        return SkillDefinition(
            name=skill.name,
            display_name=skill.display_name,
            description=skill.description or "",
            category=skill.category or "general",
            trigger_keywords=skill.trigger_keywords or [],
            model_tier=model_tier,
            context=context,
            allowed_tools=skill.allowed_tools or [],
            system_prompt=skill.system_prompt or "",
            enabled=skill.enabled,
            id=skill.id,
            version=skill.version or "1.0.0",
            execution_mode=execution_mode,
            pipeline_class=skill.pipeline_class or "",
            scope=scope,
            is_builtin=skill.is_builtin,
            created_by=skill.created_by,
            resources=skill.resources or {},
        )
