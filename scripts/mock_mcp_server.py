"""一个最小的 streamable_http MCP server，用来跑 MCP P0 的真实验收。

    python scripts/mock_mcp_server.py [port]      # 默认 9910

## 为什么要自己写一个

P0 的验收是「建 server → /test → /sync → 绑定 → 引擎真调通」。这条链上任何一环都
不能用 mock 替：`/sync` 要真的发 JSON-RPC 去问 tools/list，`mcp_call` 要真的把参数
发出去再把结果拿回来。所以需要一个**进程外的、真的在监听端口的** MCP server。

## 工具是刻意设计的

`lookup_ticket(ticket_ref)` —— 两个性质都是有意的：

  参数名不可猜   不是 id / query / name 这种模型闭着眼也能蒙对的词。schema 没到
                 模型面前的话，它没有任何办法知道该传 `ticket_ref`。
  返回值不可编   每张工单的状态是一个随机词。模型不可能编对，所以「回复里出现了
                 那个词」是工具真的被调用并且结果真的回来了的硬证据。

这两条合起来，让验收判据不依赖「请求成功」——那个在链路断掉时同样成立（模型会道歉
说查不到，HTTP 仍然 200）。

## 它实现了 MCP 的哪一部分

只有 `initialize` / `tools/list` / `tools/call` 三个方法，JSON 响应，不升级 SSE。
这正是 streamable_http 在「服务端选择返回单个 JSON」时的形态，也是我们唯一支持的
那种。真实 server 可能选择用 SSE 流回，那不在 P0 范围内。
"""

from __future__ import annotations

import sys

from fastapi import FastAPI, Request
import uvicorn

app = FastAPI()

# 工单号 → (状态, 负责人)。状态词是刻意选的生僻组合，模型编不出来。
TICKETS = {
    "OUC-2291": ("紫貂-待复核", "运维二组"),
    "OUC-3307": ("鸢尾-已归档", "平台组"),
}

TOOLS = [
    {
        "name": "lookup_ticket",
        "description": "按工单号查询内部工单的当前状态和负责团队。",
        "inputSchema": {
            "type": "object",
            # ⚠️ 参数名故意不好猜。schema 丢了的话模型没法知道要传 ticket_ref ——
            # 这正是「schema 到没到模型面前」的判据。
            "required": ["ticket_ref"],
            "properties": {
                "ticket_ref": {
                    "type": "string",
                    "description": "工单号，形如 OUC-2291",
                }
            },
        },
    },
    {
        # 第二个工具的存在是为了验 tool_filter 和「远端下线要跟着删」。
        "name": "list_queues",
        "description": "列出所有工单队列。",
        "inputSchema": {"type": "object", "properties": {}},
    },
]


def ok(req_id, result):
    return {"jsonrpc": "2.0", "id": req_id, "result": result}


def err(req_id, code, message):
    return {"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}}


@app.post("/mcp")
async def mcp(request: Request):
    body = await request.json()
    method = body.get("method", "")
    req_id = body.get("id", 0)

    if method == "initialize":
        return ok(req_id, {
            "protocolVersion": "2025-06-18",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "mock-ticket-mcp", "version": "0.1.0"},
        })

    if method == "tools/list":
        # 支持用 ?only=a,b 把暴露的工具集缩小，用来验「远端下线的工具要被 sync 删掉」。
        only = request.query_params.get("only")
        tools = TOOLS
        if only:
            keep = {n.strip() for n in only.split(",")}
            tools = [t for t in TOOLS if t["name"] in keep]
        return ok(req_id, {"tools": tools})

    if method == "tools/call":
        name = body.get("params", {}).get("name", "")
        args = body.get("params", {}).get("arguments", {})

        if name == "list_queues":
            return ok(req_id, {"content": [{"type": "text", "text": "运维二组 / 平台组"}]})

        if name != "lookup_ticket":
            return ok(req_id, {
                "isError": True,
                "content": [{"type": "text", "text": f"unknown tool {name!r}"}],
            })

        ref = args.get("ticket_ref")
        if not ref:
            # 用 isError 而不是 JSON-RPC error —— 这是「工具自己失败了」，
            # 传输层是好的。参数没传出来时会走到这里，正好验 isError 的处理。
            return ok(req_id, {
                "isError": True,
                "content": [{"type": "text", "text": "缺少参数 ticket_ref"}],
            })

        if ref not in TICKETS:
            return ok(req_id, {
                "isError": True,
                "content": [{"type": "text", "text": f"工单 {ref} 不存在"}],
            })

        status, owner = TICKETS[ref]
        return ok(req_id, {"content": [
            {"type": "text", "text": f"工单 {ref}：状态={status}，负责团队={owner}"}
        ]})

    return err(req_id, -32601, f"unknown method {method!r}")


@app.get("/health")
async def health():
    return {"status": "ok", "tools": [t["name"] for t in TOOLS]}


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 9910
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")
