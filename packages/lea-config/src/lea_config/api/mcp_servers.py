"""MCP Server CRUD 路由 — platform 级 MCP 配置。

统一 /mcp-servers 路径，Auth 控制权限。

## 为什么 CRUD 在 lea-config 而执行在 lea-tools

分工不是按资源切的，是按职责切的 —— `tools` 表也是这个形状（CRUD 在这里，
`/tools/execute` 在 lea-tools）。lea-tools 不拥有这两张表，它只读。

直接原因见 `lea_tools/api/admin_mcp.py` 的文件头：那边以前有四组 `/admin/*`，四组
都没有授权，`tenant_id` 从请求体取 —— 等于调用方自选租户。而 `require_actor` /
`scoped_filter` / `require_admin` 都在 lea-config 这一侧；lea-tools 的调用方是引擎
不是浏览器，它**没有 actor 概念**，放开用户私有 MCP 之后判断不了「这是不是你的」。

还留在 lea-tools 的只有 `/sync` —— 它是唯一一个写配置数据的例外，P1 搬过来。

## 一个 server 不是一个工具

这里管的是**连接**。工具是 `/sync` 连上去问 `tools/list` 之后生成的 `tools` 行
（`execution_type='mcp'`，`mcp_server_id` 指回来）。所以删一个 server 必须连带删
那些工具行 —— 见下面的 delete。
"""

from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from lea_authkit import Actor, internal_headers, require_actor
from lea_config.api._auth_helpers import require_admin
from lea_config.database import get_db
from lea_config.models.mcp_server import (
    MCP_SCOPE_PLATFORM,
    MCP_SCOPE_USER,
    MCP_STATUS_UNKNOWN,
    MCP_TRANSPORT_STREAMABLE_HTTP,
    MCP_TRANSPORT_VALUES,
    MCPServer,
    visible_to,
)
from lea_config.models.tool import Tool

router = APIRouter(prefix="/mcp-servers", tags=["mcp-servers"])


class MCPServerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = ""
    category: str = Field(default="general", min_length=1, max_length=50)
    # transport 有唯一合法值，所以给默认值 —— 调用方不该被迫填一个只有一个选项的字段。
    # 仍然保留这个字段是为了让「以后会有别的传输方式」这件事在契约上可见。
    transport: str = Field(default=MCP_TRANSPORT_STREAMABLE_HTTP)
    # endpoint 必填。这是这轮修的核心：以前它压根不在创建契约里，于是**每一行建出来
    # 的 endpoint 都是 NULL**，而执行侧读的就是它 —— sync 和调用两条路一起断。
    endpoint: str = Field(min_length=1, max_length=500)
    auth_config: Optional[dict] = None
    tool_filter: Optional[list[str]] = None
    enabled: bool = True


class MCPServerUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = Field(None, min_length=1, max_length=50)
    transport: Optional[str] = None
    endpoint: Optional[str] = Field(None, min_length=1, max_length=500)
    auth_config: Optional[dict] = None
    tool_filter: Optional[list[str]] = None
    enabled: Optional[bool] = None


class MCPServerEnable(BaseModel):
    enabled: bool


def _to_dict(s: MCPServer, actor: Actor) -> dict:
    """行 → 响应。

    ⚠️ **auth_config 不出现在响应里**，只给一个布尔。它装的是发给远端的 bearer token
    或 api key；列表接口对整个租户可见，把 key 放进去等于任何能列 server 的人都能拿到
    它。写得进去、读不出来，跟 tool_credentials 的取向一致。
    """
    can_edit = (
        s.owner_user_id == actor.user_id
        if s.owner_scope == MCP_SCOPE_USER
        else actor.is_platform_admin
        or (actor.is_tenant_admin and s.tenant_id == actor.tenant_id)
    )
    result = {
        "id": s.id,
        "tenant_id": s.tenant_id,
        "name": s.name,
        "description": s.description or "",
        "category": s.category or "general",
        "owner_scope": s.owner_scope,
        # owner_user_id 以前不在响应里。放开用户私有 server 之后必须给 —— 否则前端
        # 分不出"这是我建的"和"平台建的"，而这两者能不能改是不一样的（_can_edit）。
        # 它不是敏感信息：同一个租户里谁建了什么本来就互相可见（列表里就有）。
        "owner_user_id": s.owner_user_id,
        "transport": s.transport,
        "endpoint": s.endpoint or "",
        "has_auth": bool(s.auth_config),
        "tool_filter": s.tool_filter or [],
        "enabled": s.enabled,
        "connection_status": s.connection_status,
        "last_connected_at": s.last_connected_at.isoformat() if s.last_connected_at else None,
        "last_sync_at": s.last_sync_at.isoformat() if s.last_sync_at else None,
        "last_error": s.last_error or "",
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }
    result["publication_status"] = (
        "draft"
        if s.owner_scope == MCP_SCOPE_USER
        else ("active" if s.enabled else "suspended")
    )
    result["allowed_actions"] = ["read"]
    if can_edit:
        result["allowed_actions"].extend(["update", "delete", "run_check", "sync"])
    if s.owner_scope == MCP_SCOPE_USER and can_edit:
        result["allowed_actions"].append("submit_publication")
    return result


