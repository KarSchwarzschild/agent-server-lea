"""MCP Server 连通性检查 + 工具同步。

## 这个文件为什么只剩这两个端点

lea-tools 以前有四组 `/admin/*` 路由（tools / permissions / credentials / mcp-servers），
四组**都没有任何授权**：没有 actor、没有租户过滤，而且 `tenant_id` 是从**请求体或
query 参数**取的 —— 调用方自己声明往哪个租户里写。

其中三组已经删掉：

    /admin/tools        lea-config 的 /tools 是有授权的那条路（归属模型 + require）
    /admin/permissions  没有任何调用方
    /admin/credentials  没有任何调用方，而它管的是 api_key_enc

MCP 的 CRUD 也在 lea-config（api/mcp_servers.py，带 require_admin + 租户约束），
这里只保留**要连远端**的那两个：`/test` 和 `/sync`。

⚠️ 以前这里写着「/sync 只能留在 lea-tools，因为连接池在本进程里」—— **那句是假的**。
`MCPSession` 里根本没有连接（三个字段），连接复用在共享的 httpx pool 里。真正的理由
是「出网这件事该留在网关」：lea-config 在管理面那一侧，不该由它去打用户填的任意 URL。

这两个端点仍然有一处不干净：`/sync` **往 `tools` 表写行，而那张表归 lea-config**。
正确的形状是 lea-config 出一个 `/mcp-servers/{id}/sync`（它有 actor，能判归属、能写
自己的表），把「连远端取清单」这一步内部调到这里。放开用户私有 MCP 时必须这么改 ——
否则三层可见性的规则要在两个服务里各写一遍。

## tenant_id 为什么必须从 server 行上取

同步出来的工具归**这个 MCP server 所属的租户**。server_id 是路径参数，租户是从库里
那一行读出来的，所以调用方没法指定「往哪个租户里写工具」—— 它只能指定「同步哪个
server」，而那个 server 属于谁已经在库里定了。

这跟「从请求体拿 tenant_id」的差别就是那个洞的全部：后者等于让调用方自选租户。
"""

from datetime import datetime

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from lea_tools.db import get_db
from lea_tools.db.models import OWNER_SCOPE_PLATFORM, MCPServer, Tool
from lea_tools.executor.mcp_proxy import MCPSession, check_transport

router = APIRouter(prefix="/admin/mcp-servers", tags=["admin-mcp"])


def _load_server(db: Session, server_id: int, tenant_id: str, role: str) -> MCPServer:
    """取一行可用的 server，或者抛出说清楚为什么不可用。

    抽出来是因为 /test 和 /sync 前置条件完全一样，而这些检查漏一条的后果都是
    「在一个不该连的东西上发起连接」。
    """
    if role.strip().lower() != "admin":
        # 两个端点都会往库里写（/test 写连通状态，/sync 还写 tools 行），
        # 而工具行里有 mcp_server_id → 决定调用打到哪。不是管理员就不能改。
        raise HTTPException(403, "admin access required")

    server = db.get(MCPServer, server_id)
    if not server:
        raise HTTPException(404, "MCP server not found")

    # 跨租户返 404 而不是 403 —— 403 会确认「这个 server_id 存在」。
    if server.tenant_id != tenant_id:
        raise HTTPException(404, "MCP server not found")

    if not server.enabled:
        # migrations/016 把所有搬不过来的历史行都停用了，所以这条也挡住它们。
        raise HTTPException(400, "MCP server is disabled")

    if reason := check_transport(server.transport):
        raise HTTPException(400, reason)

    if not server.endpoint:
        raise HTTPException(400, "MCP server has no endpoint configured")

    return server


def _record(server: MCPServer, error: str | None) -> None:
    """把这次连接的结果写回那三列。

    它们以前是**死列**：`api/mcp_servers.py` 的响应里读它们，但全仓没有一处写。
    于是 connection_status 永远是 'unknown'，前端只能显示一个永远不变的状态。
    """
    if error is None:
        server.connection_status = "connected"
        server.last_connected_at = datetime.utcnow()
        server.last_error = None
    else:
        server.connection_status = "error"
        # 截断：远端可能返回一整页 HTML（比如把 404 页面当响应），整段塞进 TEXT 列
        # 既没用又难看。前 500 字足够判断是什么错。
        server.last_error = error[:500]


@router.post("/{server_id}/test")
async def test_mcp_connection(
    server_id: int,
    x_tenant_id: str = Header(..., alias="X-Tenant-ID"),
    x_user_role: str = Header("user", alias="X-User-Role"),
    db: Session = Depends(get_db),
):
    """连一下看看通不通，**不写 tools 表**。

    跟 /sync 分开是因为它们回答的是两个问题：「这个地址配对了吗」和「把工具同步进来」。
    把前者绑在后者上，用户填错一个 URL 得先触发一次会写库的操作才知道。
    """
    server = _load_server(db, server_id, x_tenant_id, x_user_role)
    session = MCPSession(server.id, server.endpoint, server.auth_config)

    try:
        tools = await session.list_tools()
    except Exception as e:
        _record(server, f"{type(e).__name__}: {e}")
        db.commit()
        # 200 而不是 502：**请求本身成功了** —— 我们成功地测出了"连不通"。
        # 502 会让调用方分不清"检查这个动作失败了"和"检查结果是失败"。
        return {"ok": False, "error": server.last_error, "tool_count": 0}

    _record(server, None)
    db.commit()
    return {
        "ok": True,
        "tool_count": len(tools),
        "tools": [t.get("name", "") for t in tools],
    }


