"""MCP 发现 —— 只做**出网**这一件事。

## 为什么单独一个端点

`sync` 这个动作其实是三件事，各自该归谁不一样（设计见
docs/02-技术方案/MCP_USER_SERVERS.md §4.6）：

    判断有没有资格同步    lea-config   它有 actor，判断得了"这是不是你的"
    打远端问 tools/list   **本服务**   它有 egress 和凭证，而且已经有 MCP 客户端
    写 tools 行           lea-config   那张表归它

以前三件都在 lea-tools 的 `/admin/mcp-servers/{id}/sync` 里。那在"只有管理员能建
server"的前提下是对的 —— `_load_server` 看一眼 `X-User-Role: admin` 就够了。放开用户
私有 server 之后那个前提没了：判断"这是不是 alice 的 server"需要 actor，而本服务的调用
方是引擎不是浏览器，它**没有 actor 概念**（`admin_mcp.py` 文件头写的就是这件事）。

## 为什么不是整个搬到 lea-config

第一版设计就是那样，被推翻了：那会让 lea-config **开始打用户填的任意 URL**，而它今天
一次外网都不打 —— NetworkPolicy 可以把它的 egress 锁死。

「哪个服务允许往用户填的地址发请求」是一条安全边界，越窄越好。所以出网这一跳留在这里，
lea-config 内部调过来。

## 这个端点不碰库

拿到的是 `{endpoint, auth_config}`，不是 `server_id`。所以它**查不到**也**改不了**任何
一行 —— 归属判断、写 tools 行、写连通状态全在调用方那一侧。

这也是它比 `/sync` 安全的地方：没有"调用方自选租户"的余地，因为它压根不知道租户是什么。

⚠️ 但它**会按调用方给的地址出网**，所以 url_guard 必须在这里再查一遍。不能因为
lea-config 那边校验过就跳过 —— 那边只挡 scheme，而且 DNS 可以在写入之后才改指向内网。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from lea_tools.executor.mcp_proxy import MCPSession, check_transport
from lea_tools.executor.url_guard import check_outbound_url

router = APIRouter(prefix="/internal/mcp", tags=["internal-mcp"])


class DiscoverRequest(BaseModel):
    endpoint: str = Field(min_length=1, max_length=500)
    # auth_config 在服务间传一次（走 internal_auth，内网）。这是拆两跳的代价，
    # 记在设计文档 §4.6 里。
    auth_config: dict | None = None
    transport: str | None = None
    # server_id 只用来构造 MCPSession（它拿这个做日志和前缀判断），**不查库**。
    # 给 0 也能跑 —— 这个端点不需要那一行存在。
    server_id: int = 0


class DiscoverResponse(BaseModel):
    ok: bool
    tools: list[dict[str, Any]] = []
    error: str | None = None


@router.post("/discover", response_model=DiscoverResponse)
async def discover(body: DiscoverRequest) -> DiscoverResponse:
    """连一下远端，把 tools/list 的结果原样返回。

    ## 连不通返回 200 + ok:false，不是 502

    跟 `/admin/mcp-servers/{id}/test` 同一个取向：**请求本身成功了** —— 我们成功地测出
    了"连不通"。502 会让调用方分不清「发现这个动作失败了」和「发现结果是连不通」，而
    那两种情况调用方要做的事完全不同（重试 vs 把错误写到那一行上给用户看）。

    ## 不做任何过滤和改名

    `tool_filter` 是策略（只暴露哪几个），前缀是命名规则 —— 两者都归调用方。这里返回
    远端说的原样，好让调用方那边只有一处决定"哪些留下、叫什么名字"。
    """
    if reason := check_transport(body.transport):
        raise HTTPException(400, reason)

    # ⚠️ 必须在这里再查一遍，不能因为 lea-config 那边校验过就跳过。
    #
    # 那边只挡 scheme（_validate_endpoint 的注释写明了），挡不住
    # `foo.example.com` 解析到 10.0.0.5；而且那道门在**写入**时生效，DNS 可以在写入
    # 之后才改指向内网。这里是真的要出网的时点，也是唯一有意义的检查点。
    if reason := check_outbound_url(body.endpoint):
        raise HTTPException(400, f"endpoint 不允许: {reason}")

    session = MCPSession(body.server_id, body.endpoint, body.auth_config)
    try:
        tools = await session.list_tools()
    except Exception as e:
        return DiscoverResponse(ok=False, error=f"{type(e).__name__}: {e}")

    return DiscoverResponse(ok=True, tools=tools)
