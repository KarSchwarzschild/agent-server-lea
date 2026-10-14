"""Tool 模型。

## 为什么这个文件之前是坏的

`tools` 表被**三个服务各定义一遍**，而 `create_all()` 只建表不改表 —— 于是两个服务
先后打在同一张表上，真实表变成了两份模型的**并集**，还带着两组表达同一件事的列：

    执行器真正读的（lea-tools 的词汇）     lea-config 以前写的（死列）
    ────────────────────────────────     ──────────────────────────
    enabled                              is_enabled
    schema_json      NOT NULL            input_schema
    execution_type   NOT NULL            （没有）
    endpoint / sandbox_config /          source / is_builtin
    http_adapter_config / mcp_server_id
    supports_local / supports_cloud

后果有两层，第二层比第一层糟：

  1. `POST /tools` 直接 500 —— `schema_json` 和 `execution_type` 是 NOT NULL 无默认，
     而这个模型压根没有这两列，INSERT 缺列。
  2. **就算把 500 修好也是错的。** lea-config 写 is_enabled/input_schema，执行器读
     enabled/schema_json（services/__init__.py:29、services/registry.py:14,37）——
     建出来的行存得下、列得出，一执行就找不到 schema。那是个静默的坏，比 500 难查。

所以修法不是"给这个模型补 9 列"，而是**统一到执行器读的那套词汇**。判据是运行时：
真正决定一个工具能不能被调用的是 lea-tools 的查询，那它的命名就是权威的。
死掉的四列由 migrations/006 删掉。
"""

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    and_,
    func,
    or_,
    text,
)

from lea_config.database import (
    DEFAULT_TENANT_ID,
    OWNER_SCOPE_PLATFORM,
    OWNER_SCOPE_USER,
    Base,
    OwnerMixin,
    TenantMixin,
)

# 跟 lea_tools.db.models.Tool 的 ENUM 保持逐值一致 —— 两个模型映射同一列，
# 值集合不一样的话 create_all() 在新库上会建出一个双方都不认的 ENUM。
#
# ⚠️ 这里列全 5 个值是因为**列的定义**必须完整；但**接口能接受哪些**是另一件事，
# 见 api/tools.py 的 UserExecutionType（只有 api / mcp）。
EXECUTION_TYPES = ("local", "sandbox", "api", "internal", "mcp")


