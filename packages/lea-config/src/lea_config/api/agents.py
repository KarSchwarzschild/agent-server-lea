"""统一 Agent 路由 — 合并 admin + user 端点。

路径 = 资源（/agents），Auth = 权限（读用 agent 的 visible_to，写用 _require_mine）。
- 普通用户：只看到 全局平台层(default) + 本租户共享 + 自己私有 的 agent
  （见 models/agent.py::visible_to；跟 tool/skill/mcp 的三段式可见性一致，
   有 default 全局层，不用 authkit 的 scoped_filter —— 那个没有全局逃逸）
- 平台管理员：看到全部
- 写操作（改/绑定/分享）靠 _require_mine：只能改自己建的；admin-only 操作靠 _is_admin()
"""

from __future__ import annotations

import time
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from lea_authkit import Actor, require_actor
from pydantic import BaseModel, Field
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from lea_config.database import (
    DEFAULT_TENANT_ID,
    OWNER_SCOPE_PLATFORM,
    OWNER_SCOPE_USER,
    get_db,
)
from lea_config.models.agent import Agent
from lea_config.models.agent import visible_to as agent_visible_to
from lea_config.models.binding import (
    AgentKbBinding,
    AgentMcpBinding,
    AgentSkillBinding,
    AgentToolBinding,
)
from lea_config.models.knowledge import KnowledgeBase
from lea_config.models.mcp_server import MCPServer
from lea_config.models.mcp_server import visible_to as mcp_visible_to
from lea_config.models.skill import Skill
from lea_config.models.team import AgentTeam, AgentTeamMember
from lea_config.models.tool import Tool, visible_to

router = APIRouter(prefix="/agents", tags=["agents"])


# ── Schemas ────────────────────────────────────────────────

class AgentCreate(BaseModel):
    """统一创建 — 合并 admin 和 user 字段，管理字段 Optional。"""
    name: str = Field(min_length=1, max_length=100)
    description: str = ""
    avatar: str = ""
    welcome_message: str = ""
    soul_prompt: str = ""
    category: str = "general"
    example_questions: list = []
    sort_order: int = 0
    # admin 字段（普通用户忽略）
    model: str = ""
    temperature: int = 7
    # 100 而不是 20：一次迭代 = 一次模型往返，多文件任务每写一个文件就吃掉一轮。
    # 跟 lea-agent 的 config_loader.py 兜底值保持一致，那边有完整说明。
    max_turns: int = 100
    max_tokens: int = 4096
    kb_mode: str = "tool"
    memory_mode: str = "always"
    timeout_seconds: int = 120
    is_system: bool = False
    enabled: bool = True


class AgentUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    avatar: str | None = None
    welcome_message: str | None = None
    soul_prompt: str | None = None
    category: str | None = None
    example_questions: list | None = None
    sort_order: int | None = None
    model: str | None = None
    temperature: int | None = None
    max_turns: int | None = None
    max_tokens: int | None = None
    kb_mode: str | None = None
    memory_mode: str | None = None
    timeout_seconds: int | None = None
    enabled: bool | None = None
    # 乐观并发控制：前端读到这个 agent 时带出的 updated_at，原样传回来。
    # 省略=不检查（旧客户端兼容，行为等同于改之前那种"后写覆盖前写"）。
    # 带了就必须跟数据库当前值完全一致才允许更新——两个标签页同时打开同一个
    # agent 的编辑面板，先保存的那次会把 updated_at 往前推，后保存的那次
    # 这里核对不上，返回 409 而不是悄悄覆盖掉先保存的人的修改。
    expected_updated_at: datetime | None = None


class DefaultAgentBody(BaseModel):
    agent_id: int | None = None


class BindSkillBody(BaseModel):
    skill_id: int
    priority: int = Field(100, ge=0, le=9999)


class BindKbBody(BaseModel):
    kb_id: int
    strategy: str = Field("hybrid", pattern="^(semantic|keyword|hybrid)$")
    top_k: int = Field(5, ge=1, le=50)


class BindToolBody(BaseModel):
    tool_id: int
    enabled: bool = True


class BindMcpBody(BaseModel):
    mcp_server_id: int
    enabled: bool = True


# ── Helpers ────────────────────────────────────────────────

def _is_admin(actor: Actor) -> bool:
    return actor.is_platform_admin or actor.is_tenant_admin


def _resolve_super_agent(db: Session, tenant_id: str) -> Agent | None:
    return (
        db.query(Agent)
        .filter(
            Agent.name == "super-agent",
            Agent.tenant_id == tenant_id,
            Agent.is_system == True,  # noqa: E712
        )
        .first()
    )