def _validate_transport(t: str) -> None:
    if t not in MCP_TRANSPORT_VALUES:
        raise HTTPException(400, f"transport must be one of {MCP_TRANSPORT_VALUES}")


def _validate_endpoint(url: str) -> None:
    """只收 http/https 的绝对 URL。

    ⚠️ 这里**只挡 scheme**，挡不住 `foo.example.com` 解析到 10.0.0.5，也挡不住 DNS
    在写入之后才改指向内网。真正的出网防护在 lea-tools 的 executor/url_guard.py ——
    它在每次 /test /sync /调用之前查，那才是唯一有意义的时点。
    这一道留着是为了让明显写错的 URL 在**创建时**就被拒，而不是等到第一次调用。
    """
    if not url.startswith(("http://", "https://")):
        raise HTTPException(400, "endpoint must be an absolute http(s) URL")


def _load_visible(db: Session, actor: Actor, mcp_id: int) -> MCPServer:
    """按 id 取一个 actor 看得见的 server，看不见一律 404。

    404 而不是 403：403 会确认「这个 id 存在」，而跨租户/别人的私有 server 不该给出
    这个信息。跟 api/skills.py::_load_visible 同一个取向。

    管理员在这里**不放大可见范围**，跟 skills 那边不同 —— 那边租户管理员要审核上架
    申请，所以得看得见别人的私有 skill。MCP 没有上架流程，管理员没有"必须看见别人
    私有 server"的理由，而它装着别人的 api key。
    """
    s = (
        db.query(MCPServer)
        .filter(MCPServer.id == mcp_id, *visible_to(actor.tenant_id, actor.user_id))
        .first()
    )
    if s is None:
        raise HTTPException(404, "MCP server not found")
    return s


def _require_can_edit(s: MCPServer, actor: Actor) -> None:
    """能不能改这一行。

    平台级的要管理员（跟放开之前的行为一致）；用户私有的只有主人自己能改 ——
    **管理员也不能**，理由同 _load_visible：那里面有别人的凭证。

    ⚠️ 判据是 owner_user_id 相等，不是"是不是管理员"。写成后者的话租户管理员能改掉
    任何人的 server endpoint，而 endpoint 决定请求发到哪 —— 等于能把别人的 key 转发
    到自己的地址。
    """
    if s.owner_scope == MCP_SCOPE_USER:
        if s.owner_user_id != actor.user_id:
            raise HTTPException(404, "MCP server not found")
        return
    # 平台级
    require_admin(actor)
    if not actor.is_platform_admin and s.tenant_id != actor.tenant_id:
        raise HTTPException(404, "MCP server not found")


