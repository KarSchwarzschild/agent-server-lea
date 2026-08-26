"""验收：用户私有工具在**真链路**上调得通，且别人看不见。

    python scripts/mock_stock_api.py 9911 &          # 先起假上游
    LEA_AGENT_URL=http://127.0.0.1:8080 python scripts/my_prefix_e2e.py

前置：lea-config / lea-tools / lea-agent 在跑（infra/docker-compose.verify.yml）。

## 为什么这一条必须真跑

`my__` 前缀是**两端**的约定：

    下发   lea-config 的 exposed_name()   库里 stock_lookup → 发给模型 my__stock_lookup
    执行   lea-tools 的 resolve_tool()    收到 my__stock_lookup → 剥前缀 → 只在
                                          owner_scope=user 里找

单测把两端各自钉住了，但它们在**两个仓、两个进程、两套 mock** 里。以前正是这个
形状出的事：加前缀那一半写好了却没有调用方，下发的是裸名，于是用户私有工具
结构上调不通（模型调裸名 → resolve_tool 走 else 分支 → owner_scope != USER →
404）。全仓 grep `my__` 有命中、有测试、还全绿。

MCP 那边也栽过同一跤：`mcp_27_lookup_ticket` 带着我们自己的前缀发去了远端，
所有单测都 mock 掉了 call_tool，只有真链路抓到。

所以这个脚本只信真东西：真库、真模型、真 HTTP。

## 判据

    ① 模型调的名字是 my__stock_lookup（**带**前缀）
    ② warehouse_ref 传出来了（schema 到位）
    ③ 回复里有 青鸾-7F —— 编不出来的值，等于工具真的执行了
    ④ 换一个用户问同一句，这个工具**不出现**在他的清单里

④ 是另一半：前缀不只是消歧，它同时是隔离。少了 ④，"给所有人都加前缀"也能让
①②③ 全绿。
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import time
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import _dbconn as d
from mock_stock_api import MAGIC_ZONE

AGENT_URL = os.getenv("LEA_AGENT_URL", "http://127.0.0.1:8100") + "/api/chat/stream"
CONFIG_URL = os.getenv("LEA_CONFIG_URL", "http://127.0.0.1:8500").rstrip("/")
SECRET = d.env()["SECRET_KEY"]
TENANT = "default"

OWNER = "myprefix-owner"      # 建工具的人
STRANGER = "myprefix-other"   # 同租户的另一个人，不该看见

AGENT_ID = int(os.getenv("LEA_AGENT_ID", "2"))

BARE_NAME = "stock_lookup"            # 库里的名字
EXPOSED_NAME = "my__stock_lookup"     # 发给模型的名字

# 上游地址：lea-tools 在容器里，打宿主要走 host.docker.internal。
UPSTREAM = os.getenv("MOCK_STOCK_URL", "http://host.docker.internal:9911/stock")

# ⚠️ 问句里**不出现** warehouse_ref 这个词，也不出现"库存"以外的提示。
# 参数名要靠 schema 给，不是靠我们口述 —— 口述了就测不出 schema 到没到。
QUESTION = "帮我查一下 WH-8834 这个库位现在的情况。"

SCHEMA = {
    "type": "object",
    "required": ["warehouse_ref"],
    "properties": {
        "warehouse_ref": {
            "type": "string",
            "description": "库位编号，例如 WH-8834",
        }
    },
}

ADAPTER = {
    "method": "GET",
    "endpoint": UPSTREAM,
    "request_mapping": {"body": {"warehouse_ref": "{{ warehouse_ref }}"}},
    "headers_template": {},
}


# ── 库操作 ────────────────────────────────────────────────────────────────

def seed(conn) -> int:
    """建一个属于 OWNER 的私有工具并绑到 AGENT_ID。返回 tool_id。"""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO tools (tenant_id, name, display_name, description, category, "
            "execution_type, schema_json, http_adapter_config, enabled, is_builtin, "
            "owner_scope, owner_user_id) "
            "VALUES (%s,%s,%s,%s,'general','api',%s,%s,1,0,'user',%s)",
            (TENANT, BARE_NAME, "库位查询", "按库位编号查当前库存所在分区和数量。",
             json.dumps(SCHEMA, ensure_ascii=False),
             json.dumps(ADAPTER, ensure_ascii=False), OWNER),
        )
        tool_id = cur.lastrowid
        cur.execute(
            "INSERT INTO agent_tool_bindings (agent_id, tool_id, enabled) VALUES (%s,%s,1)",
            (AGENT_ID, tool_id),
        )
    conn.commit()
    return tool_id


def cleanup(conn, tool_id: int, sessions: list[str]) -> None:
    with conn.cursor() as cur:
        cur.execute("DELETE FROM agent_tool_bindings WHERE tool_id=%s", (tool_id,))
        cur.execute("DELETE FROM tools WHERE id=%s", (tool_id,))
        for s in sessions:
            cur.execute("DELETE FROM agent_messages WHERE session_id=%s", (s,))
    conn.commit()


# ── 链路操作 ──────────────────────────────────────────────────────────────

def mint(user: str) -> str:
    import jwt
    return jwt.encode(
        {"sub": user, "tenant_id": TENANT, "exp": int(time.time()) + 3600},
        SECRET, algorithm="HS256",
    )


def ask(session_id: str, user: str, message: str) -> int:
    import requests
    r = requests.post(
        AGENT_URL,
        json={"session_id": session_id, "agent_id": AGENT_ID, "message": message},
        headers={"Authorization": f"Bearer {mint(user)}"},
        stream=True, timeout=180,
    )
    if r.status_code != 200:
        print(f"  HTTP {r.status_code}: {r.text[:300]}")
        return r.status_code
    for _ in r.iter_lines(decode_unicode=True):
        pass
    return 200


def tool_results(conn, session_id: str) -> list[str]:
    """把 tool_result 块的内容抽出来。

    区分"被下发了但执行被挡住"和"下发了而且真执行了"必须看这个 —— 光看模型的
    回复分不出来：执行失败时模型会**照样编一个答案**出来（实测 stranger 那轮
    编了"B12 区 / 247 件"，跟上游真实返回的 青鸾-7F / 2291 毫无关系）。
    两种情况的最终回复都是一段像模像样的中文。
    """
    out = []
    with conn.cursor() as cur:
        cur.execute(
            "SELECT role, message FROM agent_messages "
            "WHERE tenant_id=%s AND session_id=%s ORDER BY id",
            (TENANT, session_id),
        )
        rows = cur.fetchall()
    for _role, raw in rows:
        for b in (json.loads(raw).get("content") or []):
            if isinstance(b, dict) and b.get("type") == "tool_result":
                c = b.get("content")
                out.append(c if isinstance(c, str) else json.dumps(c, ensure_ascii=False))
    return out


def transcript(conn, session_id: str) -> tuple[list[tuple[str, dict]], str]:
    """返回 (工具调用列表, 最后一段文本回复)。真相从库里读，不看 SSE。"""
    calls, reply = [], ""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT role, message FROM agent_messages "
            "WHERE tenant_id=%s AND session_id=%s ORDER BY id",
            (TENANT, session_id),
        )
        rows = cur.fetchall()
    for role, raw in rows:
        msg = json.loads(raw)
        if role != "assistant":
            continue
        for b in msg.get("content") or []:
            if isinstance(b, dict) and b.get("type") == "tool_use":
                calls.append((b.get("name", ""), b.get("input") or {}))
        t = "".join(
            b.get("text", "") for b in (msg.get("content") or []) if isinstance(b, dict)
        ).strip()
        if t:
            reply = t
    return calls, reply


def step(n: int, title: str) -> None:
    print(f"\n[{n}] {title}")


def main() -> int:
    d.utf8_stdout()
    conn = d.connect()
    s_owner = f"myprefix-o-{uuid.uuid4().hex[:10]}"
    s_other = f"myprefix-s-{uuid.uuid4().hex[:10]}"
    tool_id = None
    failures: list[str] = []

    try:
        step(1, f"建私有工具 {BARE_NAME}（owner={OWNER}）并绑到 agent {AGENT_ID}")
        tool_id = seed(conn)
        print(f"    tool_id={tool_id}  上游={UPSTREAM}")

        step(2, "确认下发清单里是带前缀的名字")
        # 这一步是**可选**的：验证台默认只暴露 nginx，lea-config 不发布端口。
        # 连不上就跳过 —— 后面 ①②③④ 才是真判据，它们只依赖对话链路。
        # 早期版本这里连接被拒直接把整个脚本崩掉了（只挡了非 200，没挡 ConnectionError），
        # 于是一个**辅助**检查决定了主验收跑不跑得成。
        import requests
        try:
            cfg = requests.get(
                f"{CONFIG_URL}/internal/agent-config/{AGENT_ID}?tenant_id={TENANT}",
                timeout=10,
            )
            if cfg.status_code == 200:
                names = [t["name"] for t in cfg.json().get("tools", [])]
                print(f"    清单: {names}")
                if EXPOSED_NAME in names:
                    print(f"    ✓ {EXPOSED_NAME} 在清单里")
                else:
                    failures.append(f"下发清单里没有 {EXPOSED_NAME}，实际 {names}")
            else:
                print(f"    (跳过：HTTP {cfg.status_code})")
        except requests.RequestException as e:
            print(f"    (跳过：{CONFIG_URL} 连不上 —— {type(e).__name__})")

        step(3, f"{OWNER} 提问：{QUESTION}")
        t0 = time.time()
        if ask(s_owner, OWNER, QUESTION) != 200:
            failures.append("owner 这轮 HTTP 非 200")
            raise SystemExit
        calls, reply = transcript(conn, s_owner)
        print(f"    {time.time()-t0:.1f}s   工具调用 {len(calls)} 次")
        for n, p in calls:
            print(f"      {n}({json.dumps(p, ensure_ascii=False)})")
        print(f"    回复: {reply[:160]}")

        mine = [(n, p) for n, p in calls if n.endswith(BARE_NAME)]
        if not mine:
            failures.append("模型没调这个工具 —— 没到它面前")
        else:
            name, params = mine[0]
            if name == EXPOSED_NAME:
                print(f"    ✓ ① 调的是 {name}（带前缀）")
            else:
                failures.append(f"① 调的是 {name!r}，期望 {EXPOSED_NAME!r}")

            ref = params.get("warehouse_ref")
            if isinstance(ref, str) and ref:
                print(f"    ✓ ② warehouse_ref = {ref!r}")
            else:
                failures.append(f"② warehouse_ref 没传出来: {params!r}")

        if MAGIC_ZONE in reply:
            print(f"    ✓ ③ 回复里有 {MAGIC_ZONE} —— 工具真的执行了")
        else:
            failures.append(f"③ 回复里没有 {MAGIC_ZONE}，工具没执行成功")

        step(4, f"{STRANGER} 问同一句 —— 这个工具不该出现")
        if ask(s_other, STRANGER, QUESTION) != 200:
            failures.append("stranger 这轮 HTTP 非 200")
        else:
            calls2, reply2 = transcript(conn, s_other)
            results2 = tool_results(conn, s_other)
            leaked = [n for n, _ in calls2 if BARE_NAME in n]
            print(f"    工具调用 {len(calls2)} 次: {[n for n, _ in calls2] or '（无）'}")
            for r in results2:
                print(f"    工具返回: {r[:140]}")
            print(f"    回复: {reply2[:120]}")

            # 分两层判，因为这两层的严重程度差一个量级：
            #
            #   下发层  别人的工具清单里出现了这个工具 —— 名字、描述、schema 全泄了
            #   执行层  别人真的拿到了数据
            #
            # 只报一句"泄漏"会把它们混成一件事，而修的地方完全不同。
            got_data = any(MAGIC_ZONE in r for r in results2) or MAGIC_ZONE in reply2
            if got_data:
                failures.append(f"④-执行 别人拿到了真数据（{MAGIC_ZONE}）—— 越权执行")
            if leaked:
                failures.append(
                    f"④-下发 别人的清单里有 {leaked} —— 私有工具的定义泄漏给了同 agent 的其他人"
                    + ("；但执行被挡住了（返回里没有真数据）" if not got_data else "")
                )
            if not leaked and not got_data:
                print(f"    ✓ ④ {STRANGER} 够不着这个工具")

    except SystemExit:
        pass
    finally:
        step(5, "清理")
        if tool_id:
            cleanup(conn, tool_id, [s_owner, s_other])
            print(f"    删掉 tool {tool_id}、它的绑定、两个会话的消息")
        conn.close()

    print()
    if failures:
        print("✗ 失败：")
        for f in failures:
            print(f"    - {f}")
        return 1
    print("✓ 用户私有工具在真链路上跑通了：下发带前缀、执行剥前缀、别人看不见。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
