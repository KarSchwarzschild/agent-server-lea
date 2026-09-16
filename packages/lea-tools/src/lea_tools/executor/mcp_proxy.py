"""MCP Proxy —— 连外部 MCP Server，代理 tools/list 和 tools/call。

## 只支持 streamable_http

`stdio` = 在**本进程里 fork 子进程**。它以前是 `mcp_servers.transport` 的默认值，
但从来没实现过，所以是个死值。一旦实现，「用户能加 MCP」就等于「用户能在网关进程
里执行任意命令」。这条路由 migrations/016 在库层面关死（ENUM 只剩一个值），这里再
挡一道 —— 库约束防的是写入，这里防的是**存量脏数据**。

`sse` 是长连接，绑在某一个副本上。本服务是多副本的，下一次工具调用可能落到别的副本，
那边没有这条连接。要支持得引入会话亲和或连接注册表，为一个没人要的传输方式不值。

## 这里为什么不缓存 session

以前有一个 `_sessions: dict[int, MCPSession]`，按 `server_id` 分键，进程内。删掉了，
理由不是「改进」是「它是错的」：

  1. `MCPSession` 缓存不了任何有价值的东西，只缓存了**凭证**。
  2. 缓存键（server_id）比数据的归属粒度粗。今天一行 server 只属于一个租户，最坏是
     用了过期凭证；**放开每用户 key 之后就是 alice 的 token 发 bob 的请求** ——
     跟「按名字查工具不带 user_id」是同一类 bug。
  3. 多副本下各副本的配置更新时间不一致，表现为「我改了 key，有时生效有时不生效」。

现在每次调用现场构造 MCPSession，用官方 SDK 的 streamablehttp_client + ClientSession
开一个**短连接**（连 → initialize 握手 → 调用 → 关），字段全部来自库里那一行。所以
「改了配置立刻生效」是结构上保证的，跨调用不持有连接、也不持有凭证。代价是每次调用一次
握手往返 —— 对当前规模可接受；真成瓶颈了再引入带归属边界的连接复用，而不是退回旧的
按-server 缓存。

同一个事实还推翻了 admin_mcp.py 里那句「/sync 只能留在 lea-tools，因为连接池在本进程
里」—— 从来就没有那个连接池，那个端点可以随便搬。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from pydantic import AnyUrl

from lea_tools.executor.router import ToolCall, ToolResult
from lea_tools.executor.url_guard import check_outbound_url

# 跟 lea-config 的 MCP_TRANSPORT_VALUES 是同一套值，**故意重新定义一遍**：lea-tools
# 是执行侧，它不依赖配置服务的包（两个服务分开部署）。跟 db/models.py 里
# OWNER_SCOPE_* 的处理一致。
SUPPORTED_TRANSPORTS = ("streamable_http",)

# MCP 的 resources 不是 tool，但模型只会调 tool。sync 把"这个 server 能读资源"投影成
# 两个**合成工具行**，让它们复用整条 tool 下发/执行管道。这两个常量是判据：写进合成
# 工具行的 `endpoint` 列（真 MCP 工具行 endpoint 恒为 NULL），mcp_call 读到它就走
# resources 分支而不是 call_tool。admin_mcp.py 建行时引用同一对常量。
MCP_RESOURCE_LIST = "mcp:resource-list"
MCP_RESOURCE_READ = "mcp:resource-read"


class MCPSession:
    """一次 MCP 调用需要的全部东西。**不要缓存它的实例**（见模块头）。

    每次调用现场构造，字段全部来自库里那一行 —— 这样「改了配置立刻生效」是结构上
    保证的，不是靠记得去失效某个缓存。构造成本是三次属性赋值。

    ## 为什么用官方 SDK 而不是裸 POST

    以前这里是自己拼 JSON-RPC + `resp.json()`，两个硬伤让它连不了任何**规范实现**的
    streamable-HTTP server（deepwiki / context7 等公开 server 都是）：

      1. **不解析 SSE**：规范 server 用 `event:/data:` 流回，`resp.json()` 撞上流的
         第一行就 `Expecting value: line 1 column 1`。
      2. **跳过 initialize 握手**：MCP 要求先 initialize → 拿 Mcp-Session-Id →
         notifications/initialized → 才能 tools/list。直接发 tools/list 规范 server 不认。

    这两件（SSE 解析、握手、session-id 管理）正是官方 SDK 已经做好的，自己写只会引 bug。
    每次调用现开一个短连接、握手、用完关 —— 保住模块头「不缓存 session」的原则：SDK 的
    ClientSession 是 async context manager，随用随建随关，不持有跨调用的凭证。
    """

    def __init__(self, server_id: int, endpoint: str, auth_config: dict | None = None):
        self.server_id = server_id
        self.endpoint = endpoint.rstrip("/")
        self.auth_config = auth_config or {}

    def _headers(self) -> dict[str, str]:
        # 凭证头。Accept / Content-Type / Mcp-Session-Id 等协议头由 SDK 自己管，这里
        # 只补鉴权。bearer 优先于 api_key，跟 mcp_call 的凭证优先级一致。
        headers: dict[str, str] = {}
        if "bearer_token" in self.auth_config:
            headers["Authorization"] = f"Bearer {self.auth_config['bearer_token']}"
        elif "api_key" in self.auth_config:
            headers["X-API-Key"] = self.auth_config["api_key"]
        return headers

    async def list_tools(self) -> list[dict]:
        # 短连接：连 → initialize 握手 → tools/list → 关。SDK 负责 SSE 解析和 session-id。
        headers = self._headers()
        async with streamablehttp_client(self.endpoint, headers=headers or None) as (
            read, write, _get_session_id,
        ):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.list_tools()
                # 回成跟旧实现一致的 list[dict]（sync 那边按 name/description/inputSchema 读）。
                return [t.model_dump(by_alias=True) for t in result.tools]

    async def call_tool(self, tool_name: str, params: dict[str, Any]) -> dict:
        headers = self._headers()
        async with streamablehttp_client(self.endpoint, headers=headers or None) as (
            read, write, _get_session_id,
        ):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool_name, params or {})

        # MCP 用 isError 表达「工具自己失败了」，跟传输层的错误是两回事。不认它的话
        # 一次失败的调用会被当成成功结果喂给模型 —— 模型会拿着一句错误信息当数据用。
        if result.isError:
            texts = [
                getattr(c, "text", "") for c in (result.content or [])
                if getattr(c, "type", None) == "text"
            ]
            raise MCPError("\n".join(t for t in texts if t) or "tool reported an error")

        texts = [
            c.text for c in (result.content or [])
            if getattr(c, "type", None) == "text"
        ]
        if texts:
            return {"content": "\n".join(texts)}
        # 没有文本块（结构化返回等）—— 交出可序列化的原始结果，别丢数据。
        return {"content": result.model_dump(by_alias=True)}

    @asynccontextmanager
    async def _open(self):
        """开一条短连接、完成 initialize 握手，交出可用的 ClientSession。

        连 → initialize → yield → 关，跟 list_tools/call_tool 里那段 nested async with
        同形，抽出来给下面几个 resources 方法复用 —— 保住模块头「不缓存 session」的原则：
        随用随建随关，不持有跨调用的连接或凭证。
        """
        headers = self._headers()
        async with streamablehttp_client(self.endpoint, headers=headers or None) as (
            read, write, _get_session_id,
        ):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session

    async def describe(self) -> dict:
        """一次连接内取回 tools + resources + 能力位，给 /sync 用。

        为什么不复用 list_tools：sync 要同时知道「有哪些工具」和「支不支持 resources、
        有哪些资源」。分两次连接就要握两次手；这里单次 initialize 拿全部。/test 仍走
        list_tools（它只答"通不通"，不关心 resources）。

        supports_resources 取自 initialize 广告的 capabilities.resources —— 规范要求
        客户端不去调没广告的能力。但广告了却列不出来（实现不全 / 当前为空）也不该拖垮
        整次 describe：tools 已经拿到，resources 退化成空。
        """
        headers = self._headers()
        async with streamablehttp_client(self.endpoint, headers=headers or None) as (
            read, write, _get_session_id,
        ):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                tools_result = await session.list_tools()
                tools = [t.model_dump(by_alias=True) for t in tools_result.tools]

                supports_resources = (
                    getattr(init.capabilities, "resources", None) is not None
                )
                resources: list[dict] = []
                if supports_resources:
                    try:
                        res_result = await session.list_resources()
                        # mode="json" 把 AnyUrl 之类序列化成字符串 —— 这份要塞进 sync
                        # 的响应体，得是可 JSON 化的。
                        resources = [
                            r.model_dump(by_alias=True, mode="json")
                            for r in res_result.resources
                        ]
                    except Exception:
                        resources = []
        return {
            "tools": tools,
            "resources": resources,
            "supports_resources": supports_resources,
        }

    async def list_resources(self) -> list[dict]:
        # 执行侧 list_resources 合成工具走这条。暂不翻页（cursor）—— 到有 server 真的
        # 返回上百个资源再说，现在多一层分页只是没人用的复杂度。
        async with self._open() as session:
            result = await session.list_resources()
        return [r.model_dump(by_alias=True, mode="json") for r in result.resources]

    async def read_resource(self, uri: str) -> list[dict]:
        # SDK 的 read_resource 收 AnyUrl，模型传进来的是裸字符串，这里包一层。
        async with self._open() as session:
            result = await session.read_resource(AnyUrl(uri))
        return [c.model_dump(by_alias=True, mode="json") for c in (result.contents or [])]


def _render_resource_catalog(resources: list[dict]) -> str:
    """资源清单 → 给模型看的一段文本。

    每行一个 uri（模型拿它去 read_resource）+ 名称/说明（帮它判断该读哪个）+ mimeType
    （预判文本还是二进制）。空清单明说，别让模型以为是读失败了。
    """
    if not resources:
        return "（这个 server 当前没有可读的 resource）"
    lines = []
    for r in resources:
        bits = [f"- {r.get('uri', '')}"]
        if name := r.get("name"):
            bits.append(f"  名称: {name}")
        if desc := r.get("description"):
            bits.append(f"  说明: {desc}")
        if mime := r.get("mimeType"):
            bits.append(f"  类型: {mime}")
        lines.append("\n".join(bits))
    return "\n".join(lines)


def _render_resource_contents(contents: list[dict]) -> str:
    """resource 的内容块 → 给模型看的字符串。

    MCP 一个 resource 可返回多个内容块，每块要么文本（text）要么二进制（blob，base64）：
      · 文本块：原样拼进去，模型能直接读。
      · 二进制块：不内联 base64（模型读不懂一坨 base64，只会烧 token），换成一行占位，
        标出 uri / mimeType / 大小 —— 让模型知道"这里有个二进制资源"而不是当成读失败。
    多块之间空行隔开。
    """
    parts: list[str] = []
    for c in contents:
        if c.get("text") is not None:
            parts.append(c["text"])
        elif c.get("blob") is not None:
            uri = c.get("uri", "")
            mime = c.get("mimeType", "application/octet-stream")
            parts.append(f"[二进制资源 {uri} ({mime}, ~{len(c['blob'])} base64 字节)，未内联]")
    if not parts:
        return "（resource 没有返回任何内容）"
    return "\n\n".join(parts)


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

        # 出网地址检查。**每次调用都查，不只在 /test /sync 时查。**
        #
        # 只在 admin_mcp._load_server 挡是不够的：那两个端点是管理动作，而工具调用是
        # 一条独立的路 —— DNS 可以在 sync 成功之后才改指向内网，那之后每次调用都是一次
        # SSRF，而管理端再也不会被打到。
        #
        # 代价是每次调用一次 getaddrinfo（命中系统 DNS 缓存时是内存查表）。跟一次真的
        # 出网 RPC 比可以忽略。
        if reason := check_outbound_url(server.endpoint):
            return ToolResult(status="error", error=f"endpoint 不允许: {reason}")

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
        # 资源合成工具的判据：真 MCP 工具行 endpoint 为 NULL，合成行写了 marker。
        # 在 db 关掉之前读出来（call.endpoint 来自工具行的 endpoint 列）。
        marker = call.endpoint
        # 在 session 关掉之前算出来 —— 之后 server 就是个游离对象了。
        remote_name = remote_tool_name(call.tool_name, server.id)
    finally:
        db.close()

    try:
        # resources 分支：这两个"工具"是我们合成的，远端没有同名 tool，不能走 call_tool
        # （见 admin_mcp._sync_resource_tools）。改调 MCP 的 resources/* 方法。
        if marker == MCP_RESOURCE_LIST:
            resources = await session.list_resources()
            return ToolResult(output={"content": _render_resource_catalog(resources)})
        if marker == MCP_RESOURCE_READ:
            uri = (call.params or {}).get("uri")
            if not uri:
                return ToolResult(status="error", error="read_resource 需要 uri 参数")
            contents = await session.read_resource(uri)
            return ToolResult(output={"content": _render_resource_contents(contents)})
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