@router.post("/{server_id}/sync")
async def sync_mcp_tools(
    server_id: int,
    x_tenant_id: str = Header(..., alias="X-Tenant-ID"),
    x_user_role: str = Header("user", alias="X-User-Role"),
    db: Session = Depends(get_db),
):
    """把远端 MCP server 暴露的工具同步进 tools 表。

    ── 身份从哪来 ────────────────────────────────────────────────────────

    跟 execute.py 一样走 X-Tenant-ID / X-User-Role 头，由 lea-agent 的反代重写
    （proxy.go 三个头都 Set-或-Del，X-User-Role 按白名单下发）。这里**不**用
    lea_authkit.require_actor：那个依赖要求 X-User-ID，而本服务的调用方是引擎而
    不是浏览器，引擎不一定带用户 id。租户 + 角色够这个端点用。

    ⚠️ 因此这道门的强度来自"只有 lea-agent 能打到这个端口"（internal_auth +
    NetworkPolicy）。install_internal_auth 已经在 server.py 上挂了中间件。

    ── 幂等 + 并发安全 ───────────────────────────────────────────────────

    两个副本、或两个管理员，可能同时 sync 同一个 server。所以：

      · 按 (tenant_id, name) 查了再决定 update 还是 insert，不盲插
      · 「远端已经没有的工具」要删，但**不能先删后插** —— 那个中间窗口里别的请求
        会读到一份空的工具清单，表现是"工具偶尔全没了"。这里用集合差集，在同一个
        事务里只删该删的。
    """
    server = _load_server(db, server_id, x_tenant_id, x_user_role)
    session = MCPSession(server.id, server.endpoint, server.auth_config)

    try:
        remote_tools = await session.list_tools()
    except Exception as e:
        _record(server, f"{type(e).__name__}: {e}")
        db.commit()
        raise HTTPException(502, f"Failed to connect to MCP server: {e}")

    tool_filter = server.tool_filter or []
    synced: list[str] = []

    # 先把这个 server 现有的工具行捞出来，按名字索引。一次查询，避免每个远端工具
    # 各查一次 —— 一个 server 出几十个工具是常态。
    existing = {
        t.name: t
        for t in db.query(Tool).filter(
            Tool.tenant_id == server.tenant_id,
            Tool.mcp_server_id == server.id,
        )
    }

    for rt in remote_tools:
        name = rt.get("name", "")
        if not name:
            continue
        if tool_filter and name not in tool_filter:
            continue

        # 前缀带 server_id：工具名只在单个 MCP server 内唯一，聚合多个 server 时必然
        # 撞（GitHub 和 Linear 都有 create_issue）。server_id 本身唯一，所以 alice 和
        # bob 各接一个 Notion 是两行 server、两批不撞的名字。
        full_name = f"mcp_{server.id}_{name}"

        # schema 落 schema_json —— **执行器和下发链路读的都是这一列**。写成
        # input_schema 的话工具建得出来、列得出来、到了模型面前没有参数。
        # 那正是 lea-config → lea-agent 那条链上刚修完的同一个坑。
        schema = rt.get("inputSchema") or {"type": "object"}
        description = rt.get("description", "")

        if row := existing.get(full_name):
            row.schema_json = schema
            row.description = description
            row.enabled = True
        else:
            db.add(Tool(
                tenant_id=server.tenant_id,
                name=full_name,
                display_name=f"[MCP] {name}",
                description=description,
                execution_type="mcp",
                schema_json=schema,
                mcp_server_id=server.id,
                # 归属跟着 server 走。今天 server 只能是 platform 级（CRUD 硬过滤），
                # 放开用户私有之后这里要改成 server.owner_scope / owner_user_id ——
                # 而那正是「sync 该搬到有 actor 的那侧」的原因之一。
                owner_scope=OWNER_SCOPE_PLATFORM,
                enabled=True,
            ))
        synced.append(full_name)

    # 远端已经没有的：删掉。不删的话留下一批调用必然失败的工具，而它们仍然出现在
    # 清单里、仍然能被绑定 —— 模型会去调一个上游已经下线的东西。
    #
    # 集合差集，不是 delete-all-then-insert。
    stale = [row for name, row in existing.items() if name not in set(synced)]
    for row in stale:
        db.delete(row)

    server.last_sync_at = datetime.utcnow()
    _record(server, None)
    db.commit()
    return {
        "synced": len(synced),
        "tools": synced,
        "removed": [r.name for r in stale],
    }
