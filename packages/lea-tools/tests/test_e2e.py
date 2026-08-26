"""
Tool Gateway E2E 测试 — 覆盖所有 executor + 权限 + 配额。
使用 mock backend 模拟 lea-rag / 外部 API / E2B / MCP Server。
"""

import asyncio
import json
import threading
import time

import httpx
import uvicorn
from fastapi import FastAPI, Request

# ============================================================
# Mock Backends
# ============================================================

mock_app = FastAPI()


@mock_app.post("/kb/search")
async def mock_kb_search(request: Request):
    body = await request.json()
    return {
        "results": [
            {"content": f"mock result for: {body.get('query', '')}", "score": 0.95}
        ]
    }


@mock_app.post("/memory/search")
async def mock_memory_search(request: Request):
    body = await request.json()
    return {"memories": [{"text": "user prefers dark mode", "relevance": 0.8}]}


@mock_app.post("/api/execute")
async def mock_e2b_execute(request: Request):
    body = await request.json()
    code = body.get("code", "")
    return {
        "stdout": f"executed: {code[:50]}",
        "stderr": "",
        "artifacts": [],
    }


@mock_app.post("/external/search")
async def mock_external_search(request: Request):
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return {"error": "unauthorized"}, 401
    body = await request.json()
    return {
        "data": {
            "items": [
                {"title": "AI News", "url": "https://example.com", "snippet": "Latest AI developments"}
            ]
        }
    }


@mock_app.post("/mcp-server")
async def mock_mcp_server(request: Request):
    """Mock MCP Server — Streamable HTTP transport."""
    body = await request.json()
    method = body.get("method", "")
    if method == "tools/list":
        return {
            "jsonrpc": "2.0", "id": body["id"],
            "result": {
                "tools": [
                    {"name": "get_weather", "description": "Get weather", "inputSchema": {"type": "object", "properties": {"city": {"type": "string"}}}},
                    {"name": "get_stock", "description": "Get stock price", "inputSchema": {"type": "object", "properties": {"symbol": {"type": "string"}}}},
                ]
            }
        }
    elif method == "tools/call":
        name = body["params"]["name"]
        args = body["params"]["arguments"]
        return {
            "jsonrpc": "2.0", "id": body["id"],
            "result": {"content": [{"type": "text", "text": f"{name}({json.dumps(args)}) => mock_result"}]}
        }
    return {"jsonrpc": "2.0", "id": body.get("id", 0), "error": {"code": -32601, "message": "unknown method"}}


# ============================================================
# Test runner
# ============================================================

import os
os.environ["DB_HOST"] = "sqlite"
os.environ["DB_NAME"] = "test_e2e"
os.environ["LEA_RAG_URL"] = "http://127.0.0.1:9900"
os.environ["E2B_API_URL"] = "http://127.0.0.1:9900"
os.environ["REDIS_URL"] = "redis://202.205.160.8:16379/15"
os.environ["ENCRYPTION_KEY"] = "test-encryption-key-32bytes!!!!!"


def start_mock():
    uvicorn.run(mock_app, host="127.0.0.1", port=9900, log_level="error")


def start_gateway():
    from lea_tools.server import app
    uvicorn.run(app, host="127.0.0.1", port=8410, log_level="error")


# ── 种子数据：直接写库 ────────────────────────────────────────────────────
#
# 平台内部的执行类型（internal / sandbox）没有、也不该有 HTTP 入口，所以种子只能
# 走库。这也让"哪些工具存在"和"接口允许建什么"解耦：接口收紧了不会连带把测试
# 的覆盖面缩掉。

TENANT = "t1"
USER = "u1"       # 普通用户，只看得到共享工具
OWNER = "u2"      # 有一个私有工具的用户