def _agent_to_dict(agent: Agent, actor: Actor) -> dict:
    is_super = agent.is_system and agent.name == "super-agent"
    can_edit = _is_admin(actor) or (
        agent.owner_scope == OWNER_SCOPE_USER and agent.owner_user_id == actor.user_id
    )
    publication_status = (
        "draft"
        if agent.owner_scope == OWNER_SCOPE_USER
        else ("active" if agent.enabled else "suspended")
    )
    visibility = (
        "private"
        if agent.owner_scope == OWNER_SCOPE_USER
        else ("global" if agent.tenant_id == DEFAULT_TENANT_ID else "tenant")
    )
    allowed_actions = ["read"]
    if can_edit:
        allowed_actions.extend(["update", "delete"])
    if agent.owner_scope == OWNER_SCOPE_USER and can_edit:
        allowed_actions.append("submit_publication")
    return {
        "id": agent.id,
        "name": "Super Agent" if is_super else agent.name,
        "description": agent.description or "",
        "avatar": agent.avatar or "\U0001f916",
        "welcome_message": agent.welcome_message or "",
        "soul_prompt": agent.soul_prompt or "",
        "category": agent.category or "general",
        "category_label": agent.category or "通用",
        "is_system": bool(agent.is_system),
        "tenant_id": agent.tenant_id,
        "owner_scope": agent.owner_scope,
        "owner_user_id": agent.owner_user_id,
        "created_by": agent.created_by,
        "visibility": visibility,
        "can_edit": can_edit,
        "is_mine": bool(
            actor.user_id
            and agent.created_by
            and agent.created_by == actor.user_id
        ),
        "is_enabled": bool(agent.enabled),
        "sort_order": agent.sort_order or 0,
        "model": agent.model or "",
        "temperature": agent.temperature or 7,
        # 兜底 100，跟建库默认值和 lea-agent 的 config_loader.py 保持一致——
        # 三处任一处不一样，就会出现"界面显示 20、实际按 100 跑"这种查不出来的错位。
        "max_turns": agent.max_turns or 100,
        "max_tokens": agent.max_tokens or 4096,
        "kb_mode": agent.kb_mode or "tool",
        "memory_mode": agent.memory_mode or "always",
        "timeout_seconds": agent.timeout_seconds or 120,
        "allowed_skills": None,
        "example_questions": agent.example_questions or [],
        # 乐观并发控制用——前端保存时原样带回来当 expected_updated_at。
        # ISO 格式字符串，不是 datetime 对象：JSON 里没有 datetime 类型，
        # 而 isoformat() 的字符串排序/相等比较跟底层时间戳完全对应，
        # 服务端那边收到的还是解析成 datetime 之后按值比较，不依赖字符串
        # 格式本身要多精确，只要来回不失真。
        "updated_at": agent.updated_at.isoformat() if agent.updated_at else None,
        "publication_status": publication_status,
        "allowed_actions": allowed_actions,
    }


def _get_agent_or_404(db: Session, agent_id: int, actor: Actor) -> Agent:
    agent = db.query(Agent).filter(Agent.id == agent_id).first()
    if agent is None:
        raise HTTPException(404, "Agent not found")
    # 可见性跟 list_agents 用同一条规则（agent_visible_to）：本租户 + 全局平台层(default)。
    # 用 in-Python 判定而不是再查一次库——agent 已在手上。跨租户但非全局的一律 404，不泄露存在性。
    #
    # 注意这只放开**读**：写路径的 handler 拿到 agent 后还会走 _require_mine（只能改自己建的），
    # 所以别的租户能看到/召唤一个全局专家，但改不动它。
    if actor.is_platform_admin:
        return agent
    is_global = agent.tenant_id == DEFAULT_TENANT_ID and agent.owner_scope == OWNER_SCOPE_PLATFORM
    is_own_tenant = agent.tenant_id == actor.tenant_id
    if not (is_global or is_own_tenant):
        raise HTTPException(404, "Agent not found")
    return agent


def _require_mine(agent: Agent, actor: Actor) -> None:
    """普通用户只能操作自己创建的非系统 agent。admin 跳过。"""
    if _is_admin(actor):
        return
    if agent.is_system:
        raise HTTPException(403, "不能操作系统 Agent")
    if agent.created_by != actor.user_id:
        raise HTTPException(403, "只能操作自己创建的 Agent")


# ── Agent CRUD ─────────────────────────────────────────────

