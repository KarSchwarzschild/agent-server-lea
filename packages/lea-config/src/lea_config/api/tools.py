"""Tool CRUD — 统一 /tools 路径，归属决定权限。

## 为什么普通用户也能建工具

"建工具"本质是**填个表单描述一次 HTTP 调用**（execution_type=api）或**接一个 MCP
server**（mcp）。那是用户配置自己工作空间的一部分，不是管理员特权。所以这里的门不是
角色而是**归属**：谁建的谁能改，管理员建的落 platform（全租户可用，只有管理员能改）。

## 为什么只放开 api / mcp 两种执行类型

另外三种是平台内部的，外部填了要么走不通要么危险（这段判断来自 lea-tools 的
admin_tools.py，那边是这张表的执行侧）：

    local     executor/router.py 的 match 里没有这个 case，落到 unknown type
    internal  目标路径来自硬编码的 INTERNAL_ROUTES，外部填了也走不通
    sandbox   硬编码读 params.code/language/timeout，等于 code_interpreter 的克隆

sandbox 尤其不能放开：建出来的行到 Go 侧走 DynamicTool，而它的
IsConcurrencySafe() 硬编码 true（platform_tools.go），于是引擎会并行调一个持有跨调用
沙箱状态的工具。DB 那列是 5 值 ENUM，能拦住乱填，拦不住填一个**合法却不该由外部
指定**的值 —— 所以白名单必须在接口层。
"""

from __future__ import annotations

from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from lea_authkit import (
    DELETE,
    UPDATE,
    Actor,
    require,
    require_actor,
    scoped_filter,
)
from lea_config.database import (
    DEFAULT_TENANT_ID,
    OWNER_SCOPE_PLATFORM,
    OWNER_SCOPE_USER,
    get_db,
)
from lea_config.models.tool import Tool

router = APIRouter(prefix="/tools", tags=["tools"])

UserExecutionType = Literal["api", "mcp"]


# ---------- Schemas ----------

class ToolCreate(BaseModel):
    # populate_by_name 让内部名也能用（测试里直接构造对象时方便），但线上传的是 alias。
    model_config = {"populate_by_name": True}

    name: str = Field(min_length=1, max_length=100)
    display_name: str = ""
    description: str = ""
    category: str = "general"
    execution_type: UserExecutionType
    # 线上契约是 schema_json（跟列名一致），但字段名不能叫这个 —— BaseModel 上有一个
    # 同名的 V1 遗留方法，直接用会 shadow 它并抛 UserWarning。所以走 alias：
    # 对外是 schema_json，对内是 tool_schema，model_dump(by_alias=True) 拿回列名。
    tool_schema: dict[str, Any] = Field(alias="schema_json")
    endpoint: Optional[str] = None
    http_adapter_config: Optional[dict] = None
    mcp_server_id: Optional[int] = None


class ToolUpdate(BaseModel):
    model_config = {"populate_by_name": True}

    display_name: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    execution_type: Optional[UserExecutionType] = None
    tool_schema: Optional[dict[str, Any]] = Field(None, alias="schema_json")
    endpoint: Optional[str] = None
    http_adapter_config: Optional[dict] = None
    enabled: Optional[bool] = None
    # name 不给改：它是 agent 绑定里的引用键（按名字找工具，见 lea-tools 的
    # visible_tools），改了等于把已有绑定悄悄指向空。要改就删了重建。


# ---------- Routes ----------