def seed_tools(check):
    """种 t1 自己的工具。

    ⚠️ 这批种子以前叫 kb_search / memory_search / web_search —— **跟三个平台内置工具
    同名**，而且是**共享工具**（owner_scope=platform）。共享工具跟内置工具都不带
    命名空间前缀，所以那是真撞，现在建不出来（lea-config 的 _reject_name_conflict）。
    它当时造成了 4 条 e2e 失败，症状还极难追：解析同名时取哪行由存储引擎决定，
    实测内置那行赢，于是本该打 mock 的 web_search 打到了真的 serpapi.com，
    报错来自远端而不是我们的代码。

    （换成**私有**工具的话反而不冲突 —— 它的暴露名是 my__web_search。下面 my_notes
    走的就是那条路，所以执行时的名字是 my__my_notes。）

    改法不是"让租户的赢"，是让种子本身合法：**要测内置行为就直接用内置工具**
    （kb_search / memory_search 由 lifespan 种到全局租户），自建的换成不撞名的名字。
    """
    from lea_tools.db import SessionLocal
    from lea_tools.db.models import OWNER_SCOPE_PLATFORM, OWNER_SCOPE_USER, Tool

    rows = [
        # 租户共享工具（管理员建的那种），全租户可见
        Tool(tenant_id=TENANT, name="code_exec", display_name="代码执行",
             execution_type="sandbox", owner_scope=OWNER_SCOPE_PLATFORM,
             schema_json={"code": "string", "language": "string"}),
        Tool(tenant_id=TENANT, name="mock_search", display_name="联网搜索（mock）",
             execution_type="api", schema_json={"query": "string"},
             owner_scope=OWNER_SCOPE_PLATFORM,
             http_adapter_config={
                 "endpoint": "http://127.0.0.1:9900/external/search",
                 "method": "POST",
                 "headers_template": {"Authorization": "Bearer {{api_key}}"},
                 "request_mapping": {"body": {"q": "{{query}}"}},
                 "response_mapping": {"result": "$.data.items"},
             }),
        # u2 的私有工具 —— 只有 u2 看得见。不需要凭证，所以打不要鉴权的那个 mock。
        Tool(tenant_id=TENANT, name="my_notes", display_name="我的笔记",
             execution_type="api", schema_json={"query": "string"},
             owner_scope=OWNER_SCOPE_USER, owner_user_id=OWNER,
             http_adapter_config={
                 "endpoint": "http://127.0.0.1:9900/kb/search",
                 "method": "POST",
                 "request_mapping": {"body": {"query": "{{query}}"}},
                 "response_mapping": {"result": "$.results"},
             }),
    ]
    db = SessionLocal()
    try:
        db.add_all(rows)
        db.commit()
        n = db.query(Tool).filter(Tool.tenant_id == TENANT).count()
        check("seed 3 tools", n == 3, f"got {n}")

        # lifespan 刚种完的内置工具必须带 is_builtin=1。这一列是"内置不可改不可删"
        # 那两道门的唯一依据，而 seed_builtin_tools 是内置行的唯一生产者 —— 漏写的
        # 后果是两道门在现网有效（那 5 行是老 lea-server 种的，标了 1）、在任何新
        # 环境静默失效。正因为只影响新库，才特别容易漏，所以钉在这里。
        builtins = db.query(Tool).filter(Tool.tenant_id == "default").all()
        check("builtins seeded with is_builtin=1",
              len(builtins) == 5 and all(t.is_builtin for t in builtins),
              f"got {[(t.name, t.is_builtin) for t in builtins]}")
    finally:
        db.close()


def seed_permission(role: str, tool_name: str, allowed: bool, rate_limit: int = 0):
    """按**工具名**建权限规则，走跟执行完全相同的解析路径。

    以前这里传的是 tool_id=1 —— 那是"第一条种子恰好是 kb_search"的巧合。加一行种子
    就会让这条规则悄悄挂到别的工具上，而测试照样绿（只是不再测它自称在测的东西）。

    后来改成按名字查，但查的是 `tenant_id == TENANT` —— 那对**平台内置工具**是错的：
    它们住在全局租户里，这么查一条都找不到（`.one()` 直接抛）。规则必须挂到执行时
    真正解析出来的那一行上，所以这里直接用 resolve_tool。规则行本身仍然是 t1 的
    （tenant_id=TENANT）—— 租户可以给一个平台工具配自己的权限，这是对的。
    """
    from lea_tools.db import SessionLocal
    from lea_tools.db.models import ToolPermission
    from lea_tools.services import resolve_tool

    db = SessionLocal()
    try:
        tool = resolve_tool(db, TENANT, USER, tool_name)
        assert tool is not None, f"解析不到工具 {tool_name}"
        db.add(ToolPermission(
            tenant_id=TENANT, tool_id=tool.id, role=role,
            allowed=allowed, rate_limit=rate_limit,
        ))
        db.commit()
    finally:
        db.close()


