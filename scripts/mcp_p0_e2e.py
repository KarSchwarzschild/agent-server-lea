"""MCP P0 验收：注册 → 连通 → 同步 → 绑定 → 引擎真调通。

    python scripts/mcp_p0_e2e.py [agent_id]

前置：
    python scripts/mock_mcp_server.py 9910
    lea-config:8500（带 LEA_AUTHZ_ALLOW_UNTRUSTED_HEADERS=1）
    lea-tools:8400
    lea-agent:8100

## 为什么必须整条跑

这条链上有五个环节，**每一个都曾经或正在是断的**：

    ① 注册     endpoint 以前不在创建契约里 → 建出来的行永远是残的
    ② 连通     connection_status 三列以前没有任何代码写过
    ③ 同步     读 endpoint，而它是 NULL → 必然 400，从没成功过一次
    ④ 绑定     绑定接口以前不校验工具归属（本轮刚补）
    ⑤ 调用     读同一个 NULL endpoint

而且这五环的失败**全是安静的**：③ 之前失败，所以 ④⑤ 从来没有数据可走，于是
「MCP 不通」这件事在日志里根本不显形 —— 表现只是"工具列表里没有 MCP 工具"。

## 判据不是「请求成功」

远端工具叫 `lookup_ticket(ticket_ref)`，两个性质是刻意的：

    参数名不可猜   不是 id / query / name。schema 没到模型面前的话它没法知道
                   要传 ticket_ref
    返回值不可编   工单状态是「紫貂-待复核」这种生僻组合，模型编不出来

所以最终判据是：模型传了 `ticket_ref`，且回复里出现了那个状态词。
链路断掉时请求同样是 200 —— 模型会道歉说查不到。
"""

from __future__ import annotations

import json
import pathlib
import sys
import time
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import requests

import _dbconn as d

CONFIG = "http://127.0.0.1:8500"
TOOLS = "http://127.0.0.1:8400"
AGENT = "http://127.0.0.1:8100/api/chat/stream"
MCP_ENDPOINT = "http://127.0.0.1:9910/mcp"

TENANT = "default"
ADMIN = "mcp-e2e-admin"
SECRET = d.env()["SECRET_KEY"]  # 从仓库根 .env 读，别写死 —— 写死就是把密钥提交进库

TICKET = "OUC-2291"
SECRET_STATUS = "紫貂-待复核"

ADMIN_HEADERS = {
    "X-User-ID": ADMIN,
    "X-Tenant-ID": TENANT,
    "X-User-Role": "admin",
    "X-Internal-Token": "dev-internal",
}


def step(n: int, title: str) -> None:
    print(f"\n[{n}] {title}")


def mint_token(user: str) -> str:
    import jwt

    return jwt.encode(
        {"sub": user, "tenant_id": TENANT, "exp": int(time.time()) + 3600},
        SECRET, algorithm="HS256",
    )


def ask(session_id: str, agent_id: int, message: str) -> int:
    r = requests.post(
        AGENT,
        json={"session_id": session_id, "agent_id": agent_id, "message": message},
        headers={"Authorization": f"Bearer {mint_token('mcp-e2e-user')}"},
        stream=True, timeout=180,
    )
    if r.status_code != 200:
        print(f"    HTTP {r.status_code}: {r.text[:300]}")
        return r.status_code
    for _ in r.iter_lines(decode_unicode=True):
        pass
    return 200


