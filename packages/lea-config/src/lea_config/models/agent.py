"""Agent 模型。"""

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    and_,
    func,
    or_,
)

from lea_config.database import (
    DEFAULT_TENANT_ID,
    OWNER_SCOPE_PLATFORM,
    OWNER_SCOPE_USER,
    Base,
    OwnerMixin,
    TenantMixin,
)


class Agent(OwnerMixin, TenantMixin, Base):
    __tablename__ = "agents"
    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_agents_tenant_name"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, index=True)
    description = Column(Text, default="")

    avatar = Column(String(200), default="")
    welcome_message = Column(Text, default="")
    soul_prompt = Column(Text, default="")
    category = Column(String(50), default="general")
    example_questions = Column(JSON, default=list)
    sort_order = Column(Integer, default=0)

    model = Column(String(100), default="")
    temperature = Column(Integer, default=7)
    # 100：一次 ReAct 迭代 = 一次模型往返，多文件任务每写一个文件吃一轮，20 会把
    # "生成一套 PPT"这类任务在收尾处截断。跟 api/agents.py 的两处默认值、以及
    # lea-agent 的 config_loader.go 兜底值保持一致（那边有完整说明）。
    max_turns = Column(Integer, default=100)
    max_tokens = Column(Integer, default=4096)
    kb_mode = Column(String(20), default="tool")
    memory_mode = Column(String(20), default="always")
    timeout_seconds = Column(Integer, default=120)

    is_system = Column(Boolean, default=False, index=True)
    enabled = Column(Boolean, default=True, index=True)
    created_by = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


# 快照覆盖的字段 = AgentUpdate 允许改的那些。单独拎出来是因为三处要用同一份清单
# （写版本、回滚、migration 037 的基线），各写一遍迟早漂移：漏一个字段的后果是
# 它永远不进版本、回滚时被静默保留成当前值，而这件事不会报错。
#
# 刻意**不含** id / tenant_id / owner_* / is_system / created_* —— 那些是身份和
# 归属，不是配置。回滚一次配置不该把 agent 换个主人。
VERSIONED_FIELDS = (
    "name",
    "description",
    "avatar",
    "welcome_message",
    "soul_prompt",
    "category",
    "example_questions",
    "sort_order",
    "model",
    "temperature",
    "max_turns",
    "max_tokens",
    "kb_mode",
    "memory_mode",
    "timeout_seconds",
    "enabled",
)


def agent_snapshot(agent: "Agent") -> dict:
    """按 VERSIONED_FIELDS 取一份配置快照。"""
    return {f: getattr(agent, f) for f in VERSIONED_FIELDS}


class AgentVersion(Base):
    """不可变的 Agent 配置快照。

    追加式：当前状态也在表里（revision 最大的那条）。"回滚到 N"＝读 N 的快照
    写回 agents，并**再追加一条新 revision**，历史永不改写。
    """

    __tablename__ = "agent_versions"
    __table_args__ = (
        UniqueConstraint("agent_id", "revision", name="uq_agent_versions_agent_rev"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    agent_id = Column(
        Integer, ForeignKey("agents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    revision = Column(Integer, nullable=False)
    snapshot = Column(JSON, nullable=False, default=dict)
    # create / update / rollback / baseline
    change_kind = Column(String(20), nullable=False, default="update")
    change_note = Column(String(500), nullable=False, default="")
    created_by = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=func.now())


def visible_to(tenant_id: str, user_id: str | None):
    """"这个人能看到哪些 agent"的三段式判据，返回一组 SQLAlchemy 过滤条件。

        全局平台专家   (default, platform, NULL)   谁都能看（跨租户）
        本租户共享的   (t1,      platform, NULL)   t1 的人能看
        自己私有的     (t1,      user,     alice)  只有 alice 能看

    ## 为什么单独写这个，而不是继续用 authkit 的 scoped_filter

    scoped_filter 把查询硬夹在 `tenant_id == actor.tenant_id`，**没有 default 全局逃逸**——
    它是给"严格租户隔离"的资源（如 KB）设计的。而 agent 跟 tool/skill/mcp_server 一样需要
    "全局平台层"：官方出的公共专家/专家团要让所有租户都能召唤。tool/skill/mcp 早就各有一份
    带 `tenant_id IN (mine, default)` 的 visible_to（见 models/tool.py），agent 当初图省事套了
    通用的 scoped_filter，于是成了唯一没有全局层的资源——放在 (default, platform) 的专家对别的
    租户不可见。这个函数把 agent 补齐到跟它们一致。

    这条规则只管**读**（能不能看到/召唤）。**改**的归属另有 _require_mine 把关（只能改自己建的），
    全局专家对别的租户是只读的。

    user_id 为空时只返回平台层，fail-closed（跟 tool.py 同理）。
    """
    conds = [
        Agent.tenant_id.in_([tenant_id, DEFAULT_TENANT_ID]),
    ]
    if user_id:
        conds.append(
            or_(
                Agent.owner_scope == OWNER_SCOPE_PLATFORM,
                and_(
                    Agent.owner_scope == OWNER_SCOPE_USER,
                    Agent.owner_user_id == user_id,
                ),
            )
        )
    else:
        conds.append(Agent.owner_scope == OWNER_SCOPE_PLATFORM)
    return conds