async def _discover_remote_tools(s: MCPServer) -> dict:
    """内部调 lea-tools 去问远端有哪些工具。**出网发生在那一侧，不在这里。**

    ## 为什么绕这一跳

    「哪个服务允许往用户填的地址发请求」是一条安全边界，越窄越好。本服务装着所有租户
    的配置（包括 llm_providers.api_key），让它能打任意 URL 才是真的把配置面暴露给
    SSRF —— 所以出网留在 lea-tools，它本来就是那个"允许出网"的服务。

    加 httpx 依赖之前本服务一次 HTTP 都不发。加了之后 egress 仍然可以在 NetworkPolicy
    里锁到"只允许 lea-tools"，**不要**让它演变成"什么都能打"。

    ## 传的是 endpoint 而不是 server_id

    这样 lea-tools 那一跳查不到也改不了任何一行 —— 它连租户是什么都不知道，没有"调用方
    自选租户"的余地。代价是 auth_config 在服务间传一次（内网 + internal_auth）。

    ## 连不通不抛异常

    返回 {"ok": False, "error": ...}，让调用方决定怎么表达。调用方要做两件事：把错误
    写到那一行上（用户在列表里看得见），然后返 502。分开的理由是"发现失败"和"同步失败"
    在语义上不是一回事，只有调用方知道它在做哪一件。
    """
    import httpx

    base = os.getenv("LEA_TOOLS_URL", "http://localhost:8400").rstrip("/")
    payload = {
        "endpoint": s.endpoint,
        "auth_config": s.auth_config,
        "transport": s.transport,
        "server_id": s.id,
    }

    # 60s：远端 MCP server 可能很慢，而这是个用户点了"同步"之后在等的动作。
    # 比 30s 宽是因为一次 tools/list 可能要远端自己去查一圈。
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                f"{base}/internal/mcp/discover",
                json=payload,
                headers=internal_headers(),
            )
    except httpx.HTTPError as e:
        # lea-tools 本身不可达 —— 这不是"远端 MCP 连不通"，是我们自己的服务出问题了。
        # 说清楚是哪一种，否则用户会以为自己的 URL 填错了。
        return {"ok": False, "error": f"工具网关不可达: {type(e).__name__}: {e}"}

    if r.status_code >= 400:
        # 400 是 lea-tools 那边拒绝了这个 endpoint（url_guard / transport）。它的
        # detail 是给人看的，原样带出去 —— 用户需要知道"你填的地址指向内网"这种事。
        detail = ""
        try:
            detail = (r.json() or {}).get("detail", "")
        except Exception:
            detail = r.text[:200]
        return {"ok": False, "error": detail or f"HTTP {r.status_code}"}

    return r.json()


