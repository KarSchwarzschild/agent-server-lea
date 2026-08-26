"""工具注册中心 — 提供可用工具列表。"""

from sqlalchemy.orm import Session

from lea_tools.db.models import Tool, ToolPermission
from lea_tools.services import exposed_name, visible_tools


def list_tools_for_session(
    db: Session,
    tenant_id: str,
    user_id: str | None,
    role: str,
    agent_id: int | None = None,
) -> list[dict]:
    """给引擎发这个 session 能用的工具清单。

    ⚠️ 这里以前是 `db.query(Tool).filter(enabled, supports_cloud)` —— **完全没有租户
    过滤**。它是"按名字查工具不带 tenant"那批里的第五处，而且是最外露的一处：返回值
    是发给引擎的能力清单，所以每个租户都能看到**所有**租户的工具名和 schema
    （schema 里常有业务字段名，等于泄漏内部结构）。前四处修的是"执行时拿错行"，
    这一处泄漏的是"存在哪些工具"。

    `user_id` 同样不能省：工具名是用户内唯一的，不带人过滤的话 alice 的私有工具会
    出现在 bob 的能力清单里 —— 而清单是发给引擎的，等于把别人的工具描述喂进
    另一个人的对话上下文。

    发出去的是 `exposed_name()` 而不是库里的 `name`：用户私有工具带 `my__` 前缀，
    所以它跟共享/内置工具**不可能同名**。去重按暴露名做，剩下能撞的只有两行平台
    工具同名那一种，按 `visible_tools` 的排序取第一个。同名本身是非法的，warning 由
    执行路径上的 `resolve_tool()` 打；这里不再打一遍，否则每次缓存未命中都会重复刷。
    """
    tools = (
        visible_tools(db, tenant_id, user_id)
        .filter(Tool.enabled.is_(True), Tool.supports_cloud.is_(True))
        .all()
    )

    result = []
    seen: set[str] = set()
    for tool in tools:
        name = exposed_name(tool)
        if name in seen:
            # 已经有一行同名的赢了（visible_tools 排序：内置 > 本租户共享 > 全局共享）。
            continue
        seen.add(name)
        rules = (
            db.query(ToolPermission)
            .filter(ToolPermission.tool_id == tool.id, ToolPermission.tenant_id == tenant_id)
            .all()
        )
        allowed = True
        for rule in rules:
            if rule.agent_id and rule.agent_id != agent_id:
                continue
            if rule.role != "*" and rule.role != role:
                continue
            allowed = rule.allowed
            break

        if allowed:
            result.append({
                "name": name,
                "display_name": tool.display_name,
                "description": tool.description,
                "schema": tool.schema_json,
                "execution_type": tool.execution_type,
            })
    return result