@router.get("")
def list_tools(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    # scoped_filter 自带 tenant_id 约束，不要再叠一个 —— 叠了的话平台管理员
    # （scoped_filter 返 true()）会被这里的 tenant 条件重新夹死，跟 can() 矛盾。
    # 系统内置工具由 lea-tools 默认加载，不属于用户可配置的动态工具；这里仅用于
    # 动态工具的管理，避免 UI 把不可编辑的内置能力误当作可选项。
    q = db.query(Tool).filter(
        scoped_filter(actor, Tool),
        Tool.is_builtin.is_(False),
    )
    total = q.count()
    items = q.order_by(Tool.id).offset((page - 1) * page_size).limit(page_size).all()
    return {"total": total, "items": [_row_to_dict(t) for t in items]}


@router.post("", status_code=201)
def create_tool(
    body: ToolCreate,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    """建工具。管理员建的归平台，普通用户建的归自己。

    这是 can() 里"谁能改"的来源，所以归属必须在这里定死，不能靠调用方传 —— 传的话
    普通用户就能把 owner_scope 填成 platform，等于自助提权成"全租户可用"。
    """
    if actor.is_tenant_admin:
        scope, owner = OWNER_SCOPE_PLATFORM, None
    else:
        scope, owner = OWNER_SCOPE_USER, actor.user_id

    _reject_name_conflict(db, actor.tenant_id, body.name, scope, owner)

    tool = Tool(
        tenant_id=actor.tenant_id,
        owner_scope=scope,
        owner_user_id=owner,
        # 显式写死。ToolCreate 里本来就没有这个字段，所以传不进来 —— 但"内置由代码
        # 定义"这件事值得在建行的地方看得见，而不是靠一个 schema 里的缺席。
        is_builtin=False,
        # by_alias 必须有：字段内部叫 tool_schema，列叫 schema_json。
        # 漏了的话 Tool(**{"tool_schema": ...}) 直接 TypeError。
        **body.model_dump(by_alias=True),
    )
    db.add(tool)
    db.commit()
    db.refresh(tool)
    return _row_to_dict(tool)


@router.put("/{tool_id}")
def update_tool(
    tool_id: int,
    body: ToolUpdate,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    tool = _load(db, tool_id)
    # require 而不是 require_admin：跨租户 404、同租户越权 403、自己的东西放行。
    require(actor, UPDATE, tool)
    # 顺序要紧：先 require 再拦内置。反过来的话，别的租户探一个 id 会拿到 403
    # "内置工具不可修改"，等于确认了这行存在 —— require 有意让跨租户是 404。
    _reject_if_builtin(tool, "修改")
    for k, v in body.model_dump(exclude_unset=True, by_alias=True).items():
        setattr(tool, k, v)
    db.commit()
    db.refresh(tool)
    return _row_to_dict(tool)


@router.delete("/{tool_id}", status_code=204)
def delete_tool(
    tool_id: int,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    tool = _load(db, tool_id)
    require(actor, DELETE, tool)
    _reject_if_builtin(tool, "删除")
    db.delete(tool)
    db.commit()


def _reject_name_conflict(
    db: Session, tenant_id: str, name: str, scope: str, owner: str | None
) -> None:
    """建工具时挡住会造成**按名字派发有歧义**的同名。

    判据不是"库里有没有同名行"，而是**会不会有人同时看见两个一样的工具名**。而
    "工具名"指的是发给模型的那个（lea-tools 的 `exposed_name`），不是库里的 `name`。

    ── 命名空间把大部分冲突变成了不可能 ────────────────────────────────

    用户私有工具在能力清单里带 `my__` 前缀，共享/内置不带。所以下面这些**不再是
    冲突**，一律放行：

        alice 建 weather，bob 也建 weather      两人各自 my__weather，互相看不见
        alice 建 kb_search（跟内置同名）         她看到的是 my__kb_search
        alice 建 weather，管理员也建共享 weather  my__weather vs weather

    最后一条以前是拒的，而且是双向拒 —— 挡住管理员那一侧尤其别扭（一个用户占了名字，
    管理员就建不了同名共享工具）。加了前缀之后这个取舍整个消失了：不需要决定谁赢，
    因为它们压根不同名。

    ── 还剩两条 ────────────────────────────────────────────────────────

    **1. 同一个人不能有两个同名工具。** 这条就是唯一索引的镜像（migrations/007），
    在这里拦是为了给一条 409 而不是 IntegrityError 500。

    **2. 管理员建的共享工具不能占用系统内置工具名。** 共享工具和内置工具**都不带
    前缀**，所以它们之间仍然会真撞。而且内置工具住在 DEFAULT_TENANT_ID，跨租户 ——
    唯一索引根本碰不到这两行，只能在这里挡。这也正是你说的"不能覆盖系统底层工具"
    唯一还能发生的地方。
    """
    if scope == OWNER_SCOPE_PLATFORM and tenant_id != DEFAULT_TENANT_ID:
        builtin = (
            db.query(Tool.id)
            .filter(
                Tool.tenant_id == DEFAULT_TENANT_ID,
                Tool.name == name,
                Tool.is_builtin.is_(True),
            )
            .first()
        )
        if builtin:
            raise HTTPException(409, f"{name!r} 是系统内置工具名，共享工具不能占用")

    dup = (
        db.query(Tool.id)
        .filter(
            Tool.tenant_id == tenant_id,
            Tool.name == name,
            Tool.owner_scope == scope,
            Tool.owner_user_id.is_(None) if owner is None else Tool.owner_user_id == owner,
        )
        .first()
    )
    if dup:
        raise HTTPException(
            409,
            f"你已经有一个叫 {name!r} 的工具了" if owner
            else f"本租户已经有一个叫 {name!r} 的共享工具了",
        )


def _reject_if_builtin(tool: Tool, verb: str) -> None:
    """内置工具的**定义**不可改不可删。

    403 而不是 404：这一行确实存在，说"这个改不了"是准确的；返 404 会让调用方
    以为自己找错了 id 然后反复重试。跟 api/skills.py 对内置 Skill 的处理一致。

    ⚠️ 这不等于"内置工具不可定制"。可定制性落在**绑定参数**上，不在工具行上 ——
    `agent_kb_bindings` 有 strategy/top_k/weight，`agents` 有 kb_mode/memory_mode。
    业界也是这么分的（OpenAI Assistants 的 file_search 配 vector_store 和
    max_num_results，工具本身不可编辑；Dify 的内置工具节点不能编辑，暴露的是节点
    参数）。所以锁死这里不会堵住任何真实需求。
    """
    if tool.is_builtin:
        raise HTTPException(403, f"内置工具不可{verb}")


def _load(db: Session, tool_id: int) -> Tool:
    """按 id 取一行。**故意不带租户过滤** —— 那是 require() 的活。

    在这里过滤掉别的租户会让越权变成 404，看着更"安全"，实际是把两种情况混成一种：
    require() 有意区分跨租户 404 和同租户越权 403（后者用户需要知道"这不是你的"，
    否则会对着 404 反复重试）。
    """
    tool = db.get(Tool, tool_id)
    if tool is None:
        raise HTTPException(404, "Tool not found")
    return tool


def _row_to_dict(obj) -> dict:
    return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}
