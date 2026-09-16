"""专门验收：切到 handoff 专家团时，接力链真的跑起来 + 跨模式切换历史不丢。

    LEA_AGENT_URL=http://127.0.0.1:8082 python scripts/expert_handoff_e2e.py

前置：lea-config / lea-agent 在跑，且有真 LLM provider。

## 为什么单独一条

expert_switch_e2e.py 已经验了"切底座"的通用机制（换人格、取消、失效兜底）。这条只盯
**接力（handoff）团**，因为它是整个第 1 层里唯一一处**两种运行/落史路径的交接**：

    单 agent / 星型团   —— 常驻引擎，历史含原生 tool_use / tool_result（PersistSession）
    handoff 团          —— 没有常驻引擎，每棒是独立会话；只补落 user + 最终答复

对话中途从单 agent 切进 handoff（或反过来），意味着新模式要去读一份**另一种形状写出来
的历史**。这是本层最可能出问题的地方，必须真跑。

## chain_hop 是"链真的跑了"的唯一可信证据

⚠️ 不能从 agent_messages 判断链有没有跑：handoff 的每一棒是独立会话，棒与棒之间的
handoff_to 调用**不落主会话的 agent_messages**（appendChainHistory 只补 user + final）。
所以"看 tool_use 里有没有 handoff_to"是错的判据——上一版就这么误判成"链没跑"。

链真的跑了，证据在 **SSE 的 chain_hop 事件**（runChain 每棒播一条，带 role/expert）。
这个脚本抓 SSE 流里的 chain_hop 来断言接力确实发生、且经过了 entry 和 exit。

## 判据

    ① 直接在 handoff 团上开会话 → SSE 里有 ≥2 条 chain_hop，角色覆盖 entry + exit
    ② 单 agent 阶段记住的事实（工号），切进 handoff 后仍被链读到（单→团 边界）
    ③ 从 handoff 切回单专家 → 事实仍在、人格是该专家（团→单 边界）
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

AGENT_URL = os.getenv("LEA_AGENT_URL", "http://127.0.0.1:8100").rstrip("/")
SECRET = d.env()["SECRET_KEY"]
TENANT = "default"
USER = "expert-handoff-user"

RUN = uuid.uuid4().hex[:4].upper()
MARK_A = f"EXPERT-A-{RUN}"
FACT = f"K-{uuid.uuid4().hex[:6].upper()}"


def mint(user: str) -> str:
    import jwt
    return jwt.encode({"sub": user, "tenant_id": TENANT, "exp": int(time.time()) + 3600},
                      SECRET, algorithm="HS256")


def hdr(user: str) -> dict:
    return {"Authorization": f"Bearer {mint(user)}"}


def turn_running(session_id: str, user: str) -> bool:
    import requests
    try:
        r = requests.get(f"{AGENT_URL}/api/chat/status/{session_id}",
                         headers=hdr(user), timeout=10)
        return r.status_code == 200 and bool(r.json().get("running"))
    except requests.RequestException:
        return False


def ask_sse(session_id: str, message: str, agent_id: int, user: str = USER):
    """发一条消息，把 SSE 流拆成事件列表返回。

    返回 (status, events)，events 是 [(type, data_str), ...]。gin 的 SSE 一条事件形如：
        id:<seq>\\nevent:<type>\\ndata:<json>\\n\\n
    所以按行读：event: 行记下当前类型，data: 行跟它配对。
    """
    import requests
    payload = {"session_id": session_id, "agent_id": agent_id, "message": message}
    r = requests.post(f"{AGENT_URL}/api/chat/stream", json=payload,
                      headers=hdr(user), stream=True, timeout=300)
    if r.status_code != 200:
        print(f"    HTTP {r.status_code}: {r.text[:300]}")
        return r.status_code, []
    events: list[tuple[str, str]] = []
    cur = None
    for line in r.iter_lines(decode_unicode=True):
        if line is None:
            continue
        if line.startswith("event:"):
            cur = line[len("event:"):].strip()
        elif line.startswith("data:"):
            data = line[len("data:"):]
            if data.startswith(" "):
                data = data[1:]
            events.append((cur or "", data))
            cur = None
    # 流结束不等于 turn 的 defer 跑完，读库前先等干净。
    for _ in range(180):
        if not turn_running(session_id, user):
            break
        time.sleep(1)
    return 200, events


def chain_hops(events) -> list[dict]:
    out = []
    for typ, data in events:
        if typ != "chain_hop":
            continue
        try:
            out.append(json.loads(data))
        except json.JSONDecodeError:
            pass
    return out


# ── 库 ────────────────────────────────────────────────────────────────────

def last_reply(conn, session_id: str) -> str:
    with conn.cursor() as cur:
        cur.execute("SELECT role, message FROM agent_messages "
                    "WHERE tenant_id=%s AND session_id=%s ORDER BY id DESC LIMIT 30",
                    (TENANT, session_id))
        for role, raw in cur.fetchall():
            if role != "assistant":
                continue
            content = json.loads(raw).get("content")
            if isinstance(content, str):
                if content.strip():
                    return content
                continue
            txt = "".join(b.get("text", "") for b in (content or [])
                          if isinstance(b, dict) and b.get("type") == "text").strip()
            if txt:
                return txt
    return ""


def active_agent(conn, session_id: str):
    with conn.cursor() as cur:
        cur.execute("SELECT active_agent_id FROM chat_sessions WHERE id=%s", (session_id,))
        r = cur.fetchone()
        return r[0] if r else None


def pick_model(conn) -> str:
    with conn.cursor() as cur:
        cur.execute("SELECT model FROM agents WHERE tenant_id=%s AND enabled=1 "
                    "AND model IS NOT NULL AND model<>'' ORDER BY id LIMIT 1", (TENANT,))
        r = cur.fetchone()
    if not r:
        raise SystemExit("现网没有可抄 model 的 agent")
    return r[0]


def make_agent(conn, model: str, name: str, soul_prompt: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agents "
            "(tenant_id, name, description, model, temperature, max_turns, max_tokens, "
            " kb_mode, memory_mode, timeout_seconds, is_system, enabled, created_by, "
            " owner_scope, soul_prompt) "
            "VALUES (%s, %s, 'handoff e2e，可删', %s, 7, 6, 800, 'none', 'none', 180, 0, 1, "
            "        'expert-handoff-e2e', 'platform', %s)",
            (TENANT, name, model, soul_prompt),
        )
        return cur.lastrowid


def make_team(conn, model: str) -> tuple[int, list[int]]:
    team_agent = make_agent(conn, model, f"接力团T-{RUN}", "你是一个接力专家团。")
    # 引擎自己给 entry/worker/exit 注入了 handoff 脚手架（agentloop/chain.go），所以成员
    # 提示词只需点明角色即可；给的任务足够小，好让 entry→worker→exit 顺下来。
    entry = make_agent(conn, model, f"调度员-{RUN}",
                       "你是调度员，只负责把任务交给下一棒专家，不自己动手。")
    worker = make_agent(conn, model, f"执行专家-{RUN}",
                        "你是执行专家，做完你这部分后把工作交给验收专家。")
    exit_ = make_agent(conn, model, f"验收专家-{RUN}",
                       "你是验收专家，检查并给出面向用户的最终答复。")
    with conn.cursor() as cur:
        cur.execute("INSERT INTO agent_teams (agent_id, strategy, decomposer_prompt, "
                    "synthesizer_prompt, max_subtasks) VALUES (%s, 'handoff', '', '', 5)",
                    (team_agent,))
        team_id = cur.lastrowid
        for aid, role, nm in ((entry, "entry", "调度员"),
                              (worker, "worker", "执行"),
                              (exit_, "exit", "验收")):
            cur.execute("INSERT INTO agent_team_members (team_id, member_agent_id, role, name) "
                        "VALUES (%s, %s, %s, %s)", (team_id, aid, role, nm))
    return team_agent, [entry, worker, exit_]


def set_active_agent(session_id: str, agent_id: int, user: str = USER) -> dict:
    import requests
    r = requests.put(f"{AGENT_URL}/api/sessions/{session_id}/active-agent",
                     json={"agent_id": agent_id}, headers=hdr(user), timeout=30)
    return {"status": r.status_code, "body": r.json() if r.content else {}}


def cleanup(conn, sessions, agent_ids):
    with conn.cursor() as cur:
        for s in sessions:
            cur.execute("DELETE FROM agent_messages WHERE session_id=%s", (s,))
            cur.execute("DELETE FROM chat_sessions WHERE id=%s", (s,))
        for a in agent_ids:
            cur.execute("DELETE FROM agent_team_members WHERE member_agent_id=%s", (a,))
            cur.execute("DELETE FROM agent_teams WHERE agent_id=%s", (a,))
            cur.execute("DELETE FROM agents WHERE id=%s", (a,))


def step(n, title): print(f"\n[{n}] {title}")


def main() -> int:
    d.utf8_stdout()
    conn = d.connect()
    sess = f"handoff-{uuid.uuid4().hex[:10]}"
    agent_ids: list[int] = []
    fails: list[str] = []

    try:
        model = pick_model(conn)
        print(f"用 model={model}；本轮 marker A={MARK_A} 事实={FACT}")
        agent_a = make_agent(
            conn, model, f"专家A-{RUN}",
            f"你是专家A。正常回答问题，并在每条回复末尾另起一行原样输出【{MARK_A}】。")
        team_agent, members = make_team(conn, model)
        agent_ids += [agent_a, team_agent] + members
        print(f"  专家A id={agent_a}  接力团 id={team_agent} members={members}")

        # ── ① 直接在 handoff 团上开会话，看链跑没跑 ────────────────
        step(1, "① 直接在接力团上开会话 → SSE 应有 ≥2 条 chain_hop，覆盖 entry+exit")
        st, ev = ask_sse(sess, f"请团队确认收到我的工号 {FACT}，并给我一句最终答复。", team_agent)
        if st != 200:
            fails.append(f"① 发消息失败 HTTP {st}")
            raise SystemExit
        hops = chain_hops(ev)
        roles = [h.get("role") for h in hops]
        print(f"    chain_hop 条数={len(hops)}  角色序列={roles}")
        for h in hops:
            print(f"      hop#{h.get('index')} {h.get('expert')}/{h.get('role')} "
                  f"→ handedTo={h.get('handedTo')!r}")
        if len(hops) < 2:
            fails.append(f"① chain_hop 只有 {len(hops)} 条 —— 接力链没真正跑起来（或没走 handoff）")
        if "entry" not in roles:
            fails.append("① chain_hop 里没有 entry —— 调度员这一棒没跑")
        if "exit" not in roles:
            fails.append("① chain_hop 里没有 exit —— 没走到验收专家")
        if len(hops) >= 2 and "entry" in roles and "exit" in roles:
            clean = any(r == "worker" for r in roles)
            print(f"    ✓ ① 接力链跑通（{'entry→worker→exit 三棒' if clean else '含兜底转 exit'}）")

        # ── ② 单→团 边界：先让单 agent 记事实，再切团问它 ──────────
        step(2, f"② 单 agent 记住 {FACT}，切进 handoff 团后链仍读得到（单→团 边界）")
        sess2 = f"handoff-b-{uuid.uuid4().hex[:8]}"
        st, _ = ask_sse(sess2, f"你好，请记住我的工号是 {FACT}。", agent_a)
        if st != 200:
            fails.append("② 单 agent 首消息失败")
        else:
            r_a = last_reply(conn, sess2)
            if MARK_A not in r_a:
                fails.append("② 起手不是专家A（无 marker）")
            sw = set_active_agent(sess2, team_agent)
            if sw["status"] != 200:
                fails.append(f"② 切接力团失败 HTTP {sw['status']}")
            else:
                st, ev2 = ask_sse(sess2, "请团队用一句话复述我的工号，作为最终答复。", team_agent)
                hops2 = chain_hops(ev2)
                r_team = last_reply(conn, sess2)
                print(f"    切团后 chain_hop 条数={len(hops2)}  角色={[h.get('role') for h in hops2]}")
                print(f"    最终答复: {r_team[:160]}")
                if len(hops2) < 1:
                    fails.append("② 切团后没有 chain_hop —— 中途切进 handoff 没生效")
                if FACT not in r_team:
                    fails.append(f"② 单→团 丢了历史 —— 链没读到单 agent 阶段的工号 {FACT}")
                if len(hops2) >= 1 and FACT in r_team:
                    print("    ✓ ② 单→团 边界：接力链读到了单 agent 阶段写下的历史")

                # ── ③ 团→单 边界：切回专家A ────────────────────────
                #
                # 人格切没切回，用**确定性证据**判，不看 marker：marker 靠模型每条回复都
                # 记得追加那行，而"再确认工号"这种极短回答它常常省掉——那是 LLM 依从性，不是
                # 底座没切。确定性证据有两条：① active_agent_id 列回到了 agent_a；② 这一轮
                # SSE 里**没有 chain_hop**（还在团里的话必然又播 chain_hop）。marker 只做旁证。
                step(3, f"③ 从接力团切回专家A → 仍记得 {FACT}，且确实回到单 agent（团→单 边界）")
                set_active_agent(sess2, agent_a)
                col_back = active_agent(conn, sess2)
                st, ev3 = ask_sse(sess2, "请用一句话确认你收到了我的工号。", agent_a)
                hops3 = chain_hops(ev3)
                r_back = last_reply(conn, sess2)
                print(f"    active_agent_id={col_back}  本轮 chain_hop 条数={len(hops3)}")
                print(f"    切回专家A答复: {r_back[:160]}")
                if col_back != agent_a:
                    fails.append(f"③ 切回后 active_agent_id 应为 {agent_a}，实际 {col_back}")
                if hops3:
                    fails.append("③ 切回后仍播 chain_hop —— 底座没从团切回单 agent")
                if FACT not in r_back:
                    fails.append(f"③ 团→单 丢了历史 —— 记不起 {FACT}（链写下的历史单 agent 没读入）")
                if MARK_A not in r_back:
                    print("    · （旁证）本轮回复没带 A 的 marker —— 短答常省，不作判据")
                if col_back == agent_a and not hops3 and FACT in r_back:
                    print("    ✓ ③ 团→单 边界：底座切回单 agent，链写下的历史被读入")
            cleanup(conn, [sess2], [])

    except SystemExit:
        pass
    finally:
        cleanup(conn, [sess], agent_ids)
        conn.close()

    print("\n" + "═" * 70)
    if fails:
        print(f"失败 {len(fails)} 条：")
        for f in fails:
            print(f"  ✗ {f}")
        return 1
    print("接力链判据全通。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
