"""Tool Gateway 数据模型。"""

from datetime import datetime

from sqlalchemy import (
    Boolean, Column, DateTime, Enum, ForeignKey, Integer,
    String, Text, JSON,
)

from lea_tools.db import Base


# 平台级工具（seed.py 里的 BUILTIN_TOOLS）住在这个租户里，**所有租户都能用**。
#
# 为什么复用 "default" 而不是一个专门的哨兵（比如 "__platform__"）：库里现有的内置
# 工具行就是 tenant_id='default'，换哨兵要重新种一遍，还要跟 lea-config 的
# api/tools.py:56（它把 tenant_id 硬编码成 DEFAULT_TENANT_ID）对齐。语义上专门的
# 哨兵更干净 —— "default" 同时还是身份缺失时的兜底租户，两个含义压在一个值上 ——
# 但那是 #20 收拢路由时一起改的事，不值得夹在这个隔离修复里。
#
# 后果要知道：**一个真的叫 "default" 的租户没法拥有私有工具**（它的工具对所有人可见）。
GLOBAL_TENANT_ID = "default"

# 归属的两个取值。**故意在这里重新定义一遍，不从 lea-config 导** —— lea-tools 是执行
# 侧，它不依赖配置服务的包（两个服务分开部署）。值必须跟 lea_config.database 和
# lea_authkit.authz 里的字面量一致，因为三处映射的是同一列。
OWNER_SCOPE_PLATFORM = "platform"
OWNER_SCOPE_USER = "user"


class Tool(Base):
    __tablename__ = "tools"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # ⚠️ tenant_id 以前**不在这个模型里** —— 那才是"四处按 name 查工具不带 tenant"的
    # 根子：模型压根表达不了租户，所以那些查询不是忘了过滤，是没法过滤。
    #
    # 而 name 以前是 unique=True，那正是真实表上那个全局 UNIQUE(name) 索引的出处
    # （lea-config 的模型声明的是 UniqueConstraint(tenant_id, name)，两个服务的
    # create_all() 先后打在同一张表上，于是两套约束都攒下了）。
    #
    # 全局唯一的后果：租户 A 建了 web_search，别人永远不能有同名工具。
    # 去掉它由 migrations/005 完成 —— **必须在下面那些查询加上租户过滤之后**，
    # 否则 .first() 会随机返回某个租户的工具配置（里面有 endpoint），
    # 从一个显式报错退化成静默的跨租户执行。
    tenant_id = Column(String(50), nullable=False, default=GLOBAL_TENANT_ID, index=True)
    name = Column(String(100), nullable=False, index=True)

    # ⚠️ 归属三列在真实表里一直都有（lea-config 的 OwnerMixin 建的），这个模型以前
    # **不映射它们** —— 于是执行侧只能按 (租户, 名字) 找工具，表达不了"这是谁的工具"。
    # 工具名改成用户内唯一之后（migrations/007）那就不够了：同一个租户里 alice 和 bob
    # 可以各有一个 weather，不带 user_id 的查询会在两行里随便挑一行。
    #
    # is_builtin 同理：它标的是"实现在代码里"（seed.py 的 BUILTIN_TOOLS），表里那行只是
    # 给引擎看的元数据。不映射的话接口层没法拦住"改内置工具"。
    owner_scope = Column(String(20), nullable=False, default=OWNER_SCOPE_PLATFORM, index=True)
    owner_user_id = Column(String(64), nullable=True, index=True)
    is_builtin = Column(Boolean, nullable=False, default=False, index=True)
    display_name = Column(String(200))
    description = Column(Text)
    category = Column(String(50))
    execution_type = Column(
        Enum("local", "sandbox", "api", "internal", "mcp", name="exec_type_enum"),
        nullable=False,
    )
    schema_json = Column(JSON, nullable=False)
    endpoint = Column(String(500))
    sandbox_config = Column(JSON)
    http_adapter_config = Column(JSON)
    mcp_server_id = Column(Integer, ForeignKey("mcp_servers.id"), nullable=True)
    supports_local = Column(Boolean, default=False)
    supports_cloud = Column(Boolean, default=True)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class ToolPermission(Base):
    __tablename__ = "tool_permissions"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(String(100), nullable=False)
    role = Column(String(50), default="*")
    agent_id = Column(Integer, nullable=True)
    tool_id = Column(Integer, ForeignKey("tools.id"), nullable=False)
    allowed = Column(Boolean, default=True)
    rate_limit = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)


class ToolCredential(Base):
    __tablename__ = "tool_credentials"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(String(100), nullable=False)
    tool_id = Column(Integer, ForeignKey("tools.id"), nullable=False)
    provider = Column(String(100))
    api_key_enc = Column(String(500), nullable=False)
    api_base = Column(String(500))
    extra_config = Column(JSON)
    quota_limit = Column(Integer, default=0)
    quota_used = Column(Integer, default=0)
    expires_at = Column(DateTime, nullable=True)
    is_platform_default = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class MCPServer(Base):
    """执行侧读的 MCP server 行。**这张表归 lea-config**，这里只读。

    ⚠️ 这个模型以前跟 lea-config 那份是**两套词汇**：那边写 `config` (JSON)，这边读
    `endpoint` / `command` / `env_config`。`create_all()` 只建表不改表，于是真实表成了
    两份的并集，而经 lea-config 建出来的行 `endpoint` 永远是 NULL —— sync 和调用两条
    路一起断，MCP 工具行至今是 0。跟 `tools` 表那次是同一个病的第二例。

    migrations/016 收口到 `endpoint`（判据同 migrations/006：运行时决定能不能调通的
    那一侧，命名是权威的），并删掉 config / command / env_config 三列。

    `enabled` 在这里的含义要注意：migrations/016 把所有搬不过来的行（stdio、或没有
    url）都置成了 0，所以「disabled」不只表示人工停用，也表示「这行是历史遗留」。
    两种都不该被调用，所以行为一致。
    """

    __tablename__ = "mcp_servers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    tenant_id = Column(String(100), nullable=False)
    name = Column(String(200), nullable=False)
    # ⚠️ 这里是 String 而不是 Enum，跟 lea-config 那份模型一致，**是有意的**。
    #
    # 库里的 ENUM（migrations/016 收成一个值）管住写入就够了。Python 层再套一个
    # SQLAlchemy Enum 只多一个后果：**读到历史行时直接抛 LookupError**。而那不是
    # 假设——现网 22/24 两行就是 stdio，迁移跑之前 db.get() 它们会炸成 500，而不是
    # 返回一句"这个传输方式不再支持"。
    #
    # 实测过（迁移前）：
    #     server 22 → LookupError: 'stdio' is not among the defined enum values
    #     server 23 → 正常读出
    #
    # 也就是说 Enum 版本给代码和迁移之间焊了一个部署顺序依赖：必须先跑迁移再上线，
    # 顺序反了就是 500。而 String 版本两个顺序都安全 —— 读得出来，然后被
    # check_transport 明确拒绝。
    #
    # 附带的好处：check_transport 这才真正**可达**。Enum 版本下它永远打不到，
    # 是一段看起来在防守、实际到不了的代码。
    transport = Column(String(20), nullable=False, default="streamable_http")
    endpoint = Column(String(500))
    auth_config = Column(JSON)
    tool_filter = Column(JSON)
    enabled = Column(Boolean, default=True)
    # 连通/同步状态。以前这三列在 lea-config 那份模型里、而且没有任何代码写过；
    # 现在由本服务的 /admin/mcp-servers/{id}/{test,sync} 写 —— 连远端的是这一侧。
    connection_status = Column(String(20), default="unknown")
    last_connected_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)
    last_sync_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
