"""工具可见性 + 解析 + 权限校验。"""

import structlog
from sqlalchemy import and_, case, or_
from sqlalchemy.orm import Session

from lea_tools.db.models import (
    GLOBAL_TENANT_ID,
    OWNER_SCOPE_PLATFORM,
    OWNER_SCOPE_USER,
    Tool,
    ToolPermission,
)

log = structlog.get_logger()

# 用户私有工具在**能力清单里**的前缀。库里的 `name` 仍然是用户起的裸名字。
#
# ## 为什么加前缀，而不是禁止同名
#
# 这是聚合层的通用做法。MCP 生态踩过一遍同样的坑：工具名只在单个 server 内唯一，
# 聚合多个 server 时必然撞（GitHub 和 Linear 都有 create_issue），规范给的建议就是
# 聚合方自己加前缀消歧。不加的后果分三档 —— OpenAI Agents SDK 直接拒绝启动、
# Cursor 出现过静默路由到同一个 server、以及"后注册者赢"被用来劫持调用。
#
# 我们**已经在这么做了，只是只做了一半**：MCP 同步出来的工具是 `mcp_{server_id}_{name}`，
# 而用户自建的 api 工具用裸名字。同一张表两套命名策略，冲突全靠接口层拦。
#
# ## 为什么前缀里不放 user_id
#
# 一份能力清单里**最多只有一个人的工具**（visible_tools 已按 owner_user_id 过滤），
# 所以区分"我的"和"共享的"只需要一个 bit。放 user_id 会让工具名变长、随账号变动，
# 而且工具名是发给模型的 —— 等于把内部 id 漏进提示词。
#
# ## 为什么内置和租户共享不加前缀
#
# 它们是平台自己的命名空间，跟 Claude Code 里 Read/Bash 裸名、MCP 工具带 mcp__ 前缀
# 是同一个分法。而且租户共享工具每租户每名字只有一行（唯一索引保证），本身无歧义。
USER_TOOL_PREFIX = "my__"


def exposed_name(tool) -> str:
    """库里的行 → 发给模型的工具名。"""
    if tool.owner_scope == OWNER_SCOPE_USER:
        return f"{USER_TOOL_PREFIX}{tool.name}"
    return tool.name


def visible_tools(db: Session, tenant_id: str, user_id: str | None = None):
    """这个人能看到的工具：平台的 + 他自己的。

    所有按名字找工具的地方都必须走这里。以前是裸的 `Tool.name == ...`，不带任何
    租户约束 —— 而工具行里有 endpoint / http_adapter_config / sandbox_config，
    拿错行就是拿着别的租户的配置去执行。

    ── 两个轴，缺一不可 ────────────────────────────────────────────────

    **租户**：平台内置工具住在 GLOBAL_TENANT_ID，所以是 IN 而不是 ==。

    **人**：工具名是**用户内唯一**的（migrations/007），一个租户里 alice 和 bob
    可以各有一个 `weather`。只按租户过滤的话这两行同时可见，而调用方是按名字
    `.first()` —— 会在两个人的工具之间随便挑一个，等于跨用户串配置。

    ⚠️ `user_id=None` 时**只能看到平台工具**，看不到任何人的私有工具。这是有意的
    fail-closed：身份缺失时宁可少给工具，也不能把"没有身份"当成"匹配 owner_user_id
    IS NULL"从而漏出别人的东西。SQL 上这是自然结果（`owner_user_id == None` 生成
    IS NULL，只命中平台行），但它是靠语义成立的，不是靠巧合 —— 别把这个条件改成
    `or_` 里的短路。

    ── 排序：内置 > 平台 > 自己的 ──────────────────────────────────────

    上一版这里写的是"自己的优先，租户可以覆盖平台内置工具"。**那是错的。**
    内置工具的实现在代码里（seed.py 的 BUILTIN_TOOLS），表里那行只是给引擎看的
    元数据，用户覆盖它没有任何意义 —— 改 endpoint 只会把 internal 路由指向一个
    不存在的地方。正确的做法不是决定同名时谁赢，而是**让同名根本建不出来**
    （lea-config 的 create_tool 拦 409）。

    排序在这里只剩一个作用：**已经存在的非法同名状态要收敛得确定**，而不是由
    存储引擎随机决定。所以内置排最前 —— 遮蔽不生效，比"用户的配置悄悄赢了"安全。
    同为平台工具时本租户的排在全局的前面（更具体的赢）。

    ⚠️ 加了命名空间之后（见 USER_TOOL_PREFIX），"用户工具遮蔽平台工具"这一类同名
    已经**构造不出来**了 —— 两者在清单里根本不是同一个名字。排序现在只剩一个作用：
    两行**平台**工具同名（全局内置 + 租户共享，都是裸名）时收敛得确定。

    ⚠️ 排序只对 `.first()` 有意义。按名字取单行请走 `resolve_tool()`，它认前缀、
    并且在真的同名时打 warning。要**列举**工具的地方（registry.py）得自己去重。
    """
    return (
        db.query(Tool)
        .filter(
            Tool.tenant_id.in_([tenant_id, GLOBAL_TENANT_ID]),
            or_(
                Tool.owner_scope != OWNER_SCOPE_USER,
                Tool.owner_user_id == user_id,
            ),
        )
        .order_by(
            case(
                (Tool.is_builtin.is_(True), 0),
                (
                    and_(
                        Tool.owner_scope == OWNER_SCOPE_PLATFORM,
                        Tool.tenant_id == tenant_id,
                    ),
                    1,
                ),
                (Tool.owner_scope == OWNER_SCOPE_PLATFORM, 2),
                else_=3,
            ),
            # 同一档里按 id 定序 —— 没有这一条，同档多行还是"存储引擎说了算"。
            Tool.id,
        )
    )


