"""MCP Proxy —— 连外部 MCP Server，代理 tools/list 和 tools/call。

## 只支持 streamable_http

`stdio` = 在**本进程里 fork 子进程**。它以前是 `mcp_servers.transport` 的默认值，
但从来没实现过，所以是个死值。一旦实现，「用户能加 MCP」就等于「用户能在网关进程
里执行任意命令」。这条路由 migrations/016 在库层面关死（ENUM 只剩一个值），这里再
挡一道 —— 库约束防的是写入，这里防的是**存量脏数据**。

`sse` 是长连接，绑在某一个副本上。本服务是多副本的，下一次工具调用可能落到别的副本，
那边没有这条连接。要支持得引入会话亲和或连接注册表，为一个没人要的传输方式不值。

## 这里为什么没有连接池

以前有一个 `_sessions: dict[int, MCPSession]`，按 `server_id` 分键，进程内。删掉了，
理由不是「改进」是「它是错的」：

  1. **`MCPSession` 里根本没有连接** —— 只装了 (server_id, endpoint, auth_config)
     三个字段，每次调用还是去拿下面这个共享的 httpx pool。所以它缓存不了任何有价值
     的东西，只缓存了**凭证**。
  2. 缓存键（server_id）比数据的归属粒度粗。今天一行 server 只属于一个租户，最坏是
     用了过期凭证；**放开每用户 key 之后就是 alice 的 token 发 bob 的请求** ——
     跟「按名字查工具不带 user_id」是同一类 bug。
  3. 多副本下各副本的配置更新时间不一致，表现为「我改了 key，有时生效有时不生效」。

同一个事实还推翻了 admin_mcp.py 里那句「/sync 只能留在 lea-tools，因为连接池在本进程
里」—— 那不是连接池，那个端点可以随便搬。

真正的连接复用在 `_get_http_pool()`：httpx 的 keep-alive 按 host 复用 TCP 连接，
那才是这里唯一有意义的池，而且它不持有任何凭证。
"""

from __future__ import annotations

from typing import Any

import httpx

from lea_tools.executor.router import ToolCall, ToolResult, _get_http_pool

# 跟 lea-config 的 MCP_TRANSPORT_VALUES 是同一套值，**故意重新定义一遍**：lea-tools
# 是执行侧，它不依赖配置服务的包（两个服务分开部署）。跟 db/models.py 里
# OWNER_SCOPE_* 的处理一致。
SUPPORTED_TRANSPORTS = ("streamable_http",)


class MCPSession:
    """一次 MCP 调用需要的全部东西。**不要缓存它的实例**（见模块头）。

    每次调用现场构造，字段全部来自库里那一行 —— 这样「改了配置立刻生效」是结构上
    保证的，不是靠记得去失效某个缓存。构造成本是三次属性赋值。
    """

    def __init__(self, server_id: int, endpoint: str, auth_config: dict | None = None):
        self.server_id = server_id
        self.endpoint = endpoint.rstrip("/")
        self.auth_config = auth_config or {}

    def _headers(self) -> dict[str, str]:
        # MCP 规范要求 Accept 同时声明这两种 —— streamable_http 的服务端可以选择用
        # 单个 JSON 响应，也可以升级成 SSE 流。少一个的话严格实现的 server 会 406。
        # 我们只处理 JSON 那种（见 call_tool），但握手时必须都声明。
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if "bearer_token" in self.auth_config:
            headers["Authorization"] = f"Bearer {self.auth_config['bearer_token']}"
        elif "api_key" in self.auth_config:
            headers["X-API-Key"] = self.auth_config["api_key"]
        return headers

    async def _rpc(self, method: str, params: dict) -> dict:
        client = _get_http_pool()
        resp = await client.post(
            self.endpoint,
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            headers=self._headers(),
        )
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            err = data["error"]
            # JSON-RPC 的 error 是个对象；只取 message 会丢掉 code，而 code 是区分
            # 「方法不存在」和「参数错」的唯一依据。
            raise MCPError(f"{err.get('message', err)} (code={err.get('code')})")
        return data.get("result", {})

    async def call_tool(self, tool_name: str, params: dict[str, Any]) -> dict:
        result = await self._rpc("tools/call", {"name": tool_name, "arguments": params})

        # MCP 用 isError 表达「工具自己失败了」，跟传输层的错误是两回事。不认它的话
        # 一次失败的调用会被当成成功结果喂给模型 —— 模型会拿着一句错误信息当数据用。
        if result.get("isError"):
            texts = [c.get("text", "") for c in result.get("content", []) if isinstance(c, dict)]
            raise MCPError("\n".join(t for t in texts if t) or "tool reported an error")

        content = result.get("content", [])
        texts = [
            c.get("text", "")
            for c in content
            if isinstance(c, dict) and c.get("type") == "text"
        ]
        return {"content": "\n".join(texts) if texts else result}

    async def list_tools(self) -> list[dict]:
        return (await self._rpc("tools/list", {})).get("tools", [])