def conversation(conn, session_id: str):
    """(工具调用列表, 最后一条回复)。从库里读 —— SSE 的事件形状是另一回事。"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT role, message FROM agent_messages "
            "WHERE tenant_id=%s AND session_id=%s ORDER BY id",
            (TENANT, session_id),
        )
        rows = cur.fetchall()

    calls, reply = [], ""
    for role, raw in rows:
        msg = json.loads(raw)
        if role != "assistant":
            continue
        for b in msg.get("content") or []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use":
                calls.append((b.get("name", ""), b.get("input") or {}))
            elif b.get("text"):
                reply = b["text"]
    return calls, reply


def main() -> int:
    d.utf8_stdout()
    agent_id = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    conn = d.connect()
    server_id = None
    bound_tool_id = None
    failures = []

    def check(label: str, ok: bool, detail: str = "") -> bool:
        print(f"    {'✓' if ok else '✗'} {label}" + (f" —— {detail}" if detail else ""))
        if not ok:
            failures.append(label)
        return ok

    try:
        # ── ① 注册 ────────────────────────────────────────────────────────
        step(1, "注册 MCP server（lea-config）")
        name = f"e2e-ticket-{uuid.uuid4().hex[:6]}"
        r = requests.post(f"{CONFIG}/mcp-servers", headers=ADMIN_HEADERS, json={
            "name": name,
            "description": "P0 验收用的工单 MCP",
            "endpoint": MCP_ENDPOINT,
        }, timeout=15)
        if not check("创建返回 201", r.status_code == 201, r.text[:200]):
            return 1
        body = r.json()
        server_id = body["id"]
        check("endpoint 落库了", body["endpoint"] == MCP_ENDPOINT, body["endpoint"])
        check("transport 默认 streamable_http", body["transport"] == "streamable_http")
        check("config 死列没了", "config" not in body)

        # ── ② 连通 ────────────────────────────────────────────────────────
        step(2, "连通性检查（lea-tools /test）")
        r = requests.post(f"{TOOLS}/admin/mcp-servers/{server_id}/test",
                          headers=ADMIN_HEADERS, timeout=30)
        check("HTTP 200", r.status_code == 200, r.text[:200])
        t = r.json()
        check("ok=True", t.get("ok") is True, str(t)[:160])
        check("远端报了 2 个工具", t.get("tool_count") == 2, str(t.get("tools")))

        r = requests.get(f"{CONFIG}/mcp-servers/{server_id}", headers=ADMIN_HEADERS, timeout=15)
        check("connection_status 写回了 connected",
              r.json().get("connection_status") == "connected",
              r.json().get("connection_status"))

        # ── ③ 同步 ────────────────────────────────────────────────────────
        step(3, "同步工具（lea-tools /sync）")
        r = requests.post(f"{TOOLS}/admin/mcp-servers/{server_id}/sync",
                          headers=ADMIN_HEADERS, timeout=30)
        check("HTTP 200", r.status_code == 200, r.text[:200])
        s = r.json()
        check("同步了 2 个", s.get("synced") == 2, str(s.get("tools")))

        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, execution_type, schema_json, enabled FROM tools "
                "WHERE mcp_server_id=%s ORDER BY name", (server_id,))
            tool_rows = cur.fetchall()
        check("tools 表出现 2 行", len(tool_rows) == 2,
              ", ".join(t[1] for t in tool_rows))

        lookup = next((t for t in tool_rows if t[1].endswith("lookup_ticket")), None)
        if not check("找到 lookup_ticket 那行", lookup is not None):
            return 1
        bound_tool_id = lookup[0]
        schema = json.loads(lookup[3]) if isinstance(lookup[3], str) else lookup[3]
        check("execution_type=mcp", lookup[2] == "mcp", lookup[2])
        check("schema 落在 schema_json 且带 ticket_ref",
              schema.get("required") == ["ticket_ref"], str(schema)[:120])

        # ── ④ 绑定 ────────────────────────────────────────────────────────
        step(4, f"绑定到 agent {agent_id}（lea-config）")
        r = requests.post(f"{CONFIG}/agents/{agent_id}/tools", headers=ADMIN_HEADERS,
                          json={"tool_id": bound_tool_id, "enabled": True}, timeout=15)
        check("绑定成功", r.status_code == 200, r.text[:200])

        r = requests.get(f"{CONFIG}/internal/agent-config/{agent_id}?tenant_id={TENANT}",
                         headers=ADMIN_HEADERS, timeout=15)
        delivered = {t["name"]: t for t in r.json()["tools"]}
        check("下发清单里有它", lookup[1] in delivered, ", ".join(delivered))
        if lookup[1] in delivered:
            check("下发的 schema 带 ticket_ref",
                  delivered[lookup[1]]["schema_json"].get("required") == ["ticket_ref"])

        # ── ⑤ 引擎真调 ────────────────────────────────────────────────────
        step(5, "让模型去查一张工单")
        session_id = f"mcpe2e-{uuid.uuid4().hex[:12]}"
        t0 = time.time()
        code = ask(session_id, agent_id, f"帮我查一下工单 {TICKET} 现在是什么状态？")
        print(f"    HTTP {code}  {time.time()-t0:.1f}s")
        calls, reply = conversation(conn, session_id)
        for n, p in calls:
            print(f"    调用: {n}({json.dumps(p, ensure_ascii=False)})")
        print(f"    回复: {reply.strip()[:160]}")

        mcp_calls = [(n, p) for n, p in calls if n == lookup[1]]
        if check("模型调了这个 MCP 工具", bool(mcp_calls)):
            check("传的是 ticket_ref（schema 到位了）",
                  mcp_calls[0][1].get("ticket_ref") == TICKET,
                  json.dumps(mcp_calls[0][1], ensure_ascii=False))
        check(f"回复里带回了远端状态 {SECRET_STATUS!r}", SECRET_STATUS in reply,
              "模型编不出这个词，所以它必然来自真实调用")

        # ── ⑥ 远端下线的工具要跟着删 ──────────────────────────────────────
        step(6, "远端只暴露一个工具后重新同步")
        requests.put(f"{CONFIG}/mcp-servers/{server_id}", headers=ADMIN_HEADERS,
                     json={"endpoint": f"{MCP_ENDPOINT}?only=lookup_ticket"}, timeout=15)
        r = requests.post(f"{TOOLS}/admin/mcp-servers/{server_id}/sync",
                          headers=ADMIN_HEADERS, timeout=30)
        s = r.json()
        check("list_queues 被删掉", any("list_queues" in n for n in s.get("removed", [])),
              str(s.get("removed")))
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM tools WHERE mcp_server_id=%s", (server_id,))
            check("只剩 1 行", cur.fetchone()[0] == 1)

        print("\n" + ("=" * 60))
        if failures:
            print(f"✗ {len(failures)} 项未通过：" + "、".join(failures))
            return 1
        print("✓ 五个环节全通：注册 → 连通 → 同步 → 绑定 → 引擎真调。")
        print("  改之前第 ③ 步必然 400（endpoint 是 NULL），后面三步从来没有数据可走。")
        return 0

    finally:
        # 删 server 会连带删它的工具行，绑定再跟着外键级联 —— 这本身也是一次
        # 「删除清理」的验证。
        if server_id:
            requests.delete(f"{CONFIG}/mcp-servers/{server_id}",
                            headers=ADMIN_HEADERS, timeout=15)
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM tools WHERE mcp_server_id=%s", (server_id,))
                left = cur.fetchone()[0]
            print(f"\n清理：删掉 server {server_id}，残留工具行 {left}"
                  + ("（✓ 级联干净）" if left == 0 else "（✗ 有残留）"))
        with conn.cursor() as cur:
            cur.execute("DELETE FROM agent_messages WHERE session_id LIKE 'mcpe2e-%%'")
        conn.commit()
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
