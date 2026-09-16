"""Skill 模型。

## 三层可见性

归属由 `(tenant_id, owner_scope, owner_user_id)` 三元组表达，跟 tools 表同一套编码：

    用户可见    (t1,      user,     alice)   只有 alice
    租户下可见  (t1,      platform, NULL)    t1 所有人
    所有人可见  (default, platform, NULL)    所有租户

「上架」就是改这两三列，由 skill_publish_requests 走审核流程（见 models/skill_publish.py）。

旧的 `scope` 列（private/system/public）由 migrations/014 删除 —— 它把归属和可见性
压在一列里，而且跟归属列在现网数据上是矛盾的（6 个平台级 skill 的 scope 是 private，
因为 upload 的默认参数没人传）。
"""

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, and_, func, or_

from lea_config.database import (
    DEFAULT_TENANT_ID,
    OWNER_SCOPE_PLATFORM,
    OWNER_SCOPE_USER,
    Base,
    OwnerMixin,
    TenantMixin,
)


class Skill(OwnerMixin, TenantMixin, Base):
    __tablename__ = "skills"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_skills_tenant_name"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, index=True)
    display_name = Column(String(200), default="")
    description = Column(Text, default="")
    version = Column(String(50), default="1.0.0")
    category = Column(String(50), nullable=False)
    system_prompt = Column(Text, default="")
    model_tier = Column(String(50), default="standard")
    required_tools = Column(JSON, default=list)
    # enabled 是**启用**，跟可见性是两回事：一个所有人可见的 skill 也可以被停用。
    #
    # 它取代了旧的 status 列（migrations/014 删掉）。那一列一个词兼三个意思 ——
    # 上架与否、启用与否、生命周期 —— 而 upload 写的 'active' 在三个语义里哪个都
    # 对不上，导致 6 个真 skill 全部 enabled=false、普通用户列表里一个都看不见。
    #
    # ⚠️ 停用要能断掉**已绑定**的 agent，所以 internal.py 那条下发查询也要过滤它。
    # 只在列表页过滤等于「停用了但还在用」。
    enabled = Column(Boolean, nullable=False, default=True, index=True)
    is_builtin = Column(Boolean, default=False, index=True)
    bundle_uri = Column(String(500), nullable=True)
    created_by = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


def visible_to(tenant_id: str, user_id: str | None):
    """"这个人能用哪些 skill"的三段式判据，返回一组 SQLAlchemy 过滤条件。

        所有人可见  (default, platform, NULL)
        租户下可见  (t1,      platform, NULL)
        用户可见    (t1,      user,     alice)

    跟 models/tool.py::visible_to 是同一个形状，故意各写一份而不是抽公共函数：两张表
    的列虽然同名，但条件里引用的是**各自的类属性**（Skill.owner_scope vs
    Tool.owner_scope），抽出来要么传类、要么上元编程，两种都比多写十行难读。

    ## 跟 api/skills.py::_visible_skills 的分工

    那个要 `Actor`，还额外处理两种管理员（平台管理员无约束、租户管理员看得到本租户
    别人的私有 skill —— 他要审核就得先看得见）。**管理面需要那些，下发链路不需要**：
    引擎按"这个用户能不能用"取 skill，管理员身份在这里不该放大可见范围，否则管理员
    的会话会挂上别人的私有 skill。

    所以这里只有三段式，不认管理员。user_id 为空时只返回平台级，是 fail-closed。
    """
    conds = [Skill.tenant_id.in_([tenant_id, DEFAULT_TENANT_ID])]
    if user_id:
        conds.append(
            or_(
                Skill.owner_scope == OWNER_SCOPE_PLATFORM,
                and_(
                    Skill.owner_scope == OWNER_SCOPE_USER,
                    Skill.owner_user_id == user_id,
                ),
            )
        )
    else:
        conds.append(Skill.owner_scope == OWNER_SCOPE_PLATFORM)
    return conds


class SkillVersion(Base):
    """不可变的 Skill 版本快照，类似 GitHub Release。"""

    __tablename__ = "skill_versions"
    __table_args__ = (
        UniqueConstraint("skill_id", "version_num", name="uq_skill_versions_skill_version"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    skill_id = Column(Integer, ForeignKey("skills.id", ondelete="CASCADE"), nullable=False, index=True)
    version_num = Column(String(50), nullable=False)
    snapshot = Column(JSON, nullable=False, default=dict)
    change_log = Column(Text, nullable=False, default="")
    bundle_uri = Column(String(500), nullable=True)
    sha256 = Column(String(64), nullable=True)
    size_bytes = Column(Integer, nullable=False, default=0)
    created_by = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=func.now())
