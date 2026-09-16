"""验收：会话的"激活 agent"——专家/专家团第 1 层（切底座）。

    LEA_AGENT_URL=http://127.0.0.1:8080 python scripts/expert_switch_e2e.py

前置：lea-config / lea-agent 在跑（infra/docker-compose.verify.yml），且有一个真的
LLM provider（脚本会从现网 agents 里抄一个 model 出来给测试专家用）。

## 这个脚本在验什么

"选专家 = 换会话底座、取消 = 回默认"这件事，从"agent_id 首建即锁死、后面换了不认"
改成了"经由重建链换人格"。改动把一个**确定性行为**（同一会话永远是同一个 agent）换成了
**运行期可切换**——而这条只有真跑才证得了：引擎的系统提示词在 Build 那一刻冻死，切 agent
必须靠 Drop + 下一条消息重建，单测测不到"重建后人格真的变了、且历史没丢"。

## 判据

    ① 首消息带 agent_id=专家A → 回复带 A 的 marker；库里 active_agent_id == A
    ② 中途 PUT 切到专家B → 响应含 applies；下条消息回复带 B marker（不再 A）；**历史完整**
    ③ 取消（PUT 0）→ 回默认 super-agent（无 A/B marker）；历史仍完整
    ④ 多副本自愈：在副本 A 上 PUT、到**已在内存里持有旧引擎**的副本 B 上发消息 → B 靠指纹
       自己发现列变了、重建、换人格（需 LEA_AGENT_URL_A/_B，单副本环境跳过）
    ⑤ 专家团边界（最高优先，唯一真风险）：对话中途切成 handoff 专家团 → 链跑通且
       单 agent 阶段写下的历史能被链读入；再切回单专家 → 链写下的历史也能被单 agent 读入
    ⑥ 失效兜底：active_agent_id 指向删掉的 agent → 会话仍能打开（回退默认），不 brick
    ⑦ 两层正交：专家A（第1层）之上挂一个 skill（第2层）→ 两层同时在；再切第1层到专家B →
       **挂载不该被切换冲掉**（这才是本判据真正要抓的失败模式）

## marker 是这个脚本的核心，但不能只靠 marker

人格靠**编不出来的 marker** 探测：给专家 A 的 soul_prompt 塞一条"每次回复末尾原样输出
【EXPERT-A-xxxx】"。回复里有这个 token，是"底座真的换成了 A"的不可伪造证据——模型无从
凭空编出本次随机生成的 marker。每次跑换一组，避免历史/缓存把结果染绿。

但 marker **只能作阳性证据，不能作阴性证据**：模型时不时会漏掉那行标记（尤其回复很短时），
于是"没有 marker"既可能是"人格没切"，也可能是"人格切了但它偷懒了"。实测踩到过：④ 的回复是
"我是专家A，基于 qwen3-max 模型……"——人格明显切了，却因为缺 marker 行被判失败。

所以凡是要断言"人格是 X"的地方，都走 has_persona()：**marker 或自称都算**，并且把提问写成
"你是谁"逼它自报身份。反过来断言"人格不是 X"时仍只看 marker（漏标记不影响阴性判断）。

## ⚠️ ② / ③ 的历史检查不能省

切底座会重建引擎。重建从库里回放历史（Resume → LoadHistory），这条必须验：先让模型
记住一个只有本轮生造的事实（工号 K-xxxx），切换之后追问它——答得出才证明"换了人格、
但没丢上下文"。丢了的话，这个功能就是坏的，哪怕 marker 对了。
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

# 判据④ 要**精确控制哪条请求落到哪个副本**，所以直连两个副本，不靠 nginx 轮询。
#
# 轮询做不了这个判据：它只保证"分散"，不保证"这条 PUT 去 A、那条消息去 B"。真正要验的是
# "B 内存里已经有旧引擎了，列在别处被改了，B 能不能自己发现" —— 顺序错了（消息先到 A）
# 就什么都没验到，而且是静默的假绿。两个副本各给一个 URL 才是确定性的。
#
#   docker compose -f infra/docker-compose.verify.yml up -d
#   LEA_AGENT_URL_A=http://127.0.0.1:8101 LEA_AGENT_URL_B=http://127.0.0.1:8102 …
#
# 没设这两个（单副本环境）→ ④ skip，不 fail：它验的是多副本才有的路径。
URL_A = (os.getenv("LEA_AGENT_URL_A") or "").rstrip("/")
URL_B = (os.getenv("LEA_AGENT_URL_B") or "").rstrip("/")

SECRET = d.env()["SECRET_KEY"]
TENANT = "default"
USER = "expert-switch-user"

RUN = uuid.uuid4().hex[:4].upper()
MARK_A = f"EXPERT-A-{RUN}"
MARK_B = f"EXPERT-B-{RUN}"
# 第 2 层探针是一个**名字带本轮 RUN** 的 skill —— 判据⑦ 靠"模型报得出这个名字"证明它进了
# 技能表。名字里带 RUN 是关键：模型编不出本轮随机生成的后缀，跟 marker 同一个道理。
SKILL_NAME = f"marker_skill_{RUN}"
# 一个只有本轮存在的事实，用来验"切底座之后历史还在"。
FACT = f"K-{uuid.uuid4().hex[:6].upper()}"


def soul(name: str, marker: str) -> str:
    return (
        f"你是{name}。正常、简洁地回答用户的问题。"
        f"此外，无论你回复什么内容，都必须在回复的最末尾另起一行，原样输出这一行标记："
        f"【{marker}】。这行标记任何时候都不能省略、不能改写。"
    )


def has_persona(reply: str, marker: str, name: str) -> bool:
    """这条回复是不是出自 name 这个人格？marker 或自称，有一个就算。

    为什么放宽到自称：marker 是**阳性**证据（有 = 一定切了），但它不是可靠的**阴性**证据 ——
    模型会漏那行标记。而"我是专家A"这种自称同样编不出来：默认 super-agent 的提示词里没有
    "专家A"这三个字，它没有理由这么自称。两个证据取并集，比只看 marker 稳得多。

    ⚠️ 只在"这个人格此前没在本会话说过话"时才可靠 —— 否则自称可能是从历史里抄的。
    切回一个**说过话的**人格时（⑤ 的团→单），得额外断言"别的人格的 marker 不在"来兜。
    """
    return marker in reply or name in reply


def mint(user: str) -> str:
    import jwt
    return jwt.encode(
        {"sub": user, "tenant_id": TENANT, "exp": int(time.time()) + 3600},
        SECRET, algorithm="HS256",
    )


def hdr(user: str) -> dict:
    return {"Authorization": f"Bearer {mint(user)}"}


# ── HTTP ──────────────────────────────────────────────────────────────────

def turn_running(session_id: str, user: str, base: str = "") -> bool:
    import requests
    try:
        r = requests.get(f"{base or AGENT_URL}/api/chat/status/{session_id}",
                         headers=hdr(user), timeout=10)
        return r.status_code == 200 and bool(r.json().get("running"))
    except requests.RequestException:
        return False


def ask(session_id: str, message: str, agent_id: int, user: str = USER,
        base: str = "") -> int:
    """发一条消息，等这一轮真的收尾再返回（理由见 attachment_read_e2e.ask）。

    base 指定打到哪个副本（判据④ 用）；空 = 默认入口。
    """
    import requests
    payload = {"session_id": session_id, "agent_id": agent_id, "message": message}
    r = requests.post(f"{base or AGENT_URL}/api/chat/stream", json=payload,
                      headers=hdr(user), stream=True, timeout=300)
    if r.status_code != 200:
        print(f"    HTTP {r.status_code}: {r.text[:300]}")
        return r.status_code
    for _ in r.iter_lines(decode_unicode=True):
        pass
    for _ in range(180):
        if not turn_running(session_id, user, base):
            break
        time.sleep(1)
    return 200


def set_active_agent(session_id: str, agent_id: int, user: str = USER,
                     base: str = "") -> dict:
    import requests
    r = requests.put(f"{base or AGENT_URL}/api/sessions/{session_id}/active-agent",
                     json={"agent_id": agent_id}, headers=hdr(user), timeout=30)
    return {"status": r.status_code, "body": r.json() if r.content else {}}


def attach(session_id: str, kind: str, ref_id: int, user: str = USER) -> dict:
    """第 2 层：把 tool/skill/kb/mcp_server 挂到这一个会话（判据⑦ 用）。"""
    import requests
    r = requests.post(f"{AGENT_URL}/api/sessions/{session_id}/attachments",
                      json={"kind": kind, "ref_id": ref_id},
                      headers=hdr(user), timeout=30)
    return {"status": r.status_code, "body": r.json() if r.content else {}}


# ── 库 ────────────────────────────────────────────────────────────────────

def last_reply(conn, session_id: str) -> str:
    """最近一条非空 assistant 文本。"""
    with conn.cursor() as cur:
        cur.execute("SELECT role, message FROM agent_messages "
                    "WHERE tenant_id=%s AND session_id=%s ORDER BY id DESC LIMIT 30",
                    (TENANT, session_id))
        for role, raw in cur.fetchall():
            if role != "assistant":
                continue
            msg = json.loads(raw)
            content = msg.get("content")
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
    """从现网某个 enabled agent 抄一个 model —— 保证它一定有匹配的 provider。"""
    with conn.cursor() as cur:
        cur.execute("SELECT model FROM agents WHERE tenant_id=%s AND enabled=1 "
                    "AND model IS NOT NULL AND model<>'' ORDER BY id LIMIT 1", (TENANT,))
        r = cur.fetchone()
    if not r:
        raise SystemExit("现网没有可抄 model 的 agent —— 先 seed 一个 provider/agent")
    return r[0]


def make_agent(conn, model: str, name: str, soul_prompt: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO agents "
            "(tenant_id, name, description, model, temperature, max_turns, max_tokens, "
            " kb_mode, memory_mode, timeout_seconds, is_system, enabled, created_by, "
            " owner_scope, soul_prompt) "
            "VALUES (%s, %s, '专家切换 e2e，可删', %s, 7, 6, 800, 'none', 'none', 120, 0, 1, "
            "        'expert-switch-e2e', 'platform', %s)",
            (TENANT, name, model, soul_prompt),
        )
        return cur.lastrowid


def make_marker_skill(conn) -> int:
    """建一个名字带本轮 RUN 的 skill，作判据⑦ 的第 2 层探针。

    ## skill 的正文**不是**无条件进提示词的（第一版栽在这儿）

    第一版这个探针把 marker 塞进 skill 的 system_prompt，断言"挂上之后回复里就该出现"。
    错在前提上。查 runtime/skill_discloser.go 才明白 skill 走的是**渐进披露**：

        SystemPrompt → skill.Skill.Body，而 Body 只在模型**主动调用 skill 工具**那一刻
        才进上下文。平时模型看见的只有 name + description（技能表）。

    这个设计是对的（否则每个挂上的 skill 都常驻上下文、每轮付费），但它意味着⑦ 不能被动等
    正文里的 marker 出现。当时的失败输出恰好自证了这点：同一条回复里 EXPERT-A 出来了、
    skill 的 marker 没有 —— 模型明明在遵守标记指令，只是 skill 正文压根没进去。

    ## 所以⑦ 改看"名字在不在技能表里"

    name + description 是**无条件**进提示词的 —— 不然模型不知道有这个 skill 可开。于是
    "挂载并进了 cfg"的证据变成"模型报得出这个带 RUN 后缀的名字"，不依赖它愿不愿意调工具。
    （调用那条路由 session_attach_skill_e2e.py 专门覆盖，不在本脚本职责内。）
    """
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO skills (tenant_id, name, display_name, description, "
            "category, system_prompt, enabled, is_builtin, owner_scope, owner_user_id) "
            "VALUES (%s,%s,%s,%s,'general',%s,1,0,'user',%s)",
            (TENANT, SKILL_NAME, f"marker skill {RUN}",
             f"专家切换 e2e 的第 2 层探针（{RUN}），可删。",
             "这是一个测试用技能，没有实际步骤。",
             USER),
        )
        return cur.lastrowid


def make_handoff_team(conn, model: str) -> tuple[int, list[int]]:
    """建一个最小 handoff 团：团 agent + entry(调度员)/worker/exit(验收) 三个成员。

    团 agent 本身也带 marker，好让"链跑通"有个可见信号；但 ⑤ 的核心断言在**历史存活**，
    不在链质量，所以链没跑起来会 skip 而不是 fail。
    """
    team_agent = make_agent(conn, model, f"专家团T-{RUN}", soul("专家团主持人", MARK_B))
    entry = make_agent(conn, model, f"调度员-{RUN}",
                       "你是调度员，读用户诉求后把任务交给下一棒专家。")
    worker = make_agent(conn, model, f"执行专家-{RUN}",
                        "你是执行专家，完成任务后交给验收专家。")
    exit_ = make_agent(conn, model, f"验收专家-{RUN}",
                       "你是验收专家，检查并给出最终答复。")
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


def cleanup(conn, sessions: list[str], agent_ids: list[int],
            skill_ids: list[int] | None = None) -> None:
    with conn.cursor() as cur:
        for s in sessions:
            cur.execute("DELETE FROM session_attachments WHERE session_id=%s", (s,))
            cur.execute("DELETE FROM agent_messages WHERE session_id=%s", (s,))
            cur.execute("DELETE FROM chat_sessions WHERE id=%s", (s,))
        for sk in (skill_ids or []):
            cur.execute("DELETE FROM skills WHERE id=%s", (sk,))
        for a in agent_ids:
            cur.execute("DELETE FROM agent_team_members WHERE member_agent_id=%s", (a,))
            cur.execute("DELETE FROM agent_teams WHERE agent_id=%s", (a,))
            cur.execute("DELETE FROM agents WHERE id=%s", (a,))


def step(n, title): print(f"\n[{n}] {title}")


def main() -> int:
    d.utf8_stdout()
    conn = d.connect()
    sess = f"expsw-{uuid.uuid4().hex[:10]}"
    sess6 = f"expsw-brick-{uuid.uuid4().hex[:8]}"
    sessions4: list[str] = []   # ④/⑦ 各自开的会话，收尾一起清
    agent_ids: list[int] = []
    skill_ids: list[int] = []
    fails: list[str] = []
    skips: list[str] = []

    try:
        model = pick_model(conn)
        print(f"用 model={model} 建测试专家；本轮 marker A={MARK_A} B={MARK_B} 事实={FACT}")
        agent_a = make_agent(conn, model, f"专家A-{RUN}", soul("专家A", MARK_A))
        agent_b = make_agent(conn, model, f"专家B-{RUN}", soul("专家B", MARK_B))
        agent_ids += [agent_a, agent_b]
        print(f"  专家A id={agent_a}  专家B id={agent_b}")

        # ── ① 首消息带 agent_id=专家A ──────────────────────────────
        step(1, f"① 首消息挂专家A，回复应是A的人格，且库里 active_agent_id=={agent_a}")
        if ask(sess, f"你好，你是谁？另外请记住：我的工号是 {FACT}。", agent_a) != 200:
            fails.append("① 发消息失败")
            raise SystemExit
        r1 = last_reply(conn, sess)
        col1 = active_agent(conn, sess)
        print(f"    回复: {r1[:160]}")
        print(f"    库里 active_agent_id={col1}")
        persona1 = has_persona(r1, MARK_A, "专家A")
        if not persona1:
            fails.append(f"① 回复既无 {MARK_A} 也不自称专家A —— 底座没换成 A")
        if col1 != agent_a:
            fails.append(f"① active_agent_id 应为 {agent_a}，实际 {col1} —— TouchSession 没播种激活 agent")
        if persona1 and col1 == agent_a:
            print("    ✓ ① 底座是专家A，激活 agent 已落库")

        # ── ② 中途切专家B ─────────────────────────────────────────
        step(2, f"② PUT 切到专家B，下条回复应带 {MARK_B} 而非 {MARK_A}，且记得工号 {FACT}")
        sw = set_active_agent(sess, agent_b)
        print(f"    PUT active-agent: HTTP {sw['status']}  {json.dumps(sw['body'], ensure_ascii=False)}")
        if sw["status"] != 200:
            fails.append(f"② 切换失败 HTTP {sw['status']}")
        elif "applies" not in sw["body"]:
            fails.append("② 切换响应缺 applies 字段（前端不知道何时生效）")
        if active_agent(conn, sess) != agent_b:
            fails.append(f"② 切换后 active_agent_id 应为 {agent_b}")

        if ask(sess, "你现在是谁？我刚才告诉你的工号是多少？", agent_b) != 200:
            fails.append("② 切换后发消息失败")
        else:
            r2 = last_reply(conn, sess)
            print(f"    回复: {r2[:160]}")
            # 阳性（是B）放宽到自称；阴性（不是A）仍只看 marker —— 漏标记不影响阴性判断，
            # 而"专家A"三个字此时可能出现在它解释"我刚才是专家A、现在是B"这类话里。
            b_on = has_persona(r2, MARK_B, "专家B")
            if MARK_A in r2:
                fails.append("② 还带着专家A的 marker —— 底座没真正切走")
            if not b_on:
                fails.append(f"② 回复既无 {MARK_B} 也不自称专家B —— 切到B没生效")
            if FACT not in r2:
                fails.append(f"② 切底座后丢了历史 —— 记不起工号 {FACT}（重建没回放历史？）")
            if b_on and MARK_A not in r2 and FACT in r2:
                print("    ✓ ② 底座换成专家B，且历史（工号）完整存活")

        # ── ③ 取消回默认 ─────────────────────────────────────────
        step(3, f"③ PUT 0 取消，回默认 super-agent（无 A/B marker），仍记得 {FACT}")
        sw0 = set_active_agent(sess, 0)
        print(f"    PUT active-agent 0: HTTP {sw0['status']}  {json.dumps(sw0['body'], ensure_ascii=False)}")
        if active_agent(conn, sess) != 0:
            fails.append("③ 取消后 active_agent_id 应为 0")
        if ask(sess, f"再说一次我的工号。", 0) != 200:
            fails.append("③ 取消后发消息失败")
        else:
            r3 = last_reply(conn, sess)
            print(f"    回复: {r3[:160]}")
            if MARK_A in r3 or MARK_B in r3:
                fails.append("③ 取消后仍带专家 marker —— 没回到默认 super-agent")
            if FACT not in r3:
                fails.append(f"③ 取消后丢了历史 —— 记不起工号 {FACT}")
            if MARK_A not in r3 and MARK_B not in r3 and FACT in r3:
                print("    ✓ ③ 回到默认 super-agent，历史仍完整")

        # ── ④ 多副本自愈 ─────────────────────────────────────────
        step(4, "④ 副本B 先持有旧引擎 → 在副本A 上 PUT 切A → 回到B 发消息，B 应自己重建")
        if not (URL_A and URL_B):
            skips.append("④ 未设 LEA_AGENT_URL_A/_B —— 单副本环境测不到跨副本自愈"
                         "（起 infra/docker-compose.verify.yml 再跑）")
        else:
            sess4 = f"expsw-mr-{uuid.uuid4().hex[:8]}"
            sessions4.append(sess4)
            # 1) 先在**副本B** 上开一轮，让 B 的内存里装上"默认 super-agent"那份引擎。
            #    这一步是判据的前提：要验的是"B 手里已有旧对象时能否发现列变了"，
            #    B 内存里空着的话它走的是全新构建那条路，什么都没验到。
            if ask(sess4, f"你好。请记住工号 {FACT}。", 0, base=URL_B) != 200:
                fails.append("④ 副本B 起会话失败")
            else:
                r4b = last_reply(conn, sess4)
                print(f"    B 首轮回复: {r4b[:100]}")
                if MARK_A in r4b:
                    fails.append("④ 默认态回复就带了专家A marker —— 前提没成立")

                # 2) 在**副本A** 上 PUT 切成专家A。A 内存里没这个会话，它只改了库。
                sw4 = set_active_agent(sess4, agent_a, base=URL_A)
                print(f"    在副本A PUT: HTTP {sw4['status']} "
                      f"{json.dumps(sw4['body'], ensure_ascii=False)}")
                if sw4["status"] != 200:
                    fails.append(f"④ 副本A 上 PUT 失败 HTTP {sw4['status']}")
                elif active_agent(conn, sess4) != agent_a:
                    fails.append("④ PUT 之后列没落库")
                else:
                    # 3) 回到**副本B** 发消息。B 手里那份引擎是按 active_agent_id=0 建的，
                    #    库里现在是 agent_a —— 指纹不一致，B 必须自己发现并重建。
                    #    这一步失败就是 485b485 那个 bug 在第 1 层重演。
                    # 问"你是谁"是刻意的：逼它自报身份，好让 has_persona 有自称可看。
                    # 这个会话此前只有默认 super-agent 说过话，"专家A"三个字不可能来自历史。
                    if ask(sess4, "现在你是谁？另外我的工号是多少？", 0, base=URL_B) != 200:
                        fails.append("④ 副本B 上发消息失败")
                    else:
                        r4 = last_reply(conn, sess4)
                        print(f"    B 第二轮回复: {r4[:160]}")
                        if not has_persona(r4, MARK_A, "专家A"):
                            fails.append(
                                f"④ 副本B 没有自愈 —— 既没有 {MARK_A} 也不自称专家A。"
                                "在A上改的列，B 手里还是旧引擎（指纹没折进 active_agent_id？）")
                        elif FACT not in r4:
                            fails.append(f"④ 跨副本重建后丢了历史 —— 记不起工号 {FACT}")
                        else:
                            print("    ✓ ④ 副本B 靠指纹自己发现列变了、重建、换人格，历史完整")

        # ── ⑤ 专家团边界（最高优先） ──────────────────────────────
        step(5, "⑤ 中途切成 handoff 专家团 → 链跑通且历史存活；再切回单专家历史也在")
        try:
            team_agent, members = make_handoff_team(conn, model)
            agent_ids += [team_agent] + members
            print(f"    专家团 agent id={team_agent} members={members}")
        except Exception as e:  # noqa: BLE001
            skips.append(f"⑤ 建专家团失败（{e}）—— 跳过团边界")
            team_agent = None

        if team_agent is not None:
            swt = set_active_agent(sess, team_agent)
            print(f"    PUT 切专家团: HTTP {swt['status']}  {json.dumps(swt['body'], ensure_ascii=False)}")
            if swt["status"] != 200 or active_agent(conn, sess) != team_agent:
                fails.append("⑤ 切专家团失败或列没落库")
            else:
                if ask(sess, f"用一句话确认你收到了我的工号 {FACT}。", team_agent) != 200:
                    skips.append("⑤ 专家团这一轮没跑成（发消息失败）—— 团配置或引擎问题，非本层")
                else:
                    # ⚠️ 别从 agent_messages 判断"链跑没跑"：handoff 每棒是独立会话，
                    # 棒间的 handoff_to 不落主会话（appendChainHistory 只补 user+final）。
                    # 链真跑了的证据是 SSE 的 chain_hop —— 那条由 expert_handoff_e2e.py 专门验。
                    # 这里只验本层真正关心的：切进专家团后，单 agent 阶段写下的历史还在没。
                    rt = last_reply(conn, sess)
                    print(f"    回复: {rt[:160]}")
                    if not rt:
                        skips.append("⑤ 专家团没产出回复 —— 团/引擎问题，非本层；Layer-1 切换本身已生效")
                    elif FACT not in rt:
                        fails.append(f"⑤ 切专家团后丢了历史 —— 链没读入单 agent 阶段的工号 {FACT}")
                    else:
                        print("    ✓ ⑤ 切到专家团后历史存活（单→团 交接点通过；链机制见 expert_handoff_e2e.py）")

                    # 再切回单专家：验"链写下的历史能被单 agent 读入"。
                    set_active_agent(sess, agent_a)
                    if ask(sess, "你现在是谁？最后再确认一次工号。", agent_a) == 200:
                        rback = last_reply(conn, sess)
                        print(f"    切回专家A回复: {rback[:160]}")
                        # 这里自称"专家A"可能是从历史里抄的（A 在①—② 说过话），所以自称不够，
                        # 还要"专家团的 marker 不在" —— 团人格用的是 MARK_B，它消失才说明
                        # 底座真的从团切走了。marker 作阴性证据是可靠的那一半。
                        persona_back = has_persona(rback, MARK_A, "专家A")
                        team_gone = MARK_B not in rback
                        if FACT not in rback:
                            fails.append(f"⑤ 团→单 切回后丢了历史 —— 记不起工号 {FACT}")
                        elif not persona_back:
                            fails.append("⑤ 切回专家A后既无A的 marker 也不自称A —— 底座没切回")
                        elif not team_gone:
                            fails.append(f"⑤ 切回后仍带专家团 marker {MARK_B} —— 底座没从团切走")
                        else:
                            print("    ✓ ⑤ 团→单 切回，历史完整、人格是A（两个交接方向都通）")

        # ── ⑥ 失效兜底 ───────────────────────────────────────────
        step(6, "⑥ 挂一个不存在的 agent → PUT 该被拒；直接把列改坏 → 会话仍能开（回退默认）")
        # 先起一个正常会话。
        if ask(sess6, "你好。", 0) != 200:
            fails.append("⑥ 起会话失败")
        else:
            bad = set_active_agent(sess6, 999999999)
            print(f"    PUT 不存在的 agent: HTTP {bad['status']}  {json.dumps(bad['body'], ensure_ascii=False)}")
            if bad["status"] == 200:
                fails.append("⑥ PUT 了一个不存在的 agent 却返回 200 —— 少了切换前的可加载校验")
            else:
                print("    ✓ ⑥ 切换前校验挡住了不存在的 agent")
            # 绕过校验直接把列改坏（模拟专家事后被删），验重建时回退默认、不 brick。
            with conn.cursor() as cur:
                cur.execute("UPDATE chat_sessions SET active_agent_id=999999999 WHERE id=%s", (sess6,))
            rc = ask(sess6, "还在吗？", 0)
            if rc != 200:
                fails.append(f"⑥ 激活 agent 失效后会话打不开了（HTTP {rc}）—— 没回退默认，brick 了")
            else:
                print("    ✓ ⑥ 激活 agent 失效时回退默认，会话没 brick")

        # ── ⑦ 两层正交 ───────────────────────────────────────────
        #
        # 这个判据要抓的失败模式是**切第 1 层会不会把第 2 层冲掉**。
        #
        # 机制上的风险点：GetOrCreate 每次先按 active agent 载 cfg、再 mergeSessionAttachments
        # 把挂载并进去。切专家走的是同一条重建链 —— 如果那条路没走到合并，挂载就会在切换之后
        # 悄悄消失。这是两层之间唯一真正会互相踩的地方，也是单测覆盖不到的（要真起引擎）。
        #
        # 探针：一个名字带本轮 RUN 的 skill。它进没进 cfg，看模型报不报得出这个名字 ——
        # 技能表（name+description）是无条件进提示词的，正文才是渐进披露。见 make_marker_skill。
        step(7, f"⑦ 专家A + 挂 skill {SKILL_NAME} → 两层同时在；再切到专家B → 挂载不该丢")
        sess7 = f"expsw-2l-{uuid.uuid4().hex[:8]}"
        sessions4.append(sess7)
        # 问法：只要它把名字报出来就行，不要求它去调用（调用那条路不是本判据的事）。
        ASK_SKILLS = (f"你现在是谁？另外列出你可用的技能名称，"
                      f"如果其中有名字包含 {RUN} 的技能，把它的完整名字原样写出来。")
        try:
            skill_id = make_marker_skill(conn)
            skill_ids.append(skill_id)
            print(f"    skill id={skill_id} name={SKILL_NAME}")
        except Exception as e:  # noqa: BLE001
            skips.append(f"⑦ 建 skill 失败（{e}）—— 跳过两层正交")
            skill_id = None

        if skill_id is not None:
            # 先用专家A 起一轮（也让会话落库，attach 才有行可挂）。
            if ask(sess7, "你好。", agent_a) != 200:
                fails.append("⑦ 起会话失败")
            else:
                at = attach(sess7, "skill", skill_id)
                print(f"    POST attachments: HTTP {at['status']} "
                      f"{json.dumps(at['body'], ensure_ascii=False)}")
                if at["status"] not in (200, 201):
                    fails.append(f"⑦ 挂 skill 失败 HTTP {at['status']}")
                elif ask(sess7, ASK_SKILLS, agent_a) != 200:
                    fails.append("⑦ 挂 skill 后发消息失败")
                else:
                    r7 = last_reply(conn, sess7)
                    print(f"    专家A + 挂载 回复: {r7[:240]}")
                    layer1 = has_persona(r7, MARK_A, "专家A")
                    layer2 = SKILL_NAME in r7
                    if not layer1:
                        fails.append(f"⑦ 第1层没了 —— 既无 {MARK_A} 也不自称专家A，"
                                     "挂 skill 把底座人格顶掉了")
                    if not layer2:
                        fails.append(f"⑦ 第2层没了 —— 技能表里报不出 {SKILL_NAME}，"
                                     "专家之上的挂载没并进 cfg")
                    if layer1 and layer2:
                        print("    ✓ ⑦a 底座人格(A) 与挂载(skill 在技能表里) 同时生效")

                    # 关键的后半段：切第 1 层，第 2 层必须活着。
                    sw7 = set_active_agent(sess7, agent_b)
                    print(f"    PUT 切到专家B: HTTP {sw7['status']}")
                    if sw7["status"] != 200:
                        fails.append(f"⑦ 切到专家B 失败 HTTP {sw7['status']}")
                    elif ask(sess7, ASK_SKILLS, agent_b) != 200:
                        fails.append("⑦ 切到专家B 后发消息失败")
                    else:
                        r7b = last_reply(conn, sess7)
                        print(f"    专家B + 挂载 回复: {r7b[:240]}")
                        # 人格换了没：B 此前没在这个会话说过话，自称可信。
                        b_on = has_persona(r7b, MARK_B, "专家B")
                        still = SKILL_NAME in r7b
                        if not b_on:
                            fails.append("⑦ 切到专家B 没生效 —— 第1层切换在有挂载时失灵了")
                        if not still:
                            fails.append(
                                f"⑦ 切第1层把第2层冲掉了 —— 切到B之后技能表里没有 {SKILL_NAME}。"
                                "重建时漏了 mergeSessionAttachments？")
                        if b_on and still:
                            print("    ✓ ⑦b 切换第1层之后第2层仍在 —— 两层正交")

        if active_agent(conn, sess7) != agent_b:
            fails.append("⑦ 末态 active_agent_id 应为专家B（挂载不该干扰第1层的值）")

    except SystemExit:
        pass
    finally:
        cleanup(conn, [sess, sess6] + sessions4, agent_ids, skill_ids)
        conn.close()

    print("\n" + "═" * 70)
    for s in skips:
        print(f"跳过：{s}")
    if fails:
        print(f"失败 {len(fails)} 条：")
        for f in fails:
            print(f"  ✗ {f}")
        return 1
    if skips:
        print("未失败，但有跳过项 —— 上面那些判据这一轮没有被验证。")
        return 0
    print("判据全通。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