def resolve_tool(
    db: Session,
    tenant_id: str,
    user_id: str | None,
    tool_name: str,
    *,
    enabled_only: bool = True,
):
    """把**模型发来的工具名**解析成唯一一行。

    模型看到的名字来自 `exposed_name()`，所以这里必须认前缀 —— 这是命名空间能成立
    的另一半：光在清单里改名字，执行时按裸名字查，等于没加。

    ── 严格解析，不做裸名兜底 ──────────────────────────────────────────

    `my__weather` 只在**用户私有**里找，裸的 `weather` 只在**平台/租户共享**里找。
    模型漏了前缀发来裸的 `weather` 时返 404，而不是"猜它想调私有的那个"。

    兜底看起来友好，但它把刚拆掉的歧义原样装回来了：一旦裸名字能命中私有工具，
    "共享的 weather"和"我的 weather"又变成同一个 key。宁可 404 —— 那是个模型下一轮
    能自己纠正的错误，而串到别人的工具上不是。

    ── 还剩哪种同名 ────────────────────────────────────────────────────

    两行**平台**工具同名：全局内置的 `web_search` + 某租户共享的 `web_search`，
    两者都是裸名。这一类由 lea-config 的接口层拦（管理员不能占用内置名），
    存量由 scripts/check_tool_name_shadowing.py 捞。真撞上时这里收敛并打 warning ——
    静默选一行的症状会以"我改了工具配置但没生效"的形式出现在几周之后。
    """
    q = visible_tools(db, tenant_id, user_id)
    if tool_name.startswith(USER_TOOL_PREFIX):
        q = q.filter(
            Tool.name == tool_name[len(USER_TOOL_PREFIX):],
            Tool.owner_scope == OWNER_SCOPE_USER,
        )
    else:
        q = q.filter(
            Tool.name == tool_name,
            Tool.owner_scope != OWNER_SCOPE_USER,
        )
    if enabled_only:
        q = q.filter(Tool.enabled.is_(True))
    rows = q.all()
    if not rows:
        return None
    if len(rows) > 1:
        log.warning(
            "tool_name_shadowed",
            tool_name=tool_name,
            tenant_id=tenant_id,
            user_id=user_id,
            # 报 id + 归属，才能直接定位该删哪一行
            candidates=[
                {"id": r.id, "tenant": r.tenant_id, "scope": r.owner_scope,
                 "owner": r.owner_user_id, "builtin": bool(r.is_builtin)}
                for r in rows
            ],
            resolved_id=rows[0].id,
        )
    return rows[0]


def check_permission(
    db: Session,
    tenant_id: str,
    user_id: str | None,
    role: str,
    agent_id: int | None,
    tool_name: str,
) -> bool:
    tool = resolve_tool(db, tenant_id, user_id, tool_name)
    if not tool:
        return False

    query = db.query(ToolPermission).filter(
        ToolPermission.tool_id == tool.id,
        ToolPermission.tenant_id == tenant_id,
    )

    rules = query.all()
    if not rules:
        return True

    for rule in rules:
        if rule.agent_id and rule.agent_id != agent_id:
            continue
        if rule.role != "*" and rule.role != role:
            continue
        return rule.allowed

    return True
