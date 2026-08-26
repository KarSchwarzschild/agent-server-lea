"""Skill 上架申请。

## 为什么单独一张表

「上架」是一个**请求改变可见性**的动作，而 skill 行上的归属三元组表达的是「现在是
什么」。把申请状态挂到 skill 行上（加 review_status / target_scope / reviewed_by
几列）会让一行同时说两件事，而这个库已经被「一张表两种含义」坑过两次（tools 和
mcp_servers）。

更实际的理由是**审核有历史价值**：「上次申请被驳回过，理由是 X」在第二次申请时仍然
有用。挂在 skill 行上的话第二次申请会把第一次的记录冲掉。

## 目标层的词汇

`target_scope` 用 tenant / global，**刻意不复用 owner_scope 的 platform / user**：
那两个值区分不了「租户内共享」和「全平台共享」——它们的 owner_scope 都是 platform，
差别在 tenant_id 是不是 'default'。

    target='tenant'  →  (原 tenant_id, platform, NULL)
    target='global'  →  ('default',    platform, NULL)

## 审核人

按目标层分，两个角色各管一段：

    用户私有 → 租户下可见    租户管理员（is_tenant_admin）
    租户下   → 所有人可见    平台管理员（is_platform_admin）
"""

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)

from lea_config.database import Base

# 目标层
PUBLISH_TARGET_TENANT = "tenant"  # 租户内所有人可见
PUBLISH_TARGET_GLOBAL = "global"  # 所有租户可见
PUBLISH_TARGETS = (PUBLISH_TARGET_TENANT, PUBLISH_TARGET_GLOBAL)

# 申请状态
PUBLISH_PENDING = "pending"
PUBLISH_APPROVED = "approved"
PUBLISH_REJECTED = "rejected"


class SkillPublishRequest(Base):
    __tablename__ = "skill_publish_requests"
    __table_args__ = (
        # 待审列表的主查询：某租户下所有 pending。
        Index("idx_spr_pending", "tenant_id", "status"),
        # 「这个 skill 有没有在审的申请」—— 提交前的重复检查走它。
        Index("idx_spr_skill", "skill_id", "status"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)

    # ON DELETE CASCADE：skill 删了申请跟着删。不级联的话会留下一批指向不存在
    # skill 的申请，待审列表里点开就 404 —— tools 那边踩过同一个坑。
    skill_id = Column(
        Integer, ForeignKey("skills.id", ondelete="CASCADE"), nullable=False
    )

    # 冗余存一份租户，为的是「列出本租户的待审申请」不用 JOIN skills。
    # 申请归属跟着 skill 走，而 skill 删了申请级联删，所以不会漂。
    tenant_id = Column(String(64), nullable=False)

    requested_by = Column(String(100), nullable=False)
    target_scope = Column(String(20), nullable=False)
    status = Column(String(20), nullable=False, default=PUBLISH_PENDING)

    review_note = Column(Text, nullable=True)
    reviewed_by = Column(String(100), nullable=True)
    reviewed_at = Column(DateTime, nullable=True)

    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
