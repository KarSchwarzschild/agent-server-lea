"""验收：用户挂**自己的 MCP server**，粒度是整个 server。

    python scripts/mock_mcp_server.py 9910 &
    LEA_AGENT_URL=http://127.0.0.1:8080 python scripts/session_attach_mcp_e2e.py

前置：lea-config / lea-tools / lea-agent 在跑（infra/docker-compose.verify.yml）。

## 为什么这条必须连真远端

`remote_tool_name` 那个坑（发出去的名字带着我们自己加的 `mcp_{id}_` 前缀，远端回
"unknown tool"）**单测挡不住** —— 单测把 call_tool 整个 mock 掉了，正好跳过那一跳。
挂载这条路上前缀多了一层（`my__mcp_37_lookup_ticket`），两层各由不同的代码剥：

    my__        归属命名空间     lea-tools 的 resolve_tool 剥
    mcp_37_     server 消歧      mcp_proxy 的 remote_tool_name 剥

少剥一层、或者剥错顺序，症状都是远端说不认识 —— 而那只有真的连一个 server 才撞得到。

## 六条判据

    ① 普通用户能建自己的私有 server（POST /mcp-servers/mine，不需要管理员）
    ② sync 出来的工具行归属跟着 server 走（owner_scope=user / owner_user_id=alice）
    ③ 挂一个 server → **两个**工具都进来（粒度是整个 server）
    ④ 模型真调通：传了 ticket_ref，回复里有那个编不出来的状态词
    ⑤ 另一个用户看不到（挂载只影响一个会话 + 私有 server 别人够不着）
    ⑥ 摘掉之后不再执行（判据看上游访问日志，不是看 tool_use）

## ⚠️ ④ 和 ⑥ 的判据都不能只看模型说了什么

沿用前两轮的教训。④ 那边：模型编一个工单状态毫不费力，所以判据是那个**不可伪造的
锚点**（紫貂-待复核）。⑥ 那边：摘除后模型仍然会发 tool_use（它在历史里见过这个工具），
引擎回 unknown tool 挡住 —— 那是正确行为。所以判据落在**上游有没有被真的打到**：
假 server 的 /health 报每个工具被调了多少次，那个数字伪造不了。

同一个判据错误在 tool 和 skill 两轮各犯过一次：一次把 tool_use 当成"工具还在"，一次
把延续格式当成"skill 还在"。两次的形状一样 —— 把模型的输出当成能力的证据。

## ⚠️ 一个已知缺口：普通用户调不通自己 server 的 sync

判据 ② 用的是带 admin 角色的 token，不是普通用户的。因为 lea-tools 的 `_load_server`
第一件事就是 `if role != "admin": 403` —— 它是在"只有管理员能建 server"那个前提下写的。

放开私有 server 之后那个前提没了，但那道门还在。所以今天的实际状态是：

    用户能建自己的 server        ✓（POST /api/user/mcp-servers/mine）
    用户能 sync 自己的 server    ✗ 403，得管理员帮他点

正确的修法是让 `_load_server` 认归属（自己的 server 自己能 sync），而那要先把 sync
搬到有 actor 概念的 lea-config 那一侧 —— `admin_mcp.py` 文件头把这件事记为 P1，理由
正是"lea-tools 没有 actor，判断不了这是不是你的"。

**这个脚本不绕过那道门去假装它不存在**，它用 admin token 跑通后面的链路，并把缺口写在
这里。绕过的话就会攒出一条"验收通过但用户用不了"的路。
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import time
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import requests

import _dbconn as d

AGENT_URL = os.getenv("LEA_AGENT_URL", "http://127.0.0.1:8100")
# 假 server 从容器里看是 host.docker.internal，跟 mock_stock_api 那套一致
MOCK_MCP = os.getenv("MOCK_MCP_URL", "http://host.docker.internal:9910/mcp")
MOCK_HEALTH = os.getenv("MOCK_MCP_HEALTH", "http://127.0.0.1:9910/health")

SECRET = d.env()["SECRET_KEY"]
TENANT = "default"
ALICE = "mcpatt-alice"
BOB = "mcpatt-bob"
AGENT_ID = int(os.getenv("LEA_AGENT_ID", "2"))

SERVER_NAME = f"my-tickets-{uuid.uuid4().hex[:6]}"
TICKET = "OUC-2291"
SECRET_STATUS = "紫貂-待复核"
QUESTION = f"帮我查一下工单 {TICKET} 现在什么状态。"


# ── HTTP ──────────────────────────────────────────────────────────────────

def mint(user: str) -> str:
    import jwt
    return jwt.encode(
        {"sub": user, "tenant_id": TENANT, "exp": int(time.time()) + 3600},
        SECRET, algorithm="HS256",
    )


def bearer(user: str) -> dict:
    return {"Authorization": f"Bearer {mint(user)}"}


def admin_bearer(user: str) -> str:
    """带 admin 角色的 JWT。

    ⚠️ 只有 sync 需要它，而那是个**已知缺口**：lea-tools 的 _load_server 要求
    X-User-Role: admin，所以普通用户调不通自己 server 的 sync。见模块 docstring。

    反代按白名单把 JWT 里的 roles 下发成 X-User-Role（proxy.go 的 setUserRoleHeader），
    所以这里把角色放进 token 而不是直接塞头 —— 塞头会被反代删掉，那正是它的作用。
    """
    import jwt
    return jwt.encode(
        {"sub": user, "tenant_id": TENANT, "roles": ["admin"],
         "exp": int(time.time()) + 3600},
        SECRET, algorithm="HS256",
    )


def turn_running(session_id: str, user: str) -> bool:
    try:
        r = requests.get(f"{AGENT_URL}/api/chat/status/{session_id}",
                         headers=bearer(user), timeout=10)
        return r.status_code == 200 and bool(r.json().get("running"))
    except requests.RequestException:
        return False


def ask(session_id: str, user: str, message: str) -> int:
    """发一条消息，等这一轮真的收尾（见 session_attach_e2e.py 里那段说明）。"""
    r = requests.post(
        f"{AGENT_URL}/api/chat/stream",
        json={"session_id": session_id, "agent_id": AGENT_ID, "message": message},
        headers=bearer(user), stream=True, timeout=180,
    )
    if r.status_code != 200:
        print(f"    HTTP {r.status_code}: {r.text[:200]}")
        return r.status_code
    for _ in r.iter_lines(decode_unicode=True):
        pass
    for _ in range(90):
        if not turn_running(session_id, user):
            break
        time.sleep(1)
    return 200


def upstream_hits() -> dict:
    """假上游被调了多少次 —— **不可伪造的证据**。"""
    try:
        r = requests.get(MOCK_HEALTH, timeout=10)
        return r.json().get("calls", {}) if r.status_code == 200 else {}
    except requests.RequestException:
        return {}


def reset_hits() -> None:
    """清零计数，好判断"这一个窗口里命中几次"。

    比"记下旧值再相减"可靠 —— 那个减法的采样点很容易取错（工具那轮就判错过一次：
    采样点取在了上一轮收尾之前）。
    """
    try:
        requests.post(MOCK_HEALTH.replace("/health", "/reset-calls"), timeout=10)
    except requests.RequestException:
        pass


# ── 库 ────────────────────────────────────────────────────────────────────

def tool_rows(conn, server_id: int) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT name, owner_scope, owner_user_id, enabled FROM tools "
            "WHERE mcp_server_id=%s ORDER BY name",
            (server_id,),
        )
        return cur.fetchall()


def cleanup(conn, server_id: int | None, sessions: list[str]) -> None:
    with conn.cursor() as cur:
        for s in sessions:
            cur.execute("DELETE FROM session_attachments WHERE session_id=%s", (s,))
            cur.execute("DELETE FROM agent_messages WHERE session_id=%s", (s,))
            cur.execute("DELETE FROM chat_sessions WHERE id=%s", (s,))
        if server_id:
            cur.execute("DELETE FROM tools WHERE mcp_server_id=%s", (server_id,))
            cur.execute("DELETE FROM mcp_servers WHERE id=%s", (server_id,))
    conn.commit()


def message_count(conn, session_id: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM agent_messages WHERE tenant_id=%s AND session_id=%s",
            (TENANT, session_id),
        )
        return cur.fetchone()[0]


def transcript_since(conn, session_id: str, mark: int) -> tuple[list, list, str]:
    """(工具调用, 工具返回, 最后一段文本)，只看第 mark 条之后新增的。"""
    calls, results, reply = [], [], ""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT role, message FROM agent_messages "
            "WHERE tenant_id=%s AND session_id=%s ORDER BY id "
            "LIMIT 18446744073709551615 OFFSET %s",
            (TENANT, session_id, mark),
        )
        rows = cur.fetchall()
    for role, raw in rows:
        msg = json.loads(raw)
        for b in msg.get("content") or []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_use":
                calls.append((b.get("name", ""), b.get("input") or {}))
            elif b.get("type") == "tool_result":
                c = b.get("content")
                results.append(c if isinstance(c, str) else json.dumps(c, ensure_ascii=False))
        if role == "assistant":
            t = "".join(b.get("text", "") for b in (msg.get("content") or [])
                        if isinstance(b, dict)).strip()
            if t:
                reply = t
    return calls, results, reply


def step(n, title): print(f"\n[{n}] {title}")


def main() -> int:
    d.utf8_stdout()
    conn = d.connect()
    s_alice = f"mcpatt-a-{uuid.uuid4().hex[:10]}"
    s_bob = f"mcpatt-b-{uuid.uuid4().hex[:10]}"
    server_id: int | None = None
    fails: list[str] = []

    try:
        step(0, "假 MCP server 在不在")
        h = requests.get(MOCK_HEALTH, timeout=10)
        print(f"    /health → {h.status_code} {h.text[:120]}")
        if h.status_code != 200:
            print("    ✗ 先起 python scripts/mock_mcp_server.py 9910")
            return 1

        step(1, f"① {ALICE}（普通用户）建自己的私有 server")
        # 走 lea-agent 的反代（/api/user/mcp-servers → lea-config 的 /mcp-servers）。
        # **故意不直连 lea-config** —— 那条路用户在线上够不着，直连就验不到"反代把
        # 身份注入对了"这件事，而那正是这条链新加的一环。
        r = requests.post(
            f"{AGENT_URL}/api/user/mcp-servers/mine",
            json={"name": SERVER_NAME, "endpoint": MOCK_MCP,
                  "description": "我自己的工单系统"},
            headers=bearer(ALICE), timeout=30,
        )
        print(f"    HTTP {r.status_code} {r.text[:220]}")
        if r.status_code != 201:
            fails.append(f"① 普通用户建不了私有 server（HTTP {r.status_code}）")
            raise SystemExit
        body = r.json()
        server_id = body["id"]
        if body.get("owner_scope") != "user" or body.get("owner_user_id") != ALICE:
            fails.append(f"① 归属不对: scope={body.get('owner_scope')} "
                         f"owner={body.get('owner_user_id')}")
        else:
            print(f"    ✓ ① server_id={server_id} 归 {ALICE}")

        step(2, "② sync —— 工具行归属要跟着 server 走")
        r = requests.post(
            f"{AGENT_URL}/api/user/mcp-actions/{server_id}/sync",
            headers={"Authorization": f"Bearer {admin_bearer(ALICE)}"}, timeout=60,
        )
        print(f"    HTTP {r.status_code} {r.text[:220]}")
        if r.status_code != 200:
            fails.append(f"② sync 失败 HTTP {r.status_code}")
            raise SystemExit
        rows = tool_rows(conn, server_id)
        for name, scope, owner, en in rows:
            print(f"    {name}  scope={scope} owner={owner} enabled={en}")
        if len(rows) != 2:
            fails.append(f"② 该同步出 2 个工具，实际 {len(rows)}")
        bad = [n for n, sc, ow, _ in rows if sc != "user" or ow != ALICE]
        if bad:
            fails.append(f"② 这些工具行归属没跟着 server: {bad}（全租户可见了）")
        else:
            print(f"    ✓ ② {len(rows)} 个工具行都归 {ALICE}")

        step(3, "先开一轮，此时模型不该有这些工具")
        if ask(s_alice, ALICE, "你好") != 200:
            fails.append("初始对话失败")
            raise SystemExit
        print("    ✓ 会话建好")

        step(4, f"③ 挂整个 server {server_id}")
        r = requests.post(
            f"{AGENT_URL}/api/sessions/{s_alice}/attachments",
            json={"kind": "mcp_server", "ref_id": server_id},
            headers=bearer(ALICE), timeout=30,
        )
        print(f"    HTTP {r.status_code} {r.text[:200]}")
        if r.status_code != 200:
            fails.append(f"③ 挂载失败 HTTP {r.status_code} —— kind='mcp_server' 没进白名单？")
            raise SystemExit

        step(5, f"④ 提问：{QUESTION}")
        reset_hits()
        mark = message_count(conn, s_alice)
        if ask(s_alice, ALICE, QUESTION) != 200:
            fails.append("④ 挂载后提问失败")
            raise SystemExit
        calls, results, reply = transcript_since(conn, s_alice, mark)
        for n, p in calls:
            print(f"    {n}({json.dumps(p, ensure_ascii=False)})")
        print(f"    回复: {reply[:180]}")
        print(f"    这一轮上游命中: {upstream_hits() or '（零）'}")

        mine = [(n, p) for n, p in calls if "lookup_ticket" in n]
        if not mine:
            fails.append("④ 模型没调那个工具 —— 挂载没生效")
        else:
            name, params = mine[0]
            # 两层前缀都要在：my__（归属）+ mcp_{id}_（server 消歧）
            want = f"my__mcp_{server_id}_lookup_ticket"
            if name != want:
                fails.append(f"④ 工具名不对: {name}，期望 {want}")
            if not params.get("ticket_ref"):
                fails.append(f"④ ticket_ref 没传出来（schema 没到模型面前）: {params}")
            else:
                print(f"    ✓ ④ 参数带类型: ticket_ref={params['ticket_ref']!r}")
        if SECRET_STATUS not in reply:
            fails.append(f"④ 回复里没有 {SECRET_STATUS} —— 远端没真被调通"
                         "（很可能是 remote_tool_name 那一跳剥错了前缀）")
        else:
            print(f"    ✓ ④ 回复带回 {SECRET_STATUS}，远端真的执行了")

        step(6, f"⑤ {BOB} 用同一个 agent 问同一句")
        if ask(s_bob, BOB, QUESTION) != 200:
            fails.append("⑤ bob 提问失败")
        else:
            c2, _, rep2 = transcript_since(conn, s_bob, 0)
            leaked = [n for n, _ in c2 if "lookup_ticket" in n]
            print(f"    工具调用: {[n for n, _ in c2] or '（无）'}")
            if leaked:
                fails.append(f"⑤ bob 的会话里出现了 {leaked}")
            elif SECRET_STATUS in rep2:
                fails.append(f"⑤ bob 的回复里出现了 {SECRET_STATUS}")
            else:
                print("    ✓ ⑤ 别人看不到")

        step(7, "⑥ 摘掉，等收尾，再问一个**没查过的**工单")
        r = requests.delete(
            f"{AGENT_URL}/api/sessions/{s_alice}/attachments/mcp_server/{server_id}",
            headers=bearer(ALICE), timeout=30,
        )
        print(f"    HTTP {r.status_code} {r.text[:160]}")
        # 不信 applies（只反映处理这条请求的副本），自己查 /chat/status
        for _ in range(90):
            if not turn_running(s_alice, ALICE):
                break
            time.sleep(1)

        # 清零之后这一轮的任何命中都是"摘除没生效"的直接证据，不用做减法。
        reset_hits()
        mark = message_count(conn, s_alice)
        if ask(s_alice, ALICE, "那工单 OUC-7742 呢？") != 200:
            fails.append("⑥ 摘掉后提问失败")
        else:
            calls6, results6, _ = transcript_since(conn, s_alice, mark)
            hits = upstream_hits()
            attempted = [n for n, _ in calls6 if "lookup_ticket" in n]
            print(f"    这一轮工具调用: {attempted or '（没调）'}")
            print(f"    这一轮上游命中: {hits or '（零）'}")
            # ⚠️ 判据是**上游有没有被真的打到**，不是有没有 tool_use。
            # 摘除生效后模型仍可能发 tool_use（历史里见过这个工具），引擎回
            # unknown tool 挡住 —— 那是正确行为。
            if hits.get("lookup_ticket", 0) > 0:
                fails.append(f"⑥ 摘掉之后远端仍被调用了 {hits['lookup_ticket']} 次")
            elif any(SECRET_STATUS in x for x in results6):
                fails.append("⑥ 摘掉之后工具仍然返回了真实数据")
            else:
                print("    ✓ ⑥ 摘掉之后远端不再被调用")

    except SystemExit:
        pass
    finally:
        step(8, "清理")
        cleanup(conn, server_id, [s_alice, s_bob])
        print(f"    删掉 server {server_id} 及其工具行、2 个会话")
        conn.close()

    print()
    if fails:
        print("✗ 失败：")
        for f in fails:
            print(f"    - {f}")
        return 1
    print("✓ MCP server 挂载全通：普通用户能建私有 server、sync 归属跟着走、"
          "挂一个 server 进来一批工具、远端真调通、别人看不到、摘掉即失效。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
