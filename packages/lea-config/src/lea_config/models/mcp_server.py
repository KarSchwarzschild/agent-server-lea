"""MCP Server 模型 —— 一个远端 MCP 服务的连接配置 + 归属。

## 这个文件为什么被重写过

跟 `models/tool.py` 是同一个病的第二例：这张表被 lea-config 和 lea-tools **各定义
一遍**，`create_all()` 只建表不改表，于是真实表成了两份模型的并集，还带着两组表达
同一件事的列：

    这个模型以前**写**的          执行侧（lea-tools）**读**的
    ──────────────────          ──────────────────────────
    config (JSON)                endpoint / command / env_config
                                 auth_config / tool_filter

后果是端到端不通：通过这里建出来的行，`endpoint` 永远是 NULL，而 `/sync` 和
`mcp_call` 读的都是它。现网 4 行全是这个状态，所以 MCP 工具行至今是 0 —— 同步从来
没成功过一次。

修法跟 tools 表那次一致：**统一到执行器读的那套词汇**。判据是运行时——真正决定一个
MCP server 能不能被调通的是 lea-tools 的查询，那它的命名就是权威的。死列由
migrations/016 删掉。

## 只支持 streamable_http

`stdio` 的语义是在网关进程里 fork 子进程，而它以前还是**默认值**。今天没实现所以是
死值；一旦实现，「用户能加 MCP」就等于「用户能在网关进程里执行任意命令」。

`sse` 是长连接，绑在某一个副本上。所有服务都是多副本，下一次工具调用可能落到别的
副本，那边没有这条连接。要支持得引入会话亲和或连接注册表，为一个没人要的传输方式
不值。

`streamable_http` 是请求-响应，无状态，天然多副本安全。

## 一个 server → N 个工具

这张表**不是工具表**。一个 server 连上去问 `tools/list`，答回来几个，才变成几个模型
看得见的工具（`tools` 表里 `execution_type='mcp'` 的行，靠 `mcp_server_id` 指回来）。
模型从头到尾不知道 MCP 是什么，它只看到一串工具。

删 server 时要连带删那些工具行 —— 见 `api/mcp_servers.py` 的 delete。
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    JSON,
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
    OWNER_SCOPE_VALUES,
    Base,
    OwnerMixin,
    TenantMixin,
)


# 只剩一个取值。保留这个元组是因为接口层的校验和错误文案都基于它，而且以后真要加
# 传输方式时，加在这里比在 if 里加一个 or 更难漏。
MCP_TRANSPORT_STREAMABLE_HTTP = "streamable_http"
MCP_TRANSPORT_VALUES = (MCP_TRANSPORT_STREAMABLE_HTTP,)

# 这三个名字是本表私有的历史命名，现在只是共享常量的别名 —— 归属模型统一到
# database.py 的 OWNER_SCOPE_*（本表就是那套设计的来源，见它的 docstring）。
MCP_SCOPE_PLATFORM = OWNER_SCOPE_PLATFORM
MCP_SCOPE_USER = OWNER_SCOPE_USER
MCP_SCOPE_VALUES = OWNER_SCOPE_VALUES

MCP_STATUS_UNKNOWN = "unknown"
MCP_STATUS_CONNECTED = "connected"
MCP_STATUS_ERROR = "error"
MCP_STATUS_VALUES = (MCP_STATUS_UNKNOWN, MCP_STATUS_CONNECTED, MCP_STATUS_ERROR)


class MCPServer(OwnerMixin, TenantMixin, Base):
    __tablename__ = "mcp_servers"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "owner_scope", "owner_user_id", "name",
            name="uq_mcp_servers_tenant_owner_name",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False, index=True)
    description = Column(Text, default="")

    # owner_scope / owner_user_id 来自 OwnerMixin，跟 tools / skills 是同一套三层
    # 可见性编码：
    #     (default, platform, NULL)   所有租户可见
    #     (t1,      platform, NULL)   t1 内共享
    #     (t1,      user,     alice)  alice 私有
    # 接口层今天还硬过滤 platform（用户私有是 P2），但表已经能表达了。

    transport = Column(
        String(20), nullable=False, default=MCP_TRANSPORT_STREAMABLE_HTTP
    )

    # ⚠️ endpoint 是连接配置的**唯一真源**。别再加一个 JSON 口袋去装 url ——
    # 那正是这个文件开头说的那个病。自定义 header 走 auth_config。
    endpoint = Column(String(500), nullable=True)

    # auth_config 是**发给远端的凭证**，执行侧 mcp_proxy._headers() 读它：
    #     {"bearer_token": "..."}  → Authorization: Bearer ...
    #     {"api_key": "..."}       → X-API-Key: ...
    #
    # ⚠️ 明文 JSON，而且是行级共享（一行 server 一份）。这对「租户自己配的 server」
    # 够用，对「市场里全租户共用的 server」不够 —— 那种要每人一把 key，属于 P1 的
    # mcp_credentials。在那之前不要把市场级 server 的 key 往这里塞。
    auth_config = Column(JSON, nullable=True)

    # tool_filter 是**我们的策略**（只暴露这个 server 的哪几个工具），不是 MCP 客户端
    # 配置的一部分 —— 所以它是独立列，不混进别的 JSON 里。
    # 空/NULL = 全要。sync 时生效，见 lea-tools 的 admin_mcp.py。
    tool_filter = Column(JSON, nullable=True)

    enabled = Column(Boolean, default=True, index=True)

    # 这三列以前是**死列**：只在响应里被读出来，没有任何代码写过它们。现在由
    # lea-tools 的 /admin/mcp-servers/{id}/test 和 /sync 写 —— 那边才有 MCP 客户端。
    # （P1 把 sync 搬到 lea-config 之后，写的人跟着搬。）
    connection_status = Column(String(20), default=MCP_STATUS_UNKNOWN)
    last_connected_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)

    last_sync_at = Column(DateTime, nullable=True)

    created_by = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


def visible_to(tenant_id: str, user_id: str | None):
    """"这个人能用哪些 MCP server"的三段式判据，返回一组 SQLAlchemy 过滤条件。

        所有人可见  (default, platform, NULL)
        租户下可见  (t1,      platform, NULL)
        用户可见    (t1,      user,     alice)

    跟 models/tool.py / models/skill.py 里那两份同形状，故意各写一份：条件里引用的是
    各自的类属性（MCPServer.owner_scope vs Tool.owner_scope），抽公共函数要么传类、
    要么上元编程，两种都比多写十行难读。

    user_id 为空时只返回平台级，是 fail-closed。
    """
    conds = [MCPServer.tenant_id.in_([tenant_id, DEFAULT_TENANT_ID])]
    if user_id:
        conds.append(
            or_(
                MCPServer.owner_scope == OWNER_SCOPE_PLATFORM,
                and_(
                    MCPServer.owner_scope == OWNER_SCOPE_USER,
                    MCPServer.owner_user_id == user_id,
                ),
            )
        )
    else:
        conds.append(MCPServer.owner_scope == OWNER_SCOPE_PLATFORM)
    return conds