def seed_mcp_server(check) -> int:
    from lea_tools.db import SessionLocal
    from lea_tools.db.models import MCPServer

    db = SessionLocal()
    try:
        s = MCPServer(
            tenant_id=TENANT, name="Mock Weather MCP",
            transport="streamable_http",
            endpoint="http://127.0.0.1:9900/mcp-server",
        )
        db.add(s)
        db.commit()
        db.refresh(s)
        check("register MCP server", s.id is not None)
        return s.id
    finally:
        db.close()


def count_tools() -> int:
    from lea_tools.db import SessionLocal
    from lea_tools.db.models import Tool

    db = SessionLocal()
    try:
        return db.query(Tool).filter(Tool.tenant_id == TENANT).count()
    finally:
        db.close()


def seed_credential(check):
    from lea_tools.db import SessionLocal
    from lea_tools.db.models import Tool, ToolCredential
    from lea_tools.services.credential import encrypt_key

    db = SessionLocal()
    try:
        # 按名字取 id，不硬编码 4 —— 硬编码的话上面加一行种子就悄悄挂到别的工具上。
        tool = db.query(Tool).filter(
            Tool.tenant_id == TENANT, Tool.name == "mock_search"
        ).one()
        db.add(ToolCredential(
            tenant_id=TENANT, tool_id=tool.id, provider="serpapi",
            api_key_enc=encrypt_key("sk-test-search-key"),
            is_platform_default=True,
        ))
        db.commit()
        check("credential for mock_search stored encrypted",
              db.query(ToolCredential).filter(
                  ToolCredential.tool_id == tool.id).one().api_key_enc
              != "sk-test-search-key")
    finally:
        db.close()


