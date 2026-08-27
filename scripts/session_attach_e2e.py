"""验收：会话级挂载 —— 用户在**这一个会话里**临时挂上自己的工具。

    python scripts/mock_stock_api.py 9911 &
    LEA_AGENT_URL=http://127.0.0.1:8080 python scripts/session_attach_e2e.py

前置：lea-config / lea-tools / lea-agent 在跑（infra/docker-compose.verify.yml）。

## 跟 my_prefix_e2e.py 的区别

那个验的是"私有工具**绑到 agent 上**能不能调通"，顺带发现绑定会把工具定义泄给这个
agent 的所有使用者 —— 于是有了共享发布门（硬拦私有资源）和这条另一条路。

这个验的是**挂载**：工具一次都不绑 agent，只挂在会话上。所以它同时证明两件事 ——
用户的工具有地方用了，而且用的方式不泄漏。

## 五条判据

    ① 挂上之后模型真的能调，参数带类型（schema 走通了整条链）
    ② 同一个 agent，另一个用户看不到这个工具（挂载只影响一个会话）
    ③ **Drop 掉重建之后工具还在**  ← 直接验那个坑：只改内存的话这里会失败
    ④ 摘掉之后工具**不再被执行**
    ⑤ 挂一个自己够不着的（别人的私有工具）→ 静默丢弃，会话照常能用

③ 是这个脚本存在的主要理由。lea-agent 会回收闲置会话（ensureCapacity），回收不丢
对话但会丢内存对象 —— 挂载如果只存在内存里，用户吃完饭回来工具就没了，而且没有任何
提示。单测覆盖不到这条，它需要真的走一遍"落库 → 丢对象 → 重建 → 读回来"。

⑤ 验的是容错方向：挂载记录没有外键，资源随时会失效。报错的话用户的老会话直接打不开，
那比少一个工具糟得多。

## ⚠️ ④ 的判据不能是"有没有 tool_use"

这一条排查了四轮才弄对，值得写下来。摘除生效之后，模型**仍然会发出 tool_use** ——
它在历史里见过这个工具，以为还能用。引擎回一句
`Tool error: unknown tool "my__stock_lookup"` 把它挡住，这是**正确行为**。

所以 tool_use 只表示"意图"，不表示"能力"。判据必须落在**工具有没有真的执行**上：
看 tool_result 里有没有那个编不出来的锚点值（青鸾-7F），以及假上游的访问日志里有没有
对应的请求。前三版断言 tool_use 不存在，于是一个完全正确的实现被判了三次失败。

同一类陷阱在 ① 那边是反过来的：光看模型答得对不对也不行 —— 它编一个温度/分区出来
毫不费力。两边都得看**不可伪造的证据**。
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

AGENT_URL = os.getenv("LEA_AGENT_URL", "http://127.0.0.1:8100")
CONFIG_URL = os.getenv("LEA_CONFIG_URL", "http://127.0.0.1:8500").rstrip("/")
SECRET = d.env()["SECRET_KEY"]
TENANT = "default"

OWNER = "attach-owner"
STRANGER = "attach-other"
AGENT_ID = int(os.getenv("LEA_AGENT_ID", "2"))

BARE_NAME = "stock_lookup"
EXPOSED_NAME = "my__stock_lookup"
UPSTREAM = os.getenv("MOCK_STOCK_URL", "http://host.docker.internal:9911/stock")

QUESTION = "帮我查一下 WH-8834 这个库位现在的情况。"

SCHEMA = {
    "type": "object",
    "required": ["warehouse_ref"],
    "properties": {"warehouse_ref": {"type": "string", "description": "库位编号，例如 WH-8834"}},
}
ADAPTER = {
    "method": "GET",
    "endpoint": UPSTREAM,
    "request_mapping": {"body": {"warehouse_ref": "{{ warehouse_ref }}"}},
    "headers_template": {},
}


# ── 库 ────────────────────────────────────────────────────────────────────

def mk_tool(conn, owner: str, name: str = BARE_NAME) -> int:
    """建一个属于 owner 的私有工具。**不绑任何 agent** —— 这是重点。"""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO tools (tenant_id, name, display_name, description, category, "
            "execution_type, schema_json, http_adapter_config, enabled, is_builtin, "
            "owner_scope, owner_user_id) "
            "VALUES (%s,%s,%s,%s,'general','api',%s,%s,1,0,'user',%s)",
            (TENANT, name, "库位查询", "按库位编号查当前库存所在分区和数量。",
             json.dumps(SCHEMA, ensure_ascii=False),
             json.dumps(ADAPTER, ensure_ascii=False), owner),
        )
        conn.commit()
        return cur.lastrowid


def cleanup(conn, tool_ids: list[int], sessions: list[str]) -> None:
    with conn.cursor() as cur:
        for s in sessions:
            cur.execute("DELETE FROM session_attachments WHERE session_id=%s", (s,))
            cur.execute("DELETE FROM agent_messages WHERE session_id=%s", (s,))
            # ⚠️ chat_sessions 的主键列叫 `id`，**不是** session_id —— 跟
            # agent_messages / session_attachments / session_plans 那几张都不一样。
            cur.execute("DELETE FROM chat_sessions WHERE id=%s", (s,))
        for t in tool_ids:
            cur.execute("DELETE FROM agent_tool_bindings WHERE tool_id=%s", (t,))
            cur.execute("DELETE FROM tools WHERE id=%s", (t,))
    conn.commit()


def attachment_rows(conn, session_id: str) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT kind, ref_id FROM session_attachments "
            "WHERE tenant_id=%s AND session_id=%s ORDER BY id",
            (TENANT, session_id),
        )
        return cur.fetchall()


# ── HTTP ──────────────────────────────────────────────────────────────────

def mint(user: str) -> str:
    import jwt
    return jwt.encode(
        {"sub": user, "tenant_id": TENANT, "exp": int(time.time()) + 3600},
        SECRET, algorithm="HS256",
    )


def hdr(user: str) -> dict:
    return {"Authorization": f"Bearer {mint(user)}"}


def turn_running(session_id: str, user: str) -> bool:
    """当前有没有一轮在生成。用现成的 /chat/status —— 它是跨副本有效的。"""
    import requests
    try:
        r = requests.get(f"{AGENT_URL}/api/chat/status/{session_id}",
                         headers=hdr(user), timeout=10)
        if r.status_code != 200:
            return False
        return bool(r.json().get("running"))
    except requests.RequestException:
        return False


def ask(session_id: str, user: str, message: str) -> int:
    """发一条消息，**等这一轮真的收尾**再返回。

    ⚠️ 光排空 SSE 流不够。流结束和"turn 的 defer 跑完"是两件事：落库、清 turnCancel、
    以及 rebuildIfPending 都在流结束**之后**。而挂载/摘除的生效判定看的正是
    HasActiveTurn —— 不等干净就发下一条，会撞上"上一轮还没收尾"的窗口，测出来的
    失败其实是竞态而不是功能问题（这一条排查了三轮才认出来）。

    /chat/status 是跨副本有效的（没有会话对象时查库），所以拿它当收尾信号是可靠的。
    """
    import requests
    r = requests.post(
        f"{AGENT_URL}/api/chat/stream",
        json={"session_id": session_id, "agent_id": AGENT_ID, "message": message},
        headers=hdr(user), stream=True, timeout=180,
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


def attach(session_id: str, user: str, kind: str, ref_id: int) -> dict:
    import requests
    r = requests.post(
        f"{AGENT_URL}/api/sessions/{session_id}/attachments",
        json={"kind": kind, "ref_id": ref_id}, headers=hdr(user), timeout=30,
    )
    return {"status": r.status_code, "body": r.json() if r.content else {}}


def detach(session_id: str, user: str, kind: str, ref_id: int) -> dict:
    import requests
    r = requests.delete(
        f"{AGENT_URL}/api/sessions/{session_id}/attachments/{kind}/{ref_id}",
        headers=hdr(user), timeout=30,
    )
    return {"status": r.status_code, "body": r.json() if r.content else {}}


def list_attached(session_id: str, user: str) -> dict:
    import requests
    r = requests.get(
        f"{AGENT_URL}/api/sessions/{session_id}/attachments",
        headers=hdr(user), timeout=30,
    )
    return {"status": r.status_code, "body": r.json() if r.content else {}}


def transcript(conn, session_id: str) -> tuple[list, list, str]:
    """(工具调用, 工具返回, 最后一段文本)。真相从库里读。"""
    calls, results, reply = [], [], ""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT role, message FROM agent_messages "
            "WHERE tenant_id=%s AND session_id=%s ORDER BY id",
            (TENANT, session_id),
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


def message_count(conn, session_id: str) -> int:
    """当前消息条数，当"从这里往后看"的书签用。"""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM agent_messages WHERE tenant_id=%s AND session_id=%s",
            (TENANT, session_id),
        )
        return cur.fetchone()[0]


def calls_since(conn, session_id: str, mark: int) -> list[tuple[str, dict]]:
    """只看第 mark 条之后新增的消息里的工具调用。

    比"累计次数有没有涨"可靠：模型可能在回复里复述历史结果，也可能这一轮压根没调
    工具，两种情况累计值都不变 —— 拿累计值当判据就分不出"工具没了"和"没必要调"。
    """
    out = []
    with conn.cursor() as cur:
        cur.execute(
            "SELECT message FROM agent_messages "
            "WHERE tenant_id=%s AND session_id=%s ORDER BY id LIMIT 18446744073709551615 OFFSET %s",
            (TENANT, session_id, mark),
        )
        rows = cur.fetchall()
    for (raw,) in rows:
        for b in json.loads(raw).get("content") or []:
            if isinstance(b, dict) and b.get("type") == "tool_use":
                out.append((b.get("name", ""), b.get("input") or {}))
    return out


def tool_results_since(conn, session_id: str, mark: int) -> list[str]:
    """第 mark 条之后新增消息里的 tool_result 内容。

    区分"模型尝试调用"和"工具真的执行了"必须看这个。摘除生效之后模型仍然可能发出
    tool_use（它记得上一轮用过这个工具），但引擎会回 unknown tool —— 那是正确行为，
    不是 bug。判据得落在返回值上。
    """
    out = []
    with conn.cursor() as cur:
        cur.execute(
            "SELECT message FROM agent_messages "
            "WHERE tenant_id=%s AND session_id=%s ORDER BY id "
            "LIMIT 18446744073709551615 OFFSET %s",
            (TENANT, session_id, mark),
        )
        rows = cur.fetchall()
    for (raw,) in rows:
        for b in json.loads(raw).get("content") or []:
            if isinstance(b, dict) and b.get("type") == "tool_result":
                c = b.get("content")
                out.append(c if isinstance(c, str) else json.dumps(c, ensure_ascii=False))
    return out


def step(n, title): print(f"\n[{n}] {title}")


def main() -> int:
    d.utf8_stdout()
    conn = d.connect()
    s_own = f"attach-o-{uuid.uuid4().hex[:10]}"
    s_other = f"attach-s-{uuid.uuid4().hex[:10]}"
    tool_ids: list[int] = []
    fails: list[str] = []

    try:
        step(1, "建两个私有工具，都**不绑** agent")
        mine = mk_tool(conn, OWNER)
        others = mk_tool(conn, STRANGER, name="secret_tool")
        tool_ids += [mine, others]
        print(f"    我的 tool_id={mine}   别人的 tool_id={others}")

        step(2, "先开一轮对话（建出会话行），此时模型不该有这个工具")
        if ask(s_own, OWNER, "你好") != 200:
            fails.append("初始对话失败")
            raise SystemExit
        calls, _, _ = transcript(conn, s_own)
        if any(BARE_NAME in n for n, _ in calls):
            fails.append("还没挂就有这个工具了")
        else:
            print("    ✓ 挂载前模型没有这个工具")

        step(3, f"挂上 tool {mine}")
        r = attach(s_own, OWNER, "tool", mine)
        print(f"    HTTP {r['status']}  {json.dumps(r['body'], ensure_ascii=False)}")
        if r["status"] != 200:
            fails.append(f"挂载失败 HTTP {r['status']}")
            raise SystemExit
        if r["body"].get("applies") != "next_message":
            fails.append(f"空闲会话该立刻生效，applies={r['body'].get('applies')}")
        rows = attachment_rows(conn, s_own)
        print(f"    库里: {rows}")
        # pymysql 返回的是元组的元组，别拿它跟 list 比 —— 第一版写成
        # `!= [("tool", mine)]` 于是永远"失败"，而打印出来看着一模一样。
        if list(rows) != [("tool", mine)]:
            fails.append(f"落库不对: {rows}")

        step(4, f"① 提问：{QUESTION}")
        if ask(s_own, OWNER, QUESTION) != 200:
            fails.append("挂载后提问失败")
            raise SystemExit
        calls, results, reply = transcript(conn, s_own)
        mine_calls = [(n, p) for n, p in calls if BARE_NAME in n]
        for n, p in mine_calls:
            print(f"    {n}({json.dumps(p, ensure_ascii=False)})")
        print(f"    回复: {reply[:150]}")
        if not mine_calls:
            fails.append("① 模型没调这个工具 —— 挂载没生效")
        else:
            name, params = mine_calls[0]
            if name != EXPOSED_NAME:
                fails.append(f"① 名字不对: {name}")
            if not isinstance(params.get("warehouse_ref"), str) or not params["warehouse_ref"]:
                fails.append(f"① 参数没传出来: {params}")
            else:
                print(f"    ✓ ① 调通，warehouse_ref={params['warehouse_ref']!r}")
        if MAGIC_ZONE not in reply:
            fails.append(f"① 回复里没有 {MAGIC_ZONE} —— 工具没真执行")
        else:
            print(f"    ✓ ① 回复带回 {MAGIC_ZONE}，工具真的执行了")

        step(5, f"② {STRANGER} 用同一个 agent 问同一句")
        if ask(s_other, STRANGER, QUESTION) != 200:
            fails.append("stranger 提问失败")
        else:
            c2, r2, rep2 = transcript(conn, s_other)
            leaked = [n for n, _ in c2 if BARE_NAME in n]
            print(f"    工具调用: {[n for n, _ in c2] or '（无）'}")
            if leaked:
                fails.append(f"② 别人的会话里出现了 {leaked}")
            elif MAGIC_ZONE in rep2:
                fails.append(f"② 别人的回复里出现了 {MAGIC_ZONE}")
            else:
                print("    ✓ ② 挂载只影响一个会话")

        step(6, "③ 丢掉内存里的会话对象，再问一次 —— 工具必须还在")
        # 通过再挂一次同一个工具来触发 Drop（幂等 + 顺带丢对象），等价于会话被淘汰。
        # 这就是那个坑的直接复现路径：只改内存的话这一步之后工具就没了。
        again = attach(s_own, OWNER, "tool", mine)
        print(f"    重挂（幂等）→ applies={again['body'].get('applies')}")

        # ⚠️ 必须问一个**没查过的**库位号。
        #
        # 第一版重复问 WH-8834，结果是"累计调用 1 次"判定失败 —— 但那不能证明工具丢了：
        # 历史里已经有「WH-8834 在 青鸾-7F」这个答案，模型完全可以直接从上下文回答而
        # 不调工具。两种情况（工具没了 / 模型没必要调）在那个问法下长得一模一样。
        #
        # 换一个没出现过的号，模型只有调工具才可能知道答案。
        before = len([n for n, _ in transcript(conn, s_own)[0] if BARE_NAME in n])
        if ask(s_own, OWNER, "那 WH-7742 呢？") != 200:
            fails.append("重建后提问失败")
        else:
            calls6, _, rep6 = transcript(conn, s_own)
            after = len([n for n, _ in calls6 if BARE_NAME in n])
            new_refs = [p.get("warehouse_ref") for n, p in calls6 if BARE_NAME in n]
            print(f"    调用过的库位: {new_refs}")
            print(f"    回复: {rep6[:120]}")
            if after <= before:
                fails.append("③ 重建之后工具丢了 —— 挂载没有真正落库/读回")
            elif "WH-7742" not in new_refs:
                fails.append(f"③ 调了工具但参数不对: {new_refs}")
            else:
                print("    ✓ ③ 重建之后工具还在（落库→丢对象→重建→读回 走通）")

        step(7, "④ 摘掉，等本轮真的收尾，再问一次")
        rm = detach(s_own, OWNER, "tool", mine)
        applies = rm["body"].get("applies")
        print(f"    HTTP {rm['status']}  applies={applies}")
        if attachment_rows(conn, s_own):
            fails.append("④ 摘了但库里还有行")

        # ⚠️ **不能信 applies**，它在多副本下只反映"处理这条请求的那个副本"的状态。
        #
        # 实测（nginx + agent 日志对照）：
        #   14:47:42  DELETE /attachments  → 副本 A   A 内存里没这个会话，Drop 返回
        #                                            true，于是报 applies=next_message
        #   14:47:42  POST /chat/stream    → 副本 B   rt=16.7s，这一轮正在跑
        #
        # 于是"下一条消息"其实落在了那一轮的尾巴上，而工具集在 engine.Build 那一刻
        # 就冻住了 —— 它手里必然还有刚摘掉的工具。这不是 bug，是 applies 只能表达
        # 本副本视角。
        #
        # 所以判据自己去查：/chat/status 是跨副本有效的（没有会话对象时查库）。
        print("    等当前轮收尾（不信 applies，自己查 /chat/status）…")
        for _ in range(90):
            if not turn_running(s_own, OWNER):
                break
            time.sleep(1)
        else:
            fails.append("④ 等了 90s 当前轮还没结束")

        mark = message_count(conn, s_own)
        if ask(s_own, OWNER, "WH-3310 这个库位呢？") != 200:
            fails.append("摘掉后提问失败")
        else:
            new_calls = [(n, p) for n, p in calls_since(conn, s_own, mark) if BARE_NAME in n]
            print(f"    这一轮的工具调用: {[n for n, _ in new_calls] or '（没调这个工具）'}")
            # 摘除后如果模型仍"调"了这个工具，要分清两种完全不同的情况：
            #
            #   工具还在工具集里   → 真 bug，摘除没生效
            #   工具已经不在了     → 引擎会返回 unknown tool，模型是在"尝试"而已
            #
            # 判据落在**上游有没有被真的打到**：假上游的访问日志是不可伪造的。
            # 光看 tool_use 块分不出来 —— 那只表示模型发出了调用意图。
            for _, params in new_calls:
                print(f"      参数: {params}")
            if new_calls:
                results = tool_results_since(conn, s_own, mark)
                print(f"    工具返回: {[r[:80] for r in results] or '（无）'}")
                if any(MAGIC_ZONE in r for r in results):
                    fails.append(f"④ 摘掉之后工具**真的执行了**: {[n for n, _ in new_calls]}")
                else:
                    print("    ✓ ④ 模型尝试调用但被挡住了（工具已不在工具集里）")
            else:
                print("    ✓ ④ 摘掉之后模型不再有它")

        step(8, f"⑤ 挂别人的私有工具 {others} —— 该被静默丢弃，会话照常能用")
        r5 = attach(s_own, OWNER, "tool", others)
        print(f"    挂载接口 HTTP {r5['status']}（不在写入时校验归属，见 handler 注释）")
        code = ask(s_own, OWNER, "你还在吗")
        if code != 200:
            fails.append(f"⑤ 挂了够不着的工具之后会话打不开了（HTTP {code}）")
        else:
            c5, _, _ = transcript(conn, s_own)
            if any("secret_tool" in n for n, _ in c5):
                fails.append("⑤ 别人的私有工具被解析进来了 —— 越权")
            else:
                print("    ✓ ⑤ 静默丢弃，会话照常可用")

        step(9, "列表接口")
        lst = list_attached(s_own, OWNER)
        print(f"    {json.dumps(lst['body'], ensure_ascii=False)}")

    except SystemExit:
        pass
    finally:
        step(10, "清理")
        cleanup(conn, tool_ids, [s_own, s_other])
        print(f"    删掉 {len(tool_ids)} 个工具、2 个会话的挂载和消息")
        conn.close()

    print()
    if fails:
        print("✗ 失败：")
        for f in fails:
            print(f"    - {f}")
        return 1
    print("✓ 会话级挂载全通：挂上能用、只影响一个会话、重建后还在、摘掉即失效、"
          "够不着的静默丢弃。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
