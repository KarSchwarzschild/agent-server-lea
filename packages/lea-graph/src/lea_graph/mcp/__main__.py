"""MCP Server CLI 入口。

使用方式：
    python -m lea_graph.mcp                          # stdio（默认）
    python -m lea_graph.mcp --transport sse --port 8888  # SSE
"""

from __future__ import annotations

import argparse
import asyncio

from lea_graph.mcp.server import create_mcp_server


def main():
    parser = argparse.ArgumentParser(description="LEA Platform MCP Server")
    parser.add_argument("--transport", choices=["stdio", "sse"], default="stdio")
    parser.add_argument("--port", type=int, default=8888)
    args = parser.parse_args()

    server = create_mcp_server()

    if args.transport == "stdio":
        from mcp.server.stdio import stdio_server

        async def run_stdio():
            async with stdio_server() as (read_stream, write_stream):
                await server.run(read_stream, write_stream, server.create_initialization_options())

        asyncio.run(run_stdio())
    else:
        from mcp.server.sse import SseServerTransport
        from starlette.applications import Starlette
        from starlette.routing import Route

        sse = SseServerTransport("/messages")

        async def handle_sse(request):
            async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
                await server.run(streams[0], streams[1], server.create_initialization_options())

        app = Starlette(routes=[
            Route("/sse", endpoint=handle_sse),
            Route("/messages", endpoint=sse.handle_post_message, methods=["POST"]),
        ])

        import uvicorn
        uvicorn.run(app, host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    main()