@router.get("")
def list_servers(
    transport: Optional[str] = Query(None),
    enabled: Optional[bool] = Query(None),
    search: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    size: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    q = db.query(MCPServer).filter(*visible_to(actor.tenant_id, actor.user_id))
    if transport:
        q = q.filter(MCPServer.transport == transport)
    if enabled is not None:
        q = q.filter(MCPServer.enabled == enabled)
    if search:
        like = f"%{search}%"
        q = q.filter((MCPServer.name.ilike(like)) | (MCPServer.description.ilike(like)))
    total = q.count()
    rows = q.order_by(MCPServer.id).offset((page - 1) * size).limit(size).all()
    return {"total": total, "page": page, "size": size, "items": [_to_dict(s, actor) for s in rows]}


@router.get("/{mcp_id}")
def get_server(
    mcp_id: int,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    s = _load_visible(db, actor, mcp_id)
    return _to_dict(s, actor)


@router.post("", status_code=201)
def create_server(
    body: MCPServerCreate,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    require_admin(actor)
    _validate_transport(body.transport)
    _validate_endpoint(body.endpoint)
    existing = (
        db.query(MCPServer)
        .filter(
            MCPServer.tenant_id == actor.tenant_id,
            MCPServer.owner_scope == MCP_SCOPE_PLATFORM,
            MCPServer.owner_user_id.is_(None),
            MCPServer.name == body.name,
        )
        .first()
    )
    if existing:
        raise HTTPException(409, f"MCP server '{body.name}' already exists")
    s = MCPServer(
        tenant_id=actor.tenant_id,
        owner_scope=MCP_SCOPE_PLATFORM,
        owner_user_id=None,
        **body.model_dump(),
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return _to_dict(s, actor)


@router.post("/mine", status_code=201)
def create_my_server(
    body: MCPServerCreate,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    """建一个**自己的**私有 MCP server。任何登录用户都可以，不需要管理员。

    ## 为什么是独立端点而不是给 POST "" 加一个 owner_scope 字段

    那样的话「建成平台级」和「建成私有」共用一条路，而它们的授权完全不同（前者要
    管理员）。参数决定权限的形状很容易写错一次就变成越权 —— 漏判一个分支，普通用户
    传 owner_scope=platform 就建出了全租户可见的 server，而它装着他自己的 key。

    两条路各自只做一件事，授权写在入口，读代码的人不用去追一个字段的取值。

    ## 出网地址

    这里只挡 scheme（_validate_endpoint）。真正的内网防护在 lea-tools 的
    executor/url_guard.py，每次 /test /sync /调用之前查 —— 那才是唯一有意义的时点，
    因为 DNS 可以在写入之后才改。

    ⚠️ 所以**不要**因为"这里已经校验过了"就在那边放宽。
    """
    _validate_transport(body.transport)
    _validate_endpoint(body.endpoint)

    # 唯一约束是 (tenant_id, owner_scope, owner_user_id, name)，所以同名检查也要带上
    # 归属三元组 —— alice 和 bob 各有一个叫 notion 的 server 是正常的。
    existing = (
        db.query(MCPServer)
        .filter(
            MCPServer.tenant_id == actor.tenant_id,
            MCPServer.owner_scope == MCP_SCOPE_USER,
            MCPServer.owner_user_id == actor.user_id,
            MCPServer.name == body.name,
        )
        .first()
    )
    if existing:
        raise HTTPException(409, f"你已经有一个叫 '{body.name}' 的 MCP server")

    s = MCPServer(
        tenant_id=actor.tenant_id,
        owner_scope=MCP_SCOPE_USER,
        owner_user_id=actor.user_id,
        created_by=actor.user_id,
        **body.model_dump(),
    )
    db.add(s)
    db.commit()
    db.refresh(s)
    return _to_dict(s, actor)


@router.put("/{mcp_id}")
def update_server(
    mcp_id: int,
    body: MCPServerUpdate,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    s = _load_visible(db, actor, mcp_id)
    _require_can_edit(s, actor)
    data = body.model_dump(exclude_unset=True)
    if "transport" in data:
        _validate_transport(data["transport"])
    if "endpoint" in data:
        _validate_endpoint(data["endpoint"])
        # 换了地址就等于换了一个服务：之前那次连通结果不再代表任何东西。
        # 不清的话「connected」会一直挂在一个已经指向别处的行上。
        s.connection_status = MCP_STATUS_UNKNOWN
        s.last_connected_at = None
        s.last_error = None
    for k, v in data.items():
        setattr(s, k, v)
    db.commit()
    db.refresh(s)
    return _to_dict(s, actor)


@router.patch("/{mcp_id}/enable")
def toggle_enable(
    mcp_id: int,
    body: MCPServerEnable,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    s = _load_visible(db, actor, mcp_id)
    _require_can_edit(s, actor)
    s.enabled = body.enabled
    db.commit()
    db.refresh(s)
    return _to_dict(s, actor)


@router.delete("/{mcp_id}", status_code=204)
def delete_server(
    mcp_id: int,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    s = _load_visible(db, actor, mcp_id)
    _require_can_edit(s, actor)

    # 连带删掉从这个 server 同步进来的工具行。
    #
    # 不删的话留下一批 mcp_server_id 指向已经不存在的 server 的工具：它们仍然
    # enabled、仍然出现在工具列表里、仍然能被 agent 绑定，但一执行就找不到连接配置。
    # 是个静默的坏。
    #
    # 这段逻辑以前在 lea-tools 的 DELETE /admin/mcp-servers/{id} 里，那条路由连同
    # 另外三组裸奔的 /admin/* 一起删掉了（见 lea_tools/api/admin_mcp.py 的说明），
    # 所以清理责任移到这里。按 tenant_id + mcp_server_id 两个条件删 —— 只按后者的话，
    # 万一 id 在别的租户里被复用就会误删。
    db.query(Tool).filter(
        Tool.tenant_id == s.tenant_id,
        Tool.mcp_server_id == s.id,
    ).delete(synchronize_session=False)

    db.delete(s)
    db.commit()


@router.post("/{mcp_id}/sync")
async def sync_server_tools(
    mcp_id: int,
    db: Session = Depends(get_db),
    actor: Actor = Depends(require_actor),
):
    """把远端暴露的工具同步成 `tools` 行。**用户能同步自己的 server。**

    ## 为什么这个端点在这里，而出网在 lea-tools

    sync 是三件事，各自该归谁不一样（设计见 docs/02-技术方案/MCP_USER_SERVERS.md §4.6）：

        判断有没有资格同步    **本服务**   它有 actor，判断得了"这是不是你的"
        打远端问 tools/list   lea-tools    它有 egress 和凭证
        写 tools 行           **本服务**   那张表归它

以前三件都在 lea-tools 的 `/admin/mcp-servers/{id}/sync`。那在"只有管理员能建 server"
    的前提下是对的 —— 看一眼 `X-User-Role: admin` 就够了。放开用户私有 server 之后那个
    前提没了：判断"这是不是 alice 的"需要 actor，而 lea-tools 的调用方是引擎不是浏览器，
    它**没有 actor 概念**。于是实际状态变成「用户建得了自己的 server 但 sync 不了」。

    ## 为什么不把出网也搬过来

    第一版设计是那样，被推翻了：那会让本服务**开始打用户填的任意 URL**，而它今天一次
    外网都不打 —— NetworkPolicy 可以把 egress 锁死。「哪个服务允许往用户填的地址发请求」
    是一条安全边界，越窄越好。

    代价是多一跳、`auth_config` 在服务间传一次（走 internal_auth，内网）。

    ## 授权判据跟改这一行是同一条

    用 `_require_can_edit` 而不是另写一套：sync **会往库里写**（tools 行，而工具行里有
    mcp_server_id → 决定调用打到哪）。能不能 sync 和能不能改 endpoint 是同一个权限级别，
    分开写迟早不一致。
    """
    s = _load_visible(db, actor, mcp_id)
    _require_can_edit(s, actor)

    if not s.enabled:
        raise HTTPException(400, "MCP server is disabled")
    if not s.endpoint:
        raise HTTPException(400, "MCP server has no endpoint configured")

    result = await _discover_remote_tools(s)
    if not result.get("ok"):
        # 连不通：把错误写到那一行上（用户在列表里看得见），然后 502。
        #
        # 这里返 502 而 /internal/mcp/discover 返 200 —— 不矛盾：对那个端点来说"测出
        # 连不通"是成功完成了任务，对这个端点来说"同步"这件事确实失败了。
        s.connection_status = "error"
        s.last_error = (result.get("error") or "unknown")[:500]
        db.commit()
        raise HTTPException(502, f"连不上这个 MCP server: {s.last_error}")

    tool_filter = s.tool_filter or []
    synced: list[str] = []

    # 先把这个 server 现有的工具行捞出来，按名字索引。一次查询 —— 一个 server 出几十个
    # 工具是常态。
    existing = {
        t.name: t
        for t in db.query(Tool).filter(
            Tool.tenant_id == s.tenant_id,
            Tool.mcp_server_id == s.id,
        )
    }

    for rt in result.get("tools") or []:
        name = (rt or {}).get("name", "")
        if not name:
            continue
        if tool_filter and name not in tool_filter:
            continue

        # 前缀带 server_id：工具名只在单个 MCP server 内唯一，聚合多个必然撞（GitHub 和
        # Linear 都有 create_issue）。server_id 全局唯一，所以 alice 和 bob 各接一个
        # Notion 是两行 server、两批不撞的名字。
        full_name = f"mcp_{s.id}_{name}"

        # schema 落 schema_json —— **执行器和下发链路读的都是这一列**。写成 input_schema
        # 的话工具建得出来、列得出来，到了模型面前没有参数。
        schema = rt.get("inputSchema") or {"type": "object"}
        description = rt.get("description", "")

        if row := existing.get(full_name):
            row.schema_json = schema
            row.description = description
            row.enabled = True
            # 归属跟着重新对齐，**不能只在新建时设**。server 的归属会变（上架审核把私有
            # 改成 platform、或者收回），而工具行是它的投影：
            #
            #   上架后不同步  →  工具还挂在原主人名下，「上架了但别人看不到」
            #   收回后不同步  →  工具仍然全租户可见，**收回不生效**（安全问题）
            row.owner_scope = s.owner_scope
            row.owner_user_id = s.owner_user_id
        else:
            db.add(Tool(
                tenant_id=s.tenant_id,
                name=full_name,
                display_name=f"[MCP] {name}",
                description=description,
                execution_type="mcp",
                schema_json=schema,
                mcp_server_id=s.id,
                # 归属跟着 **server 那一行**走，不是跟着触发 sync 的人走：管理员帮 alice
                # 同步她的 server，工具也该归 alice。
                owner_scope=s.owner_scope,
                owner_user_id=s.owner_user_id,
                enabled=True,
            ))
        synced.append(full_name)

    # 远端已经没有的：删掉。不删的话留下一批调用必然失败的工具，而它们仍然出现在清单里、
    # 仍然能被绑定 —— 模型会去调一个上游已经下线的东西。
    #
    # 集合差集，**不是 delete-all-then-insert**：那个中间窗口里别的请求会读到一份空清单，
    # 表现是"工具偶尔全没了"。
    stale = [row for nm, row in existing.items() if nm not in set(synced)]
    for row in stale:
        db.delete(row)

    s.last_sync_at = datetime.utcnow()
    s.connection_status = "connected"
    s.last_connected_at = datetime.utcnow()
    s.last_error = None
    db.commit()

    return {
        "synced": len(synced),
        "tools": synced,
        "removed": [r.name for r in stale],
    }