class Tool(OwnerMixin, TenantMixin, Base):
    __tablename__ = "tools"
    __table_args__ = (
        # **用户内唯一**（migrations/007）。演进过三轮，每一轮都是被上一轮的后果推着走的：
        #
        #   UNIQUE(name)                          全局唯一 —— 租户 A 建了 web_search
        #                                         之后全平台没人能再有同名工具（005 删）
        #   UNIQUE(tenant_id, name)               租户内唯一 —— 同租户里 alice 占了
        #                                         weather，bob 就建不了（007 删）
        #   UNIQUE(tenant_id, name, COALESCE(...)) 每个人各自唯一 ← 现在这个
        #
        # 为什么第三列是 COALESCE 而不是直接写 owner_user_id：MySQL 里 NULL 不参与
        # 唯一性判断，而平台工具的 owner_user_id 就是 NULL —— 直接写等于对平台工具
        # 完全不生效，可以插入任意多行同名平台工具。折成空串让两种归属落进同一个键空间：
        #
        #   平台工具  ('t1', 'weather', '')       每租户每名字一行
        #   用户工具  ('t1', 'weather', 'alice')  跟 ('t1','weather','bob') 是不同键
        #
        # ⚠️ 这条**管不了遮蔽**（alice 的 weather 和平台的 weather 是两个不同的键，
        # SQL 层面完全合法）。"用户不能覆盖系统内置工具"是产品规则而且跨租户，
        # 由 api/tools.py 的 _reject_name_conflict 在接口层挡。分工写在这里，
        # 免得下一个人以为库里已经拦住了。
        Index(
            "uq_tools_owner_name",
            "tenant_id",
            "name",
            text("(COALESCE(owner_user_id, ''))"),
            unique=True,
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    # 255 而不是 100（migrations/018 放宽的）。MCP 工具名是 sync 生成的
    # `mcp_{server_id}_{远端名字}`，而远端名字由**别人的服务**决定 —— 100 加上前缀
    # 很容易不够，而超了的后果是整批 sync 在 commit 时炸，不是"少一个工具"。
    #
    # 上限受唯一索引约束：uq_tools_owner_name 是 (tenant_id, name, owner_user_id)，
    # InnoDB 索引键 3072 字节 ÷ utf8mb4 的 4 字节，减掉另两列，name 最多 654 字符。
    # 255 留了余量，好让将来往那个索引里再加一列时不用先缩这一列。
    name = Column(String(255), nullable=False, index=True)
    display_name = Column(String(200), default="")
    description = Column(Text, default="")
    category = Column(String(50), default="general")

    # ── 执行契约：下面这些是执行器真正读的 ──────────────────────────────
    execution_type = Column(
        Enum(*EXECUTION_TYPES, name="exec_type_enum"), nullable=False
    )
    schema_json = Column(JSON, nullable=False)
    endpoint = Column(String(500))
    sandbox_config = Column(JSON)
    http_adapter_config = Column(JSON)
    mcp_server_id = Column(Integer, ForeignKey("mcp_servers.id"), nullable=True)
    supports_local = Column(Boolean, default=False)
    supports_cloud = Column(Boolean, default=True)
    enabled = Column(Boolean, default=True, index=True)

    # 上一轮把 is_builtin 归进"lea-config 以前写的死列"里不再映射，那是判断错了：
    # 它不是跟 enabled 重复的那种死列，它标的是**实现在代码里**（lea-tools 的 seed.py
    # 定义了 5 个内置工具，执行走 INTERNAL_ROUTES / 硬编码沙箱适配）。表里那行只是给
    # 引擎看的元数据 —— 用户改 endpoint 只会把 internal 路由指向一个不存在的地方。
    #
    # 不映射的后果是接口层拦不住"改内置工具"：PUT/DELETE 读不到这个标志，就只能靠
    # 归属判断，而内置工具的归属是 platform —— 于是**每个租户管理员都能改掉平台内置
    # 工具的定义**。跟 Skill 的做法对齐（models/skill.py 也有这一列，api/skills.py
    # 用它挡 403）。
    is_builtin = Column(Boolean, nullable=False, default=False, index=True)

    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


# 用户私有工具在**发给模型的清单里**的前缀。库里的 `name` 仍然是用户起的裸名字。
#
# ⚠️ 这个值必须跟 lea-tools 的 `services/__init__.py::USER_TOOL_PREFIX` 一致，
# **故意在这里重新定义一遍**而不是从那边导 —— 两个服务分开部署，lea-config 不依赖
# 执行侧的包。跟 OWNER_SCOPE_* 在三处各定义一遍是同一个取舍。
def visible_to(tenant_id: str, user_id: str | None):
    """"这个人能用哪些工具"的三段式判据，返回一组 SQLAlchemy 过滤条件。

        全局平台工具   (default, platform, NULL)   谁都能用
        本租户共享的   (t1,      platform, NULL)   t1 的人能用
        自己私有的     (t1,      user,     alice)  只有 alice 能用

    ## 为什么提成一个函数

    这条规则要在**至少三个地方**成立：绑定到 agent 时、按名字解析执行时、挂载到会话
    时。而它上一次被各写一遍的结果是 `bind_tool` 整个漏掉了校验 —— 旁边 180 行的
    `bind_skill` 写对了、有注释、有测试，工具那份原样留着裸的
    `AgentToolBinding(agent_id=..., **body.model_dump())`，猜一个 id 就能绑走别人的
    工具。同一个文件里，两个几乎一样的函数，一个查了一个没查。

    所以新增用到这条规则的地方一律从这里取，别再抄一遍。
    （lea-tools 侧的 `visible_tools()` 是同一条规则的另一个实现 —— 两个服务分开部署，
    不能互相 import，跟 OWNER_SCOPE_* 各定义一遍是同一个取舍。改这里要同步改那边。）

    user_id 为空时**只返回平台工具**，是 fail-closed：没有身份就当没有私有工具，
    而不是"匹配 owner_user_id IS NULL"——后者会把平台工具和"没主人的行"混为一谈。
    """
    conds = [
        Tool.tenant_id.in_([tenant_id, DEFAULT_TENANT_ID]),
    ]
    if user_id:
        conds.append(
            or_(
                Tool.owner_scope == OWNER_SCOPE_PLATFORM,
                and_(
                    Tool.owner_scope == OWNER_SCOPE_USER,
                    Tool.owner_user_id == user_id,
                ),
            )
        )
    else:
        conds.append(Tool.owner_scope == OWNER_SCOPE_PLATFORM)
    return conds


USER_TOOL_PREFIX = "my__"


def exposed_name(tool: "Tool") -> str:
    """库里的行 → **发给模型**的工具名。

    ## 为什么下发侧必须做这件事

    执行侧的 `resolve_tool` 是**严格**解析的：`my__weather` 只在用户私有里找，裸的
    `weather` 只在平台/租户共享里找，不做裸名兜底（兜底会把刚拆掉的歧义装回去）。

    而 lea-tools 那边加前缀的 `list_tools_for_session` **没有任何调用方** —— 引擎的
    工具清单来自 `/internal/agent-config`，也就是这里。所以以前的实际行为是：

        下发裸名 weather → 模型调 weather → resolve_tool 在 owner_scope != USER
        里找 → **404**

    也就是说**用户私有工具结构上调不通**。今天库里 0 行 user 工具，所以它潜伏着；
    第一个建私有工具的人会撞上，而症状是"工具明明绑了却说找不到"。

    ## 为什么加前缀而不是禁止同名

    工具名是**用户内唯一**的（migrations/007），一个租户里 alice 和 bob 可以各有一个
    `weather`。不加前缀的话这两个在清单里是同一个 key。MCP 生态踩过同样的坑（GitHub
    和 Linear 都有 create_issue），规范给聚合方的建议就是自己加前缀消歧。

    前缀里不放 user_id：一份清单里最多只有一个人的工具，区分"我的"和"共享的"只需要
    一个 bit；放 user_id 会让工具名随账号变动，而且工具名是发给模型的 —— 等于把内部
    id 漏进提示词。
    """
    if tool.owner_scope == OWNER_SCOPE_USER:
        return f"{USER_TOOL_PREFIX}{tool.name}"
    return tool.name