@router.get("")
async def list_agents(
    scope: str = "all",
    category: str | None = None,
    search: str | None = None,
    enabled: bool | None = None,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    from lea_config.metrics import record_agent_config_load
    from lea_config.services.cache import get_agent_list_cache, set_agent_list_cache

    started = time.perf_counter()
    is_adm = _is_admin(actor)

    # 只缓存普通用户的简单查询（scope=all, 无过滤条件）
    cache_key_params = f"{scope}:{category}:{search}:{enabled}:{page}:{page_size}"
    use_cache = not is_adm and scope == "all" and not category and not search and enabled is None and page == 1

    if use_cache:
        cached = await get_agent_list_cache(actor.tenant_id, f"{actor.user_id}:{cache_key_params}")
        if cached is not None:
            # cache_hit / cache_miss 只在 use_cache 为真时记。
            #
            # 为什么不给 use_cache=False 的请求也记一笔：那些请求（管理员、带搜索
            # 或分页条件的）**压根没查缓存**，把它们算成 miss 会把
            # CacheInvalidationRateHigh 的分母和分子一起污染 —— 管理员多点几次
            # 列表页就能把失效率顶过 10%，而 Redis 其实好得很。
            record_agent_config_load("cache_hit", time.perf_counter() - started)
            return cached

    # 可见性用 agent 自己的 visible_to（本租户 + 全局平台层 default），不用 authkit 的
    # scoped_filter —— 后者没有 default 全局逃逸，会把官方公共专家挡在别的租户外面。
    # 见 models/agent.py::visible_to 的说明。平台管理员看全部。
    if actor.is_platform_admin:
        q = db.query(Agent)
    else:
        q = db.query(Agent).filter(and_(*agent_visible_to(actor.tenant_id, actor.user_id)))

    # 普通用户只看 enabled 的；admin 可通过 enabled 参数过滤
    if not is_adm:
        q = q.filter(Agent.enabled == True)  # noqa: E712
    elif enabled is not None:
        q = q.filter(Agent.enabled == enabled)

    if search:
        q = q.filter(Agent.name.contains(search))
    if category:
        q = q.filter(Agent.category == category)

    if is_adm:
        # admin 分页
        total = q.count()
        items = (
            q.order_by(Agent.sort_order, Agent.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        rows = items
        result: dict = {"total": total, "items": [_agent_to_dict(a, actor) for a in rows]}
    else:
        rows = q.order_by(Agent.sort_order, Agent.id).limit(200).all()
        # scope 过滤（内存侧）
        if scope == "mine":
            rows = [a for a in rows if a.created_by == actor.user_id]
        elif scope == "system":
            rows = [a for a in rows if a.is_system]
        result = {"items": [_agent_to_dict(a, actor) for a in rows]}

    # 缓存普通用户的简单查询结果
    if use_cache:
        await set_agent_list_cache(actor.tenant_id, f"{actor.user_id}:{cache_key_params}", result)
        # 走到这里说明上面查缓存没命中（命中的那条路径已经 return 了）。
        record_agent_config_load("cache_miss", time.perf_counter() - started)

    return result


@router.get("/categories")
def get_categories(actor: Actor = Depends(require_actor)):
    return {"general": "通用"}


@router.get("/super-agent-categories")
def get_super_agent_categories(actor: Actor = Depends(require_actor)):
    return {"general": "通用"}


@router.get("/default")
def get_default_agent(
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    """当前用户默认 agent — 暂返回 tenant super-agent。"""
    super_agent = _resolve_super_agent(db, actor.tenant_id)
    return {"agent_id": super_agent.id if super_agent else None}


@router.put("/default")
def set_default_agent(body: DefaultAgentBody, actor: Actor = Depends(require_actor)):
    """No-op，等 user_preferences 表。"""
    return {"agent_id": body.agent_id}


@router.delete("/default")
def clear_default_agent(actor: Actor = Depends(require_actor)):
    return {"agent_id": None}


@router.get("/{agent_id}")
def get_agent(
    agent_id: int,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    """id=0 兑底 super-agent（旧前端硬编码）。"""
    if agent_id == 0:
        agent = _resolve_super_agent(db, actor.tenant_id)
        if agent is None:
            raise HTTPException(404, "super-agent not seeded")
        return _agent_to_dict(agent, actor)

    agent = _get_agent_or_404(db, agent_id, actor)
    return _agent_to_dict(agent, actor)


@router.post("", status_code=201)
async def create_agent(
    body: AgentCreate,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    from lea_config.services.cache import invalidate_agent_cache

    # 查重名
    dup = (
        db.query(Agent)
        .filter(Agent.tenant_id == actor.tenant_id, Agent.name == body.name)
        .first()
    )
    if dup:
        raise HTTPException(400, f"Agent 名称 '{body.name}' 已存在")

    is_adm = _is_admin(actor)
    data = body.model_dump()

    if not is_adm:
        # 普通用户强制约束
        data["is_system"] = False
        data["enabled"] = True

    agent = Agent(
        tenant_id=actor.tenant_id,
        created_by=actor.user_id,
        owner_scope=OWNER_SCOPE_PLATFORM if data.get("is_system") else OWNER_SCOPE_USER,
        owner_user_id=None if data.get("is_system") else actor.user_id,
        **data,
    )
    db.add(agent)
    db.commit()
    db.refresh(agent)

    # 失效缓存
    await invalidate_agent_cache(actor.tenant_id)

    return _agent_to_dict(agent, actor)


@router.put("/{agent_id}")
async def update_agent(
    agent_id: int,
    body: AgentUpdate,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    from lea_config.services.cache import invalidate_agent_cache

    agent = _get_agent_or_404(db, agent_id, actor)
    _require_mine(agent, actor)

    fields = body.model_dump(exclude_unset=True)
    expected = fields.pop("expected_updated_at", None)
    # 乐观并发控制：expected 为 None 时不检查（旧客户端兼容）。带了就必须
    # 跟数据库当前值完全一致，否则说明这期间有别的请求已经改过这一行——
    # 不管是同一个人开了两个标签页，还是同一租户里两个人同时编辑，结果都
    # 一样：谁的 updated_at 对不上，谁就该先刷新看最新内容，而不是让自己
    # 那份旧数据把刚保存的改动悄悄覆盖掉。
    #
    # 比较用 replace(microsecond=...) 而不是直接 ==：MySQL 的 DATETIME
    # 默认不存微秒，但 Python 的 datetime 解析 ISO 字符串时哪怕原串没有
    # 微秒部分也会补 0，反而是路上多绕一圈（序列化又反序列化）可能引入的
    # 微小误差被放大成"明明没人改过却报冲突"的假阳性，两边都截到秒更稳。
    if expected is not None:
        current = agent.updated_at.replace(microsecond=0) if agent.updated_at else None
        if current != expected.replace(microsecond=0):
            raise HTTPException(409, "该 Agent 已被修改，请刷新后重试")

    for k, v in fields.items():
        setattr(agent, k, v)
    db.commit()
    db.refresh(agent)

    # 失效缓存
    await invalidate_agent_cache(actor.tenant_id)

    return _agent_to_dict(agent, actor)


@router.delete("/{agent_id}")
async def delete_agent(
    agent_id: int,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    from lea_config.services.cache import invalidate_agent_cache

    agent = _get_agent_or_404(db, agent_id, actor)
    _require_mine(agent, actor)

    db.delete(agent)
    db.commit()

    # 失效缓存
    await invalidate_agent_cache(actor.tenant_id)

    return {"message": "已删除"}


# ── 共享 / 取消共享 ────────────────────────────────────────
#
# 共享一个 agent = 把 owner_scope 从 user 升到 platform，本租户所有人可用。
#
# ## 为什么要一道门
#
# agent 是个**容器**：它自己没有内容，内容全在它绑的 tool / skill / kb 里。把容器
# 共享出去而里面装着私有资源，等于绕开那些资源自己的可见性。
#
# 真链路实测过这个形状（scripts/my_prefix_e2e.py）：私有工具挂在共享 agent 上，
# 另一个用户的工具清单里确实出现了 my__stock_lookup。
#
# ## 硬拦 vs 软提示：差别在**安全问题还是可用性问题**
#
#     tool / skill / 团队成员    硬拦 —— 这是**安全**问题
#         它们的**定义本身**就是要下发给模型的：名字、描述、完整参数 schema 全部
#         进入别人的对话上下文。执行会被 resolve_tool 挡住，但该泄的在下发时就泄
#         完了 —— 而 schema 的字段名往往就是业务内部结构。挡不住就是真泄漏。
#
#     knowledge_base            只提示 —— 这是**可用性**问题
#         下发的只有 kb_ids，内容要再经过一次 kb_search 才拿得到，而那一跳按归属
#         授权。所以未公开的知识库对别人来说是**用不了**的：同一个 agent，owner
#         自己用能检索到，别人用检索不到，表现不一样。
#
#         这不是"要不要拦住泄漏"，是"要不要提醒作者他共享出去的 agent 对别人是
#         残的"。作者完全可能明知故犯（就想共享一个骨架，让别人自己接知识库），
#         所以只提示不拦。
#
#         ⚠️ 文案说的是"可能无法检索"而不是"无法检索"，因为 lea-rag 那层的归属
#         校验现在是弱的（server_admin_kb.py 的 list_all_kbs 不传 tenant_id 就返
#         全平台）。也就是说**实际行为可能是两头里的任意一头**：按设计别人用不了，
#         按现状别人可能反而搜得到。两种都不好，都该让作者知道，所以文案要留余地，
#         不能斩钉截铁说"别人用不了"给人虚假的安全感。见 [project-auth-gaps]。
#
# ## 拒绝，而不是连带发布
#
# 撞到未发布的资源直接 409 并列出**每一个**阻塞项。不做"顺手一起上架"：那是一个
# 动作改很多东西，而 skill 还有真正的审批流（skill_publish_requests），连带触发
# 等于替审批人做决定。

class ShareBlocker(BaseModel):
    kind: str          # tool / skill / team_member / kb
    id: int
    name: str
    reason: str


def _share_blockers(
    db: Session, agent: Agent
) -> tuple[list[ShareBlocker], list[ShareBlocker]]:
    """返回 (硬阻塞, 软提示)。硬阻塞为空 = 可以共享。

    **两个都列全，不是碰到第一个就返回** —— 用户要的是一张待办清单，一次一个地
    试错很折磨人，尤其绑了十几样东西的 agent。
    """
    blockers: list[ShareBlocker] = []
    warnings: list[ShareBlocker] = []

    tool_rows = (
        db.query(Tool)
        .join(AgentToolBinding, AgentToolBinding.tool_id == Tool.id)
        .filter(AgentToolBinding.agent_id == agent.id)
        .all()
    )
    for t in tool_rows:
        if t.owner_scope == OWNER_SCOPE_USER:
            blockers.append(ShareBlocker(
                kind="tool", id=t.id, name=t.name,
                reason=f"私有工具（属于 {t.owner_user_id}），要先共享到租户",
            ))

    skill_rows = (
        db.query(Skill)
        .join(AgentSkillBinding, AgentSkillBinding.skill_id == Skill.id)
        .filter(AgentSkillBinding.agent_id == agent.id)
        .all()
    )
    for s in skill_rows:
        if s.owner_scope == OWNER_SCOPE_USER:
            blockers.append(ShareBlocker(
                kind="skill", id=s.id, name=s.name,
                reason=f"私有技能（属于 {s.owner_user_id}），走 /skills/{s.id}/publish 上架",
            ))

    # MCP server —— 硬拦，跟 tool/skill 同理。整个 server 绑上去，会把它同步出的全部
    # 工具下发；server 私有而 agent 共享，就是把私有工具的名字/描述/schema 泄给所有
    # 使用者（internal.py 展开时不再按 user 过滤）。所以私有 server 要先共享到租户。
    mcp_rows = (
        db.query(MCPServer)
        .join(AgentMcpBinding, AgentMcpBinding.mcp_server_id == MCPServer.id)
        .filter(AgentMcpBinding.agent_id == agent.id)
        .all()
    )
    for m in mcp_rows:
        if m.owner_scope == OWNER_SCOPE_USER:
            blockers.append(ShareBlocker(
                kind="mcp_server", id=m.id, name=m.name,
                reason=f"私有 MCP server（属于 {m.owner_user_id}），要先共享到租户",
            ))

    # 知识库：**只提示不拦**。见上面"安全 vs 可用性"那段。
    kb_rows = (
        db.query(KnowledgeBase)
        .join(AgentKbBinding, AgentKbBinding.kb_id == KnowledgeBase.id)
        .filter(AgentKbBinding.agent_id == agent.id)
        .all()
    )
    for k in kb_rows:
        if k.visibility == "private":
            warnings.append(ShareBlocker(
                kind="kb", id=k.id, name=k.name,
                reason="这个知识库还没公开，其他人用这个 agent 时可能检索不到它，"
                       "回答会跟你自己用的时候不一样。要让所有人都能用就先把它"
                       "改成 public。",
            ))

    # 专家团成员 —— 硬拦。
    #
    # 不加这条的话前面几条全白做：共享 agent 的团里挂一个**私有**成员 agent，
    # 引擎会用成员的 agent_id 再打一次 /internal/agent-config
    # （runtime/expert_assembly.go:104），成员绑的私有工具照样下发。
    # 容器套容器，可见性要一路传下去。
    team = db.query(AgentTeam).filter_by(agent_id=agent.id).first()
    if team:
        member_ids = [
            m.member_agent_id
            for m in db.query(AgentTeamMember).filter_by(team_id=team.id).all()
        ]
        if member_ids:
            for ma in db.query(Agent).filter(Agent.id.in_(member_ids)).all():
                if ma.owner_scope == OWNER_SCOPE_USER:
                    blockers.append(ShareBlocker(
                        kind="team_member", id=ma.id, name=ma.name,
                        reason=f"专家团成员是私有 agent（属于 {ma.owner_user_id}），"
                               f"要先共享它",
                    ))

    return blockers, warnings


@router.get("/{agent_id}/share-check")
def check_share(
    agent_id: int,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    """预检：现在能不能共享，卡在哪，以及有什么要留意的。

    单独一个只读端点，前端可以在"共享"按钮旁边先亮出清单，而不是让用户点一下
    才发现被拒。
    """
    agent = _get_agent_or_404(db, agent_id, actor)
    _require_mine(agent, actor)
    blockers, warnings = _share_blockers(db, agent)
    return {
        "agent_id": agent.id,
        "already_shared": agent.owner_scope == OWNER_SCOPE_PLATFORM,
        "can_share": not blockers,
        "blockers": [b.model_dump() for b in blockers],
        "warnings": [w.model_dump() for w in warnings],
    }


@router.post("/{agent_id}/share")
def share_agent(
    agent_id: int,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    """把自己的私有 agent 升到租户共享档。"""
    agent = _get_agent_or_404(db, agent_id, actor)
    _require_mine(agent, actor)

    # 幂等：已经共享就直接返回，不 400。共享是"达到某个状态"，重复调用该无害 ——
    # 前端重试、双击按钮都会走到这里。
    if agent.owner_scope == OWNER_SCOPE_PLATFORM:
        return {"agent": _agent_to_dict(agent, actor), "warnings": []}

    blockers, warnings = _share_blockers(db, agent)
    if blockers:
        raise HTTPException(409, {
            "message": f"有 {len(blockers)} 项资源还没共享，agent 不能共享",
            "blockers": [b.model_dump() for b in blockers],
            "warnings": [w.model_dump() for w in warnings],
        })

    agent.owner_scope = OWNER_SCOPE_PLATFORM
    agent.owner_user_id = None
    # is_system 不动。共享 ≠ 系统内置：系统 agent 是平台预置且不可删的，而这是
    # 用户自己建的、共享出来的，他仍然该能改能删。
    db.commit()
    db.refresh(agent)
    # 软提示跟着成功一起返回 —— 共享成功了，但"你有个私有知识库在里面"这件事用户
    # 仍然该知道。丢掉它等于让软提示只在失败时出现，而那时反而没人看。
    return {
        "agent": _agent_to_dict(agent, actor),
        "warnings": [w.model_dump() for w in warnings],
    }


@router.post("/{agent_id}/unshare")
def unshare_agent(
    agent_id: int,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    """撤回共享，还给原作者。

    共享是个对别人可见的动作。没有回退路径的话它就是一道单向门 —— 用户会因此
    不敢点。所以这个端点跟 share 一起给。

    ⚠️ `created_by` 为空的行拒绝撤回。那些是**种子数据**（库里 agent 1/3/4/5/6 的
    created_by 就是 NULL），撤回等于把平台预置的 agent 塞进当前操作者的私人名下。
    """
    agent = _get_agent_or_404(db, agent_id, actor)
    _require_mine(agent, actor)

    if agent.owner_scope == OWNER_SCOPE_USER:
        return {"agent": _agent_to_dict(agent, actor)}  # 幂等，同 share

    if not agent.created_by:
        raise HTTPException(
            400, "这个 agent 没有记录创建者（多半是种子数据），不能撤回共享"
        )

    agent.owner_scope = OWNER_SCOPE_USER
    agent.owner_user_id = agent.created_by
    db.commit()
    db.refresh(agent)
    return {"agent": _agent_to_dict(agent, actor)}


# ── Skill Bindings ─────────────────────────────────────────

@router.get("/{agent_id}/skills")
def list_agent_skills(
    agent_id: int,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    _get_agent_or_404(db, agent_id, actor)
    rows = (
        db.query(AgentSkillBinding, Skill)
        .join(Skill, Skill.id == AgentSkillBinding.skill_id)
        .filter(AgentSkillBinding.agent_id == agent_id)
        .order_by(AgentSkillBinding.priority, Skill.id)
        .all()
    )
    return {
        "agent_id": agent_id,
        "skills": [
            {
                "binding_id": b.id,
                "skill_id": s.id,
                "skill_name": s.name,
                "display_name": s.display_name,
                "priority": b.priority,
                "enabled": b.enabled,
            }
            for b, s in rows
        ],
    }


@router.post("/{agent_id}/skills")
def bind_skill(
    agent_id: int,
    body: BindSkillBody,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    agent = _get_agent_or_404(db, agent_id, actor)
    _require_mine(agent, actor)

    # ⚠️ 必须带可见性过滤。以前这里是裸的 `filter(Skill.id == body.skill_id)` ——
    # 只按 id 查，不看租户也不看归属。于是任何人猜一个 id 就能把**别的租户的**
    # skill 绑到自己 agent 上，而下发链路会老老实实从 S3 把包拉下来投进他的会话。
    #
    # 做 skill 市场之后这个洞会变大：市场页会把 id 变成公开信息，不用猜。
    #
    # 三层可见性：全局市场的（tenant='default'）+ 本租户共享的 + 自己私有的。
    # 停用的也不能绑 —— 绑上了也不会下发（见 internal.py），那是个只会让人困惑的状态。
    skill = (
        db.query(Skill)
        .filter(
            Skill.id == body.skill_id,
            Skill.enabled.is_(True),
            Skill.tenant_id.in_([actor.tenant_id, DEFAULT_TENANT_ID]),
            or_(
                Skill.owner_scope == OWNER_SCOPE_PLATFORM,
                and_(
                    Skill.owner_scope == OWNER_SCOPE_USER,
                    Skill.owner_user_id == actor.user_id,
                ),
            ),
        )
        .first()
    )
    if skill is None:
        raise HTTPException(404, "skill not found")

    existing = (
        db.query(AgentSkillBinding)
        .filter_by(agent_id=agent_id, skill_id=body.skill_id)
        .first()
    )
    if existing:
        existing.priority = body.priority
        existing.enabled = True
        db.commit()
        db.refresh(existing)
        return {"binding_id": existing.id, "skill_id": body.skill_id, "priority": body.priority}

    binding = AgentSkillBinding(
        agent_id=agent_id,
        skill_id=body.skill_id,
        priority=body.priority,
        enabled=True,
    )
    db.add(binding)
    db.commit()
    db.refresh(binding)
    return {"binding_id": binding.id, "skill_id": body.skill_id, "priority": body.priority}


@router.delete("/{agent_id}/skills/{skill_id}")
def unbind_skill(
    agent_id: int,
    skill_id: int,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    agent = _get_agent_or_404(db, agent_id, actor)
    _require_mine(agent, actor)

    b = (
        db.query(AgentSkillBinding)
        .filter_by(agent_id=agent_id, skill_id=skill_id)
        .first()
    )
    if not b:
        raise HTTPException(404, "binding not found")
    db.delete(b)
    db.commit()
    return {"message": "已解绑"}


# ── KB Bindings ────────────────────────────────────────────

@router.get("/{agent_id}/kbs")
def list_agent_kbs(
    agent_id: int,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    _get_agent_or_404(db, agent_id, actor)
    rows = (
        db.query(AgentKbBinding, KnowledgeBase)
        .join(KnowledgeBase, KnowledgeBase.id == AgentKbBinding.kb_id)
        .filter(AgentKbBinding.agent_id == agent_id)
        .all()
    )
    return {
        "agent_id": agent_id,
        "kbs": [
            {
                "binding_id": b.id,
                "kb_id": kb.id,
                "kb_name": kb.name,
                "strategy": b.strategy,
                "top_k": b.top_k,
                "enabled": b.enabled,
            }
            for b, kb in rows
        ],
    }


@router.post("/{agent_id}/kbs")
def bind_kb(
    agent_id: int,
    body: BindKbBody,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    agent = _get_agent_or_404(db, agent_id, actor)
    _require_mine(agent, actor)

    kb = db.query(KnowledgeBase).filter(KnowledgeBase.id == body.kb_id).first()
    if kb is None:
        raise HTTPException(404, "知识库不存在")
    if kb.visibility == "private" and kb.owner != actor.user_id:
        raise HTTPException(403, "无权使用该知识库")

    existing = (
        db.query(AgentKbBinding)
        .filter_by(agent_id=agent_id, kb_id=body.kb_id)
        .first()
    )
    if existing:
        existing.strategy = body.strategy
        existing.top_k = body.top_k
        existing.enabled = True
        db.commit()
        db.refresh(existing)
        return {"binding_id": existing.id, "kb_id": body.kb_id, "strategy": body.strategy}

    binding = AgentKbBinding(
        agent_id=agent_id,
        kb_id=body.kb_id,
        strategy=body.strategy,
        top_k=body.top_k,
        enabled=True,
    )
    db.add(binding)
    db.commit()
    db.refresh(binding)
    return {"binding_id": binding.id, "kb_id": body.kb_id, "strategy": body.strategy}


@router.delete("/{agent_id}/kbs/{kb_id}")
def unbind_kb(
    agent_id: int,
    kb_id: int,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    agent = _get_agent_or_404(db, agent_id, actor)
    _require_mine(agent, actor)

    b = (
        db.query(AgentKbBinding)
        .filter_by(agent_id=agent_id, kb_id=kb_id)
        .first()
    )
    if not b:
        raise HTTPException(404, "binding not found")
    db.delete(b)
    db.commit()
    return {"message": "已解绑"}


# ── Tool Bindings (admin-only, 从旧 bindings.py 迁入) ────

@router.post("/{agent_id}/tools")
def bind_tool(
    agent_id: int,
    body: BindToolBody,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    agent = _get_agent_or_404(db, agent_id, actor)
    _require_mine(agent, actor)

    # ⚠️ 必须带可见性过滤 —— 跟 bind_skill 是同一个洞，这里以前一个字的校验都没有：
    # `AgentToolBinding(agent_id=agent_id, **body.model_dump())`，tool_id 直接来自
    # 请求体。于是猜一个 id 就能把**别人的**（别的租户、或同租户别的用户的）工具绑到
    # 自己的 agent 上。
    #
    # 而且工具比 skill 更容易触发：skill 还要从 S3 拉包，工具绑上就下发。泄漏的是
    # 名字 + 描述 + schema，而 schema 里常是业务字段名（lea-tools 的 registry.py
    # 早就写过这句）—— 等于把别人的内部结构送进自己的对话上下文。
    #
    # 执行那一步会被 lea-tools 的 resolve_tool 挡住（它是这条链上唯一带租户+归属的
    # 一环），所以调不动。但「调不动」不等于没事：该泄的在下发时就泄完了。
    #
    # 三层可见性，跟 bind_skill 逐条对齐：
    #   全局平台工具   tenant='default' + platform
    #   本租户共享的   tenant=actor    + platform
    #   自己私有的     tenant=actor    + user + owner_user_id=actor
    #
    # 停用的也不能绑：绑上了也不会下发（internal.py 现在按 Tool.enabled 过滤），
    # 那是个只会让人困惑的状态。
    tool = (
        db.query(Tool)
        .filter(
            Tool.id == body.tool_id,
            Tool.enabled.is_(True),
            # 三段式可见性只有一份实现，见 models/tool.py::visible_to。
            # 以前这里是内联的一段 or_/and_，而 /internal/resolve-attachments 需要
            # 同一条规则 —— 各写一遍正是 bind_tool 当初整个漏掉校验的原因。
            *visible_to(actor.tenant_id, actor.user_id),
        )
        .first()
    )
    if tool is None:
        # 404 而不是 403 —— 403 会确认「这个 tool_id 存在」，而枚举 id 正是这个洞
        # 最省事的用法。跟本文件里其他跨归属的分支保持一致。
        raise HTTPException(404, "tool not found")

    # 重复绑定当成幂等，别 500 在唯一索引上。跟 bind_skill 同形：已存在就把它打开。
    existing = (
        db.query(AgentToolBinding)
        .filter_by(agent_id=agent_id, tool_id=body.tool_id)
        .first()
    )
    if existing:
        existing.enabled = body.enabled
        db.commit()
        db.refresh(existing)
        return {c.name: getattr(existing, c.name) for c in existing.__table__.columns}

    binding = AgentToolBinding(agent_id=agent_id, **body.model_dump())
    db.add(binding)
    db.commit()
    db.refresh(binding)
    return {c.name: getattr(binding, c.name) for c in binding.__table__.columns}


@router.delete("/{agent_id}/tools/{tool_id}")
def unbind_tool(
    agent_id: int,
    tool_id: int,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    agent = _get_agent_or_404(db, agent_id, actor)
    _require_mine(agent, actor)

    b = db.query(AgentToolBinding).filter_by(agent_id=agent_id, tool_id=tool_id).first()
    if not b:
        raise HTTPException(404, "Binding not found")
    db.delete(b)
    db.commit()
    return {"message": "已解绑"}


# -- MCP Server Bindings --
#
# 绑一个 MCP server = 用它同步出的**全部**工具行，粒度跟会话级挂载
# （session_attachments kind=mcp_server）一致。展开在 internal.py 组装 agent-config 时做，
# 与逐个 tool 绑（agent_tool_bindings）的结果按 tool id 去重合并。

@router.post("/{agent_id}/mcp")
def bind_mcp(
    agent_id: int,
    body: BindMcpBody,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    agent = _get_agent_or_404(db, agent_id, actor)
    _require_mine(agent, actor)

    # 可见性过滤跟 bind_tool 同源同理 —— 缺了它就是"猜个 id 把别人的 server 绑到自己
    # agent 上"，而 server 一绑就把它同步出的全部工具名/描述/schema 下发。三层可见性
    # 只有一份实现（models/mcp_server.py::visible_to），跟 /internal/resolve-attachments
    # 挂 server 那条用的是同一条规则。停用的 server 也不能绑（绑上也不下发）。
    server = (
        db.query(MCPServer)
        .filter(
            MCPServer.id == body.mcp_server_id,
            MCPServer.enabled.is_(True),
            *mcp_visible_to(actor.tenant_id, actor.user_id),
        )
        .first()
    )
    if server is None:
        # 404 而非 403：不确认"这个 id 存在"，跟本文件其他跨归属分支一致。
        raise HTTPException(404, "mcp server not found")

    existing = (
        db.query(AgentMcpBinding)
        .filter_by(agent_id=agent_id, mcp_server_id=body.mcp_server_id)
        .first()
    )
    if existing:
        existing.enabled = body.enabled
        db.commit()
        db.refresh(existing)
        return {c.name: getattr(existing, c.name) for c in existing.__table__.columns}

    binding = AgentMcpBinding(agent_id=agent_id, **body.model_dump())
    db.add(binding)
    db.commit()
    db.refresh(binding)
    return {c.name: getattr(binding, c.name) for c in binding.__table__.columns}


@router.delete("/{agent_id}/mcp/{mcp_server_id}")
def unbind_mcp(
    agent_id: int,
    mcp_server_id: int,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    agent = _get_agent_or_404(db, agent_id, actor)
    _require_mine(agent, actor)

    b = (
        db.query(AgentMcpBinding)
        .filter_by(agent_id=agent_id, mcp_server_id=mcp_server_id)
        .first()
    )
    if not b:
        raise HTTPException(404, "Binding not found")
    db.delete(b)
    db.commit()
    return {"message": "已解绑"}


# ── All Bindings List (从旧 bindings.py 迁入) ────────────

@router.get("/{agent_id}/bindings")
def list_bindings(
    agent_id: int,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    _get_agent_or_404(db, agent_id, actor)
    skills = db.query(AgentSkillBinding).filter_by(agent_id=agent_id).all()
    kbs = db.query(AgentKbBinding).filter_by(agent_id=agent_id).all()
    tools = db.query(AgentToolBinding).filter_by(agent_id=agent_id).all()
    mcp = db.query(AgentMcpBinding).filter_by(agent_id=agent_id).all()

    def _row_to_dict(obj):
        return {c.name: getattr(obj, c.name) for c in obj.__table__.columns}

    return {
        "skills": [_row_to_dict(s) for s in skills],
        "knowledge_bases": [_row_to_dict(k) for k in kbs],
        "tools": [_row_to_dict(t) for t in tools],
        "mcp_servers": [_row_to_dict(m) for m in mcp],
    }