class MCPError(Exception):
    """远端明确报出来的错误（JSON-RPC error 或 isError）。

    单独一个类型，是为了把它跟「连不上」区分开：连不上要清连接状态、要提示检查
    地址；远端报错说明连接是好的，问题在调用本身。
    """


def check_transport(transport: str | None) -> str | None:
    """返回一句拒绝理由，能跑就返回 None。"""
    if transport and transport not in SUPPORTED_TRANSPORTS:
        return (
            f"transport {transport!r} is no longer supported "
            f"(only {', '.join(SUPPORTED_TRANSPORTS)}); 见 migrations/016"
        )
    return None


def remote_tool_name(tool_name: str, server_id: int) -> str:
    """平台侧的工具名 → **远端自己认识的**那个名字。

    `mcp_{server_id}_` 这个前缀是**我们加的**，用来消歧：工具名只在单个 MCP server
    内唯一，聚合多个必然撞（GitHub 和 Linear 都有 create_issue）。远端不知道这回事，
    它只认识自己那个名字。

    ⚠️ 这一跳以前是漏的 —— `call_tool(call.tool_name, ...)` 直接把带前缀的名字发了
    出去，远端回一句 "unknown tool 'mcp_27_lookup_ticket'"。之所以一直没显形：sync
    从来没成功过，一行 MCP 工具都不存在，也就没有任何调用能走到这里。是"上游断路挡住
    下游的坑"的又一例，而且**单测挡不住它** —— 单测把 call_tool 整个 mock 掉了，正好
    跳过这一跳。只有真的连一个 server 才撞得到。

    前缀对不上时原样返回而不是报错：那种情况说明这行工具不是 sync 建的（人工插的、
    或者以后换了命名规则），把原名发出去至少还有一半机会是对的，而在这里抛异常只会
    把一个可能可用的调用变成必然失败。
    """
    prefix = f"mcp_{server_id}_"
    return tool_name[len(prefix):] if tool_name.startswith(prefix) else tool_name


async def mcp_call(call: ToolCall, credential: dict | None) -> ToolResult:
    from lea_tools.db import SessionLocal
    from lea_tools.db.models import MCPServer

    if not call.mcp_server_id:
        return ToolResult(status="error", error="no mcp_server_id specified")

    db = SessionLocal()
    try:
        server = db.get(MCPServer, call.mcp_server_id)
        if not server or not server.enabled:
            return ToolResult(status="error", error="MCP server not found or disabled")

        if reason := check_transport(server.transport):
            return ToolResult(status="error", error=reason)
        if not server.endpoint:
            # migrations/016 之后不该再有这种行（没有 endpoint 的都被停用了），
            # 但存量脏数据比迁移活得久，说清楚比抛 AttributeError 有用。
            return ToolResult(
                status="error",
                error="MCP server has no endpoint configured (见 migrations/016)",
            )

        # 凭证优先级：tool_credentials 里那把（加密、按租户）盖过行上的明文 auth_config。
        #
        # ⚠️ 两个落点各对一半，这是已知的过渡状态：auth_config 粒度对（一个 server
        # 一份）但明文且行内共享；tool_credentials 加密且按租户但粒度错（一个 server
        # 出 12 个工具就要配 12 份）。P1 的 mcp_credentials 收掉它。
        #
        # 这里对 server 那份做**拷贝**再改。原来是就地 `auth["bearer_token"] = ...`，
        # 改的是 SQLAlchemy 那个 JSON 属性 —— 同一个 session 里后续如果 commit，这个
        # 临时凭证会被写回库。
        auth = dict(server.auth_config or {})
        if credential and "api_key" in credential:
            auth["bearer_token"] = credential["api_key"]

        session = MCPSession(server.id, server.endpoint, auth)
        # 在 session 关掉之前算出来 —— 之后 server 就是个游离对象了。
        remote_name = remote_tool_name(call.tool_name, server.id)
    finally:
        db.close()

    try:
        # 发**远端认识的**名字，不是我们加了前缀的那个。见 remote_tool_name。
        result = await session.call_tool(remote_name, call.params)
        return ToolResult(output=result.get("content", result))
    except MCPError as e:
        return ToolResult(status="error", error=str(e))
    except httpx.ConnectError as e:
        return ToolResult(status="error", error=f"MCP server unreachable: {e}")
    except httpx.HTTPStatusError as e:
        return ToolResult(
            status="error",
            error=f"MCP server returned {e.response.status_code}: {e.response.text[:200]}",
        )
    except Exception as e:
        return ToolResult(status="error", error=f"MCP call failed: {e}")
