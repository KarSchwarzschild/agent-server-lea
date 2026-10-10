"""Agent 模型。"""

from sqlalchemy import Boolean, Column, DateTime, Integer, JSON, String, Text, UniqueConstraint, and_, func, or_

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