def main():
    import pathlib
    db_file = pathlib.Path("test_e2e.db")
    if db_file.exists():
        db_file.unlink()

    t1 = threading.Thread(target=start_mock, daemon=True)
    t1.start()
    t2 = threading.Thread(target=start_gateway, daemon=True)
    t2.start()
    time.sleep(3)

    base = "http://127.0.0.1:8410"
    client = httpx.Client(base_url=base, timeout=10)
    passed = 0
    failed = 0

    def check(name: str, cond: bool, detail: str = ""):
        nonlocal passed, failed
        if cond:
            passed += 1
            print(f"  PASS  {name}")
        else:
            failed += 1
            print(f"  FAIL  {name} — {detail}")

    print("\n=== Tool Gateway E2E Tests ===\n")

    # --- 1. Health ---
    print("[1] Health Check")
    r = client.get("/health")
    check("health endpoint", r.status_code == 200 and r.json()["status"] == "ok")

    # --- 2. Register tools ---
    #
    # 直接写库，不走 HTTP。原因不是图省事，是这些**本来就不该有 HTTP 入口**：
    #
    #   internal  目标路径来自硬编码的 INTERNAL_ROUTES
    #   sandbox   硬编码读 params.code/language/timeout
    #
    # 这两种执行类型只由平台自己写入（seed.py 和这里）。以前是打
    # POST /admin/tools —— 那条路由没有任何授权、tenant_id 还是调用方自选的，
    # 已经删掉了；lea-config 的 POST /tools 只接受 api / mcp，所以也不该用它来种
    # 这批数据。种子走库、执行走 HTTP，本来就是对的分工。
    print("\n[2] Tool Registration")
    seed_tools(check)

    # --- 3. Credentials ---
    #
    # 同上：/admin/credentials 管的是 api_key_enc，那条无授权路由已删。
    # 凭证直接落库（加密函数还是走真的 encrypt_key，所以仍然覆盖了解密路径）。
    print("\n[3] Credential Management")
    seed_credential(check)

    # --- 4. Capabilities ---
    #
    # 期望值是 t1 的 2 个共享工具 + 5 个平台内置。**没有同名**，所以不涉及去重 ——
    # 这正是种子重做的目的：合法状态下清单里根本不该有需要"谁赢"的两行。
    #
    # 这个数字错过两次，两次都错得像对：
    #   写 4 —— 当时 registry 压根没有租户过滤（返回全库），而单租户环境下
    #           "全库"恰好等于"自己的"
    #   写 6 —— 4 个自建 + 内置里没被遮蔽的 2 个，成立的前提是遮蔽合法
    print("\n[4] Session Capabilities")
    r = client.post("/session/capabilities", json={"tenant_id": "t1", "user_id": USER, "role": "user"})
    names = [t["name"] for t in r.json()["tools"]]
    check("capabilities = 2 shared + 5 builtins",
          len(names) == 7, f"got {len(names)}: {names}")
    check("no duplicate tool names", len(names) == len(set(names)), f"got {names}")
    check("private tool not visible to another user",
          "my__my_notes" not in names, f"got {names}")

    # ⚠️ 下面这条同时是**缓存键**的回归，前提是 Redis 通（[11] 的限流测试能证明）：
    # 上一次调用刚把 u1 的清单写进缓存，键里少了 user_id 的话这次会直接命中它 ——
    # u2 会拿到一份不含 my_notes 的清单，这条就红。别把它当成"重复的可见性测试"删掉。
    r = client.post("/session/capabilities", json={"tenant_id": "t1", "user_id": OWNER, "role": "user"})
    owner_names = [t["name"] for t in r.json()["tools"]]
    check("owner sees their private tool (also: cache key includes user_id)",
          "my__my_notes" in owner_names and len(owner_names) == 8,
          f"got {len(owner_names)}: {owner_names}")

    # --- 5. Execute: internal (kb_search) ---
    print("\n[5] Execute: Internal (kb_search)")
    r = client.post("/tools/execute", json={"tool_name": "kb_search", "params": {"query": "AI教育"}},
                    headers={"X-Tenant-ID": "t1", "X-User-ID": USER, "X-User-Role": "user"})
    data = r.json()
    check("kb_search success", data["status"] == "success", data.get("error", ""))
    check("kb_search has results", "results" in (data.get("result") or {}))

    # --- 6. Execute: internal (memory_search) ---
    print("\n[6] Execute: Internal (memory_search)")
    r = client.post("/tools/execute", json={"tool_name": "memory_search", "params": {"query": "preference"}},
                    headers={"X-Tenant-ID": "t1", "X-User-ID": USER, "X-User-Role": "user"})
    data = r.json()
    check("memory_search success", data["status"] == "success", data.get("error", ""))

    # --- 7. Execute: sandbox (code_exec) ---
    print("\n[7] Execute: Sandbox (code_exec)")
    r = client.post("/tools/execute", json={"tool_name": "code_exec", "params": {"code": "print('hello')", "language": "python"}},
                    headers={"X-Tenant-ID": "t1", "X-User-ID": USER, "X-User-Role": "user"})
    data = r.json()
    check("sandbox exec success", data["status"] == "success", data.get("error", ""))
    check("sandbox has stdout", "stdout" in (data.get("result") or {}))

    # --- 8. Execute: api (mock_search with credential injection) ---
    print("\n[8] Execute: HTTP Adapter (mock_search)")
    r = client.post("/tools/execute", json={"tool_name": "mock_search", "params": {"query": "latest AI news"}},
                    headers={"X-Tenant-ID": "t1", "X-User-ID": USER, "X-User-Role": "user"})
    data = r.json()
    check("mock_search success", data["status"] == "success", data.get("error", ""))

    # --- 8b. 按人隔离：私有工具只有主人能执行 ---
    #
    # 单测覆盖的是 visible_tools 这一层，这里覆盖的是**身份能不能从 HTTP 头走到
    # SQL** —— X-User-ID 之前根本没人发（Go 侧的 ExecuteRemoteTool 收了这个参数却
    # 从不设头），所以这一整段链路以前是断的。
    print("\n[8b] Per-user Tool Isolation")
    r = client.post("/tools/execute", json={"tool_name": "my__my_notes", "params": {"query": "x"}},
                    headers={"X-Tenant-ID": "t1", "X-User-ID": OWNER, "X-User-Role": "user"})
    check("owner can execute their private tool",
          r.status_code == 200 and r.json()["status"] == "success", r.text[:200])

    r = client.post("/tools/execute", json={"tool_name": "my__my_notes", "params": {"query": "x"}},
                    headers={"X-Tenant-ID": "t1", "X-User-ID": USER, "X-User-Role": "user"})
    check("another user in the same tenant is denied (403)",
          r.status_code == 403, f"got {r.status_code}")

    # 没有身份头 → fail-closed，私有工具不可见。这条防的是"调用方忘了传"退化成
    # "所有人的私有工具都能调"。
    r = client.post("/tools/execute", json={"tool_name": "my__my_notes", "params": {"query": "x"}},
                    headers={"X-Tenant-ID": "t1", "X-User-Role": "user"})
    check("no X-User-ID → private tool denied", r.status_code == 403, f"got {r.status_code}")

    # 反向：缺身份不能把共享工具也一起挡掉（那会让整条工具链停摆）。
    r = client.post("/tools/execute", json={"tool_name": "mock_search", "params": {"query": "x"}},
                    headers={"X-Tenant-ID": "t1", "X-User-Role": "user"})
    check("no X-User-ID → shared tool still works", r.status_code == 200, f"got {r.status_code}")

    # --- 9. Permission deny ---
    print("\n[9] Permission Control")
    seed_permission(role="guest", tool_name="kb_search", allowed=False)
    r = client.post("/tools/execute", json={"tool_name": "kb_search", "params": {"query": "test"}},
                    headers={"X-Tenant-ID": "t1", "X-User-ID": USER, "X-User-Role": "guest"})
    check("guest denied kb_search (403)", r.status_code == 403)

    r = client.post("/tools/execute", json={"tool_name": "mock_search", "params": {"query": "test"}},
                    headers={"X-Tenant-ID": "t1", "X-User-ID": USER, "X-User-Role": "guest"})
    check("guest allowed mock_search", r.status_code == 200)

    # --- 10. MCP Server ---
    #
    # MCP server 本身直接落库（CRUD 在 lea-config，不在本服务）；/sync 仍然走 HTTP，
    # 因为那是本服务唯一保留的配置类端点 —— 它要用本进程里的 MCP 连接池。
    print("\n[10] MCP Proxy")
    mcp_id = seed_mcp_server(check)

    # /sync 现在要求管理员角色 + 租户匹配，所以必须带身份头。
    r = client.post(f"/admin/mcp-servers/{mcp_id}/sync",
                    headers={"X-Tenant-ID": "t1", "X-User-Role": "admin"})
    check("sync MCP tools", r.status_code == 200 and r.json()["synced"] == 2, r.text)

    # 顺手把这条路由的两道门也验了 —— 它是本服务唯一的写端点。
    r = client.post(f"/admin/mcp-servers/{mcp_id}/sync",
                    headers={"X-Tenant-ID": "t1", "X-User-ID": USER, "X-User-Role": "user"})
    check("sync denied for non-admin (403)", r.status_code == 403, f"got {r.status_code}")

    r = client.post(f"/admin/mcp-servers/{mcp_id}/sync",
                    headers={"X-Tenant-ID": "other-tenant", "X-User-Role": "admin"})
    check("sync cross-tenant is 404", r.status_code == 404, f"got {r.status_code}")

    n = count_tools()
    check("tools list now includes MCP tools", n == 5, f"got {n}")

    r = client.post("/tools/execute",
                    json={"tool_name": f"mcp_{mcp_id}_get_weather", "params": {"city": "Beijing"}},
                    headers={"X-Tenant-ID": "t1", "X-User-ID": USER, "X-User-Role": "user"})
    data = r.json()
    check("MCP tool execute success", data["status"] == "success", data.get("error", ""))
    check("MCP result contains mock data", "mock_result" in str(data.get("result", "")))

    # --- 11. Rate limiting ---
    print("\n[11] Rate Limiting")
    seed_permission(role="*", tool_name="kb_search", allowed=True, rate_limit=3)

    # Note: rate limit check requires Redis. If Redis is not available, this test will be skipped.
    try:
        for i in range(4):
            r = client.post("/tools/execute", json={"tool_name": "kb_search", "params": {"query": f"rate test {i}"}},
                            headers={"X-Tenant-ID": "t1", "X-User-ID": USER, "X-User-Role": "user"})
        check("rate limit enforced (4th call → 429)", r.status_code == 429, f"got {r.status_code}")
    except Exception as e:
        print(f"  SKIP  rate limiting (Redis not available: {e})")

    # --- Summary ---
    print(f"\n{'='*40}")
    print(f"Results: {passed} passed, {failed} failed")
    print(f"{'='*40}\n")

    # Cleanup
    #
    # Windows 上网关线程还持有这个 sqlite 文件的句柄，unlink 会抛 WinError 32。
    # 不吞的话异常会在打印完"0 failed"之后逃出去，脚本返回 1 —— CI 拿到的是一个
    # 跟自己的输出矛盾的信号。删不掉不影响正确性（下次跑开头会先删）。
    import pathlib
    try:
        pathlib.Path("test_e2e.db").unlink(missing_ok=True)
    except PermissionError:
        print("  NOTE  test_e2e.db 被占用，留到下次运行时清理")

    return failed == 0


if __name__ == "__main__":
    import sys
    success = main()
    sys.exit(0 if success else 1)
