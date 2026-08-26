"""Skill 数据库模型 — 平台级技能定义持久化。

契约：SKILL.md 遵循 Anthropic Skills 标准（仅 name/description/license/metadata 4 顶级键）；
所有平台字段（display_name/model_tier/required_tools/pipeline_class/status 等）落 DB。
Skill 内容（skill.md body + references/ + scripts/）以 tar.gz bundle 形式存 OBS，
DB 只保留 bundle_uri / bundle_hash / bundle_size / bundle_manifest 元数据。
"""

from __future__ import annotations

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, func

from lea_server.core.tenant import TenantMixin
from lea_server.database import Base


# status 枚举取值（应用层校验，不用 SA Enum type 以便迁移灵活）
SKILL_STATUS_DRAFT = "draft"
SKILL_STATUS_PUBLISHED = "published"
SKILL_STATUS_DISABLED = "disabled"
SKILL_STATUS_ARCHIVED = "archived"
SKILL_STATUS_VALUES = (SKILL_STATUS_DRAFT, SKILL_STATUS_PUBLISHED, SKILL_STATUS_DISABLED, SKILL_STATUS_ARCHIVED)


class Skill(TenantMixin, Base):
    """Skill 定义 —— 可复用能力单元。

    Anthropic Skills 标准：skill.md 顶层 4 keys + body 作为 system prompt。
    Triggers 从 description 里的 "Triggers: ..." 段解析（不单独存字段）。
    skill 内容（md + references/ + scripts/）打包成 tar.gz 存 OBS，
    DB 只保留 bundle 元数据（uri/hash/size/manifest），运行时按 hash 从 OBS 拉。
    """

    __tablename__ = "skills"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_skills_tenant_name"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, index=True)  # 全局非 unique；组合 UNIQUE(tenant_id, name)
    display_name = Column(String(200), nullable=False)
    description = Column(Text, default="")  # 含 "Triggers: ..." 段，运行时解析
    version = Column(String(20), default="1.0.0")
    category = Column(String(50), default="general", index=True)

    # ── 运行时字段 ─────────────────────────────────────────────
    model_tier = Column(String(20), default="mid")
    required_tools = Column(JSON, default=list)  # 依赖声明（挂载到 agent 时校验），非权限白名单
    pipeline_class = Column(String(200), default="")  # 仅内置 skill 用；从 pipeline_registry 注入

    # ── OBS Bundle 元数据（Anthropic bundle 是运行时唯一真源）──
    bundle_uri = Column(String(500), default="")     # s3://bucket/skills/{sha256}.tar.gz
    bundle_hash = Column(String(64), default="", index=True)  # sha256 (hex, 64 chars) — 内容寻址
    bundle_size = Column(Integer, default=0)         # tarball 字节数
    bundle_manifest = Column(JSON, default=dict)     # {rel_path: file_sha256}，供审计/diff

    # ── 生命周期 / 权限 ───────────────────────────────────────
    status = Column(String(20), default=SKILL_STATUS_DRAFT, index=True)
    scope = Column(String(20), default="private", index=True)
    is_builtin = Column(Boolean, default=False, index=True)
    allowed_roles = Column(JSON, default=list)  # 空=不限，["finance","hr"]=白名单
    created_by = Column(String(100), nullable=True)

    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    def __repr__(self) -> str:
        return f"<Skill {self.name} tenant={self.tenant_id} status={self.status} builtin={self.is_builtin}>"


class SkillVersion(Base):
    """Skill 版本历史 —— 每次 bundle 发布留一条，支持回滚。

    is_current=True 的版本对应 skills.bundle_* 当前指向；一个 skill 只有一条 is_current。
    回滚 = 把某个历史版本的 is_current 置 True，同时更新 skills 表的 bundle_*。
    """

    __tablename__ = "skill_versions"
    __table_args__ = (
        UniqueConstraint("skill_id", "version_num", name="uq_skill_versions_skill_ver"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    skill_id = Column(Integer, ForeignKey("skills.id", ondelete="CASCADE"), nullable=False, index=True)
    version_num = Column(String(20), nullable=False)

    # ── Bundle 快照（跟 Skill 主表一样的 4 元组）───────────────
    bundle_uri = Column(String(500), nullable=False)
    bundle_hash = Column(String(64), nullable=False, index=True)
    bundle_size = Column(Integer, default=0)
    bundle_manifest = Column(JSON, default=dict)

    is_current = Column(Boolean, default=False, index=True)
    change_log = Column(Text, default="")

    created_by = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=func.now())

    def __repr__(self) -> str:
        return f"<SkillVersion skill={self.skill_id} v={self.version_num} current={self.is_current}>"


class SkillTestRun(Base):
    """Skill 测试运行记录（骨架表，Ph1 建表不写代码，Ph1 后段激活）。"""

    __tablename__ = "skill_test_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    skill_id = Column(Integer, ForeignKey("skills.id", ondelete="CASCADE"), nullable=False, index=True)
    version_num = Column(String(20), nullable=True)

    input = Column(JSON, default=dict)     # 测试输入（user message + context）
    output = Column(JSON, default=dict)    # 输出（final assistant message）
    tool_calls = Column(JSON, default=list)  # 工具调用序列

    status = Column(String(20), default="success", index=True)  # success | error | timeout
    duration_ms = Column(Integer, default=0)
    error_message = Column(Text, default="")

    created_by = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=func.now())

    def __repr__(self) -> str:
        return f"<SkillTestRun skill={self.skill_id} status={self.status} {self.duration_ms}ms>"


class SkillInvocation(Base):
    """Skill 调用记录 —— 每次 skill 被执行都写一条，供使用统计聚合。"""

    __tablename__ = "skill_invocations"

    id = Column(Integer, primary_key=True, autoincrement=True)
    skill_id = Column(Integer, ForeignKey("skills.id", ondelete="SET NULL"), nullable=True, index=True)
    skill_name = Column(String(100), nullable=False, index=True)
    user_id = Column(String(100), nullable=True, index=True)
    session_id = Column(String(64), nullable=True)
    source = Column(String(20), nullable=False, index=True)  # chat | test | supervisor
    status = Column(String(20), default="success", index=True)  # success | error
    input_tokens = Column(Integer, default=0)
    output_tokens = Column(Integer, default=0)
    duration_ms = Column(Integer, default=0)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=func.now(), index=True)

    def __repr__(self) -> str:
        return f"<SkillInvocation skill={self.skill_name} source={self.source} status={self.status}>"
