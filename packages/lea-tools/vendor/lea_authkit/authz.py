"""用户/管理员授权 —— Actor（你是谁）+ can()（你能对这个对象做什么）。

跟 internal_auth.py 是**两层不同的东西**，别混：

    internal_auth   服务间认证：你是不是我们自己的服务（共享密钥）
    本模块          用户授权：这个人能不能对这个对象做这件事

## 为什么授权必须在这里，不能在网关

网关手上没有对象。它能回答"这个人是不是管理员"，回答不了"这个知识库属不属于他"。
多租户里存在的是**租户**管理员而不是全局管理员，所以在网关上加一道
`roles 含 admin` 的门只是把越权上移一层 —— 租户 A 的管理员照样拿到租户 B 的数据。

对象级判断必须先把对象取出来才做得了，所以它只能发生在紧挨查询的地方。这也是
"要不要一个公用鉴权服务"的答案：公用的是**认证**和**判断的写法**，判断本身公用不了。
（真要一个服务，正确形态是策略引擎 —— OPA / Cedar / OpenFGA —— 因为服务把
"用户 + 动作 + 具体对象"一起问过去。只回答"这张票真不真"的服务做不了这件事。）

## 两个正交的轴

    owner_scope / owner_user_id   谁能**改**（可变性）
    visibility                    谁能**看**（可见性）

压成一个的后果是表达不了"我创建的，但发布给全租户看" —— skills.scope 现在就是
压在一起的（private 是归属，system/public 是可见性），所以那一格它表达不了。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import structlog
from fastapi import Header, HTTPException

from lea_authkit.internal_auth import _configured_token

logger = structlog.get_logger()

DEFAULT_TENANT_ID = "default"

# ── 归属 ──────────────────────────────────────────────────────────────────

OWNER_SCOPE_PLATFORM = "platform"
OWNER_SCOPE_USER = "user"

VISIBILITY_PRIVATE = "private"
VISIBILITY_PUBLIC = "public"
VISIBILITY_SHARED = "shared"
"""同租户内所有人可读，跨租户不可见。目前只有 knowledge_bases 用这个值 ——
kb_service.py 的 list_kbs() 早就按它过滤了，但 can()/scoped_filter() 一直没跟上，
于是"真正用户会经过"的 server_user_kb.py（走 scoped_filter，不走 kb_service）
把 shared 库当成了私有库：非 owner 的同租户用户什么都看不到。"""

# ── 角色 ──────────────────────────────────────────────────────────────────

ROLE_ADMIN = "admin"
"""**租户**管理员。库里现有的字符串（api/*.py 里出现的就是这个）。

只在自己租户内有效 —— 它**不是**跨租户权限。这是最容易搞错的一点。
"""

ROLE_PLATFORM_ADMIN = "platform_admin"
"""跨租户管理员。仅接受由 lea-agent 从已验签 JWT 转发的声明。"""

# 租户管理员能不能读用户的私有资源？这里按**不能**（最小权限）。
#
# 这是产品决策不是技术决策：合规审计场景可能需要能看。但那种需求的正确形态是一个
# 独立的、带审计的接口，不是让 /knowledge-bases 这种列表接口对管理员静默返回更多
# —— 后者的问题是从响应上分不出"这是我的"和"这是我在替别人看"。
TENANT_ADMIN_READS_PRIVATE = False


# ── Action ────────────────────────────────────────────────────────────────

READ = "read"
UPDATE = "update"
DELETE = "delete"
ACTIONS = (READ, UPDATE, DELETE)

# CREATE 故意不在这里：建东西的时候还没有对象可判断，能不能建是接口层面的事
# （比如"用户能不能建工具" —— 那是产品有没有这个功能的问题）。
# 硬塞进 can() 只会让它多一个 obj=None 的分支。


@dataclass(frozen=True)
class Actor:
    """一个**已经验明**的调用者。

    构造它的唯一正当途径是 require_actor()。手搓一个 Actor 等于绕过认证 ——
    测试里那么做没问题，业务代码里不要。
    """

    user_id: str
    tenant_id: str
    roles: tuple[str, ...] = field(default=())

    @property
    def is_tenant_admin(self) -> bool:
        return ROLE_ADMIN in self.roles or self.is_platform_admin

    @property
    def is_platform_admin(self) -> bool:
        return ROLE_PLATFORM_ADMIN in self.roles


@dataclass(frozen=True)
class Ownership:
    """从一个 ORM 对象里抽出授权需要的四个事实。

    存在的理由是**同一个概念在库里有四种叫法**：

        agents / skills   owner_scope + owner_user_id（004 迁移后）+ created_by（审计）
        mcp_servers       owner_scope + owner_user_id
        knowledge_bases   owner + visibility            （在 lea-rag，命名不同）
        tools             004 之前没有

    命名统一之后这个适配器就可以塌成直接读属性。在那之前，把兼容逻辑集中在一处
    比让十几个调用点各自 getattr 要好 —— 后者漏一个就是一个静默放行。
    """

    tenant_id: str
    scope: str
    owner_user_id: str | None
    visibility: str = VISIBILITY_PRIVATE

    @classmethod
    def of(cls, obj: Any) -> Ownership:
        tenant_id = getattr(obj, "tenant_id", None) or DEFAULT_TENANT_ID

        scope = getattr(obj, "owner_scope", None)
        owner = getattr(obj, "owner_user_id", None)

        if scope is None:
            # knowledge_bases 那套：有 owner 字段但没有 scope。
            # owner 非空 = 用户私有，为空 = 平台的。
            legacy_owner = getattr(obj, "owner", None)
            if legacy_owner:
                scope, owner = OWNER_SCOPE_USER, legacy_owner
            else:
                scope, owner = OWNER_SCOPE_PLATFORM, None

        # ⚠️ 故意**不** fallback 到 created_by。它是审计字段：平台预置资源的
        # created_by 是某个管理员的 user_id，但语义是"租户内共享"。拿它当归属会
        # 让那个管理员变成一堆共享资源的私有主人，别人反而改不了。
        visibility = getattr(obj, "visibility", None) or VISIBILITY_PRIVATE

        return cls(
            tenant_id=tenant_id,
            scope=scope,
            owner_user_id=owner,
            visibility=visibility,
        )


def can(actor: Actor, action: str, obj: Any) -> bool:
    """actor 能不能对 obj 做 action。

    obj 可以是 ORM 对象也可以是 Ownership。规则从严到宽：
    """
    if action not in ACTIONS:
        raise ValueError(f"未知 action: {action!r}（可选 {ACTIONS}）")

    own = obj if isinstance(obj, Ownership) else Ownership.of(obj)

    # 1. 平台管理员：跨租户，全通。目前没人有这个角色。
    if actor.is_platform_admin:
        return True

    # public 是真正的跨租户公开读。必须在租户边界之前判断；否则把资源发布到
    # default/public 后，其他租户仍会在下面直接返回 False，形成“发布成功但不可见”。
    if action == READ and own.visibility == VISIBILITY_PUBLIC:
        return True

    # 2. 跨租户：一律拒绝。放在最前面 —— 后面所有规则都隐含"同租户"这个前提，
    #    顺序反了的话"我是自己资源的 owner"会跨租户成立（user_id 在两个租户里
    #    可以是同一个字符串，seed 数据就是故意这么造的）。
    if own.tenant_id != actor.tenant_id:
        return False

    # 3. 自己的东西：全通。
    if own.scope == OWNER_SCOPE_USER and own.owner_user_id == actor.user_id:
        return True

    # 4. 平台/租户共享的资源：所有人可读，只有租户管理员可改。
    if own.scope == OWNER_SCOPE_PLATFORM:
        return True if action == READ else actor.is_tenant_admin

    # 5. 到这里 = 同租户里**别人**的私有资源。
    if action == READ:
        # public 是跨租户也能看的公开；shared 是同租户内共享 —— 到这一行时已经
        # 过了第 2 条的跨租户检查，两者在这里都成立。注意这不给写权限：
        # 把 visibility 当可变性用是个常见错误，改/删仍然只认 owner 或租户管理员。
        if own.visibility in (VISIBILITY_PUBLIC, VISIBILITY_SHARED):
            return True
        return actor.is_tenant_admin and TENANT_ADMIN_READS_PRIVATE

    return False


def require_admin(actor: Actor) -> None:
    """管理员端点：不是租户管理员（或平台管理员）就 403。

    ── 为什么这个跟 can() 是两件事 ──────────────────────────────────────

    can(actor, action, obj) 问的是"这个对象归你吗"。管理员操作的前提恰恰相反 ——
    对象**不**归他个人，但他管这个租户。所以那些端点需要的是角色门，不是归属判断。

    ── 光有租户过滤是不够的 ────────────────────────────────────────────

    这个错误已经犯过一次：lea-rag 的 server_admin_kb.py 把 tenant_id 从 query 参数
    改成从 actor 取（堵住了跨租户），但没加角色检查 —— 于是普通用户 alice 可以
    DELETE /admin/knowledge-bases/{id} 删掉同租户里 bob 的库。那个文件甚至
    import 了 ROLE_ADMIN 却一次没用。

    两道检查缺一不可：租户过滤挡跨租户，角色门挡租户内的普通用户。

    ⚠️ 这道门建立在 X-User-Role 头上，而那个头目前**客户端可填**
    （见 _TrustedHeaderActor 的说明）。lea-agent 的反代现在只重写
    X-User-ID / X-Tenant-ID，**不重写 X-User-Role** —— 所以它今天真正的强度来自
    "只有 lea-agent 能打到这个端口"。补上那个重写之后这道门才自足。
    """
    if not actor.is_tenant_admin:
        logger.warning(
            "authz_admin_required",
            user_id=actor.user_id,
            tenant_id=actor.tenant_id,
            roles=list(actor.roles),
        )
        raise HTTPException(status_code=403, detail="Admin access required")


def ensure_tenant(actor: Actor, resource_tenant_id: str) -> None:
    """跨租户返 404（不是 403 —— 403 会确认这个 id 存在）。

    对象已经在手上时用 require()，它同时判归属。这个是给"只有 tenant_id 没有
    完整对象"的场合用的。
    """
    if not actor.is_platform_admin and actor.tenant_id != resource_tenant_id:
        raise HTTPException(status_code=404, detail="Not found")


def require(actor: Actor, action: str, obj: Any) -> None:
    """can() 不成立就抛 HTTP 异常。

    403 和 404 的区分是有意的：**跨租户返 404，同租户越权返 403。**

    跨租户用 403 会确认"这个 id 存在"，那是一个跨租户的存在性预言 —— 对方连
    这个对象存不存在都不该知道。同租户内 403 是可以接受的：那个 id 本来就在
    调用方能触及的范围里，而且"这不是你的"这句话本身就得说出来，否则用户会
    对着一个 404 反复重试。
    """
    own = obj if isinstance(obj, Ownership) else Ownership.of(obj)

    if own.tenant_id != actor.tenant_id and not actor.is_platform_admin:
        raise HTTPException(status_code=404, detail="not found")

    if not can(actor, action, own):
        raise HTTPException(status_code=403, detail="forbidden")


# ── 身份来源 ──────────────────────────────────────────────────────────────


class _TrustedHeaderActor:
    """从 X-User-ID / X-Tenant-ID / X-User-Role 取身份，**缺了就 401**。

    ── 跟现状的区别 ──────────────────────────────────────────────────────

    现有代码（lea_rag/server_user_kb.py:32、server_user_memory.py:24、
    lea_config/core/deps.py:36）缺头时返回 ("anonymous", "default")。后果是所有
    无头请求共享**同一个身份**，于是它们互相看得见对方的资源 —— 而且这是静默的，
    没有任何一条日志说"我没认出这是谁"。

    ── 为什么敢信 HTTP 头 ────────────────────────────────────────────────

    因为 lea-agent 的反代会用验过的 JWT **覆盖**这三个头，值为空时 Del 掉
    （internal/router/proxy.go 的 UserAgentsProxy）。也就是说安全性依赖两件事：

        1. 只有 lea-agent 能打到这个端口   ← 靠 internal_auth + NetworkPolicy
        2. lea-agent 一定重写这三个头       ← 靠 proxy.go，proxy_test.go 钉住

    第 2 条一度只覆盖 X-User-ID / X-Tenant-ID，**X-User-Role 是原样转发的** ——
    于是浏览器加一个 X-User-Role: admin 就通过了下游的 require_admin()。
    现在三个头都走 Set-或-Del，且角色按白名单下发（proxy.go 的 proxiedRoles）。

    第 1 条是**这个模式成立的前提**，所以下面把它变成一道硬检查：header 模式下
    没开服务间鉴权就拒绝服务。不然就是"谁都能自称任何人"，比 anonymous 兜底更糟。

    ── 这是过渡形态 ──────────────────────────────────────────────────────

    正规做法是验一个签过名的东西：把用户 JWT 传下来各自验签，或者网关签发一个
    短时效的内部身份断言（RFC 8693 token exchange）。那时候上面第 1、2 条前提
    都不需要了 —— 身份自己可验证。换过去只需要替换这个类。
    """

    async def __call__(
        self,
        x_user_id: str | None = Header(None, alias="X-User-ID"),
        x_tenant_id: str | None = Header(None, alias="X-Tenant-ID"),
        x_user_role: str | None = Header(None, alias="X-User-Role"),
    ) -> Actor:
        if not _configured_token() and not _allow_untrusted_headers():
            # 没有服务间鉴权 = 这个端口谁都能打 = 这几个头谁都能填。
            # 这时候"认出身份"是假的，宁可整条接口不可用。
            logger.error(
                "authz_header_mode_without_internal_auth",
                detail="LEA_INTERNAL_TOKEN 未配置，X-User-ID 头不可信，拒绝服务。"
                       "本地调试设 LEA_AUTHZ_ALLOW_UNTRUSTED_HEADERS=1。",
            )
            raise HTTPException(
                status_code=503,
                detail="identity source not trustworthy",
            )

        if not x_user_id or not x_tenant_id:
            # 401 而不是落到 anonymous —— 见类 docstring。
            raise HTTPException(status_code=401, detail="missing identity")

        # X-User-Role 的白名单。这个头只能由 lea-agent 从已验签 JWT 覆盖后转发；
        # 服务间令牌和 NetworkPolicy 保证客户端不能直连本服务伪造它。
        #
        # 上游 lea-agent 的反代现在也按白名单下发（proxy.go 的 proxiedRoles，只有
        # admin），所以这里是**第二道**同样的门。看着冗余，但两侧独立成立才有意义：
        # 任一侧被误改（反代加了个角色、或者有人在这里放宽）都不会立刻破防。
        # 另外 lea-tools/api/execute.py:35 那处仍然是 Header("user") 默认值 + 客户端
        # 可填，它走的是自己的 check_permission 而不是这里。收拢那批 /admin/* 时一并改。
        roles: tuple[str, ...] = ()
        if x_user_role:
            role = x_user_role.strip().lower()
            if role in (ROLE_ADMIN, ROLE_PLATFORM_ADMIN):
                roles = (role,)

        return Actor(user_id=x_user_id, tenant_id=x_tenant_id, roles=roles)


def scoped_filter(actor: Actor, model: Any) -> Any:
    """返回一个 SQLAlchemy 条件，把查询限制在 actor **可读**的范围内。

        q = db.query(KnowledgeBase).filter(scoped_filter(actor, KnowledgeBase))
        total = q.count()          # ← 这个数是对的
        rows  = q.offset(...).limit(...).all()

    ── 为什么必须在查询里，不能逐行 can() 过滤 ──────────────────────────

    逐行过滤有两个具体的坏处，都不是洁癖：

      1. **分页会错。** count() 数的是过滤前的行数，于是"共 50 条"但翻到第 3 页
         只剩 2 条。更糟的是每页返回的条数不一样，前端的"加载更多"逻辑会乱。
      2. **把越权变成了性能问题。** 数据库先把别人的行捞出来再在 Python 里丢掉 ——
         一个漏掉过滤的分支就是一次真实泄漏，而且捞出来的行已经进过日志和 ORM
         身份映射了。

    ── 命名适配 ────────────────────────────────────────────────────────

    跟 Ownership.of() 同一个问题的类级版本（那个看实例属性，这个看表列）。
    命名统一之后两个都能塌掉。
    """
    from sqlalchemy import and_, or_, true

    cols = model.__table__.columns

    # 平台管理员先处理：**无任何约束**。
    #
    # 必须在这里返回，不能走下面那套 clause 再"跳过 tenant 约束" —— 那些 clause 里
    # 含 owner_user_id == actor.user_id，对平台管理员而言等于"平台资源 + 我自己的"，
    # 于是他反而看不到别人的东西，跟 can() 的语义正好矛盾。写过一次这个 bug，
    # 被 test_scoped_filter_platform_admin_crosses_tenants 抓到。
    if actor.is_platform_admin:
        return true()

    clauses = []
    if "owner_scope" in cols:
        clauses.append(model.owner_scope == OWNER_SCOPE_PLATFORM)
        clauses.append(
            and_(
                model.owner_scope == OWNER_SCOPE_USER,
                model.owner_user_id == actor.user_id,
            )
        )
    elif "owner" in cols:
        # knowledge_bases 那套：owner 为空 = 平台的。
        clauses.append(model.owner.is_(None))
        clauses.append(model.owner == "")
        clauses.append(model.owner == actor.user_id)
    else:
        # 表上没有任何归属信息（004 迁移之前的 tools 就是）。
        # **不静默放行** —— 那是"忘了加列"和"这张表本来就人人可读"的混淆，
        # 而前者是个洞。要人人可读就显式传一个 Ownership 进 can()。
        raise TypeError(
            f"{model.__name__} 没有归属列（owner_scope/owner_user_id 或 owner），"
            "没法安全地按 actor 过滤。先给它加上归属，见 migrations/004。"
        )

    public_clause = None
    if "visibility" in cols:
        public_clause = model.visibility == VISIBILITY_PUBLIC
        clauses.append(public_clause)
        # shared：同租户内所有人可读。不用单独判 tenant_id —— 最外层的
        # and_(model.tenant_id == actor.tenant_id, ...) 已经把它卡死了。
        clauses.append(model.visibility == VISIBILITY_SHARED)

    if TENANT_ADMIN_READS_PRIVATE and actor.is_tenant_admin:
        # 租户管理员看全租户。注意仍然被下面的 tenant_id 约束夹着。
        clauses = [model.tenant_id == actor.tenant_id]

    scope_clause = or_(*clauses)

    if "tenant_id" not in cols:
        raise TypeError(f"{model.__name__} 没有 tenant_id，无法做租户隔离")

    tenant_scope = and_(model.tenant_id == actor.tenant_id, scope_clause)
    if public_clause is not None:
        return or_(tenant_scope, public_clause)
    return tenant_scope


def _allow_untrusted_headers() -> bool:
    return os.getenv("LEA_AUTHZ_ALLOW_UNTRUSTED_HEADERS", "").strip().lower() in (
        "1", "true", "yes",
    )


require_actor = _TrustedHeaderActor()
"""FastAPI 依赖：拿到一个验明的 Actor，拿不到就 401/503。

    from lea_authkit import Actor, require_actor, require, UPDATE

    @router.put("/{kb_id}")
    def update_kb(kb_id: int, db=Depends(get_db), actor: Actor = Depends(require_actor)):
        kb = db.get(KnowledgeBase, kb_id)
        if kb is None:
            raise HTTPException(404)
        require(actor, UPDATE, kb)      # 跨租户 404，同租户越权 403
        ...

⚠️ 列表接口不能靠 can() 逐条过滤 —— 那样分页会算错（总数是过滤前的），而且把
   越权变成了性能问题。列表要在**查询里**带上约束，见 scoped_query()。
"""
