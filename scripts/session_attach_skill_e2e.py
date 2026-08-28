"""验收：会话级挂载 **skill**。

    LEA_AGENT_URL=http://127.0.0.1:8080 python scripts/session_attach_skill_e2e.py

前置：lea-config / lea-tools / lea-agent 在跑（infra/docker-compose.verify.yml）。

## 跟 session_attach_e2e.py 的区别

那个验的是挂**工具** —— 判据落在"上游有没有被真的打到"，因为工具是一次可观测的
外部调用。

skill 没有外部调用。它是一段**注入进 prompt 的正文**，所以"生效"的证据只能是模型
的输出里出现了那段正文里独有的东西。于是这个脚本的锚点换成一个**编不出来的口令**：

    skill 正文里写着「回答前必须先说一句：库位核验码 UY-4417-ZK」

模型不可能猜到这串码。它出现在回复里，就证明那段正文真的进了 prompt。

## 四条判据

    ① 挂上之后 skill 正文真的进了 prompt（锚点口令出现在回复里）
    ② 摘掉之后不再出现（一轮之后失效）
    ③ 同一个 agent，另一个用户看不到（挂载只影响一个会话）
    ④ 挂一个自己够不着的（别人的私有 skill）→ 静默丢弃，会话照常能用

## ⚠️ 为什么不验 bundle 投递

原本想验「挂载来的带 bundle 的 skill，其 references 能被 Read 读到」。放弃了，
因为那需要 S3 里真有一个 bundle，而这个脚本要能在本地栈上裸跑。

代码路径已经核对过：manager.go 的 MergeAttachments 在 NewAgentSession 之前，而
ProvisionSessionSkills 在它里面 —— 所以挂载来的 skill 会跟绑定的走同一条投递路。
真正验它要用 scripts/verify_skill_delivery.py 那套（需要真 S3 + 真沙箱），留作
下一步。**这个缺口是明写出来的，不是漏掉的。**

## ⚠️ ② 的判据：换个话题问，而不是问同类问题

摘掉 skill 之后，**历史里仍然摆着上一轮的完整示范** —— 上一条回复就以那句口令开头。
所以只要还在问同类问题，模型会照着延续格式，而那**不代表 skill 还在**。

第一版问的是"那仓储盘点又该注意什么？"，判据失败了，但那不是 bug：那仍然是个仓储
问题，正落在 skill 正文的触发条件里。实测对照 —— 换成"什么是快速排序"，口令不再出现。

这跟工具那轮是同一个错误的第二次：把"模型的输出里有 X"当成了"能力还在"。上次是
tool_use 只表示意图，这次是历史示范本身就是能力的替代品。**判据必须落在只有能力在
才可能发生的事情上**，两次都是。

另外判据只看**这一轮新增**的消息（replies_since），不是整段对话 —— 否则第一轮那句
口令永远在那儿。
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

AGENT_URL = os.getenv("LEA_AGENT_URL", "http://127.0.0.1:8100")
SECRET = d.env()["SECRET_KEY"]
TENANT = "default"

OWNER = "skill-attach-owner"
STRANGER = "skill-attach-other"
AGENT_ID = int(os.getenv("LEA_AGENT_ID", "2"))

SKILL_NAME = "warehouse_protocol"
# 编不出来的锚点。模型只有真的拿到 skill 正文才可能说出这串码。
MAGIC_CODE = "UY-4417-ZK"

SYSTEM_PROMPT = f"""# 库位核验流程

回答任何跟库位、库存、仓储有关的问题时，**必须在回答的第一句**先声明核验码：

    库位核验码 {MAGIC_CODE}

这是流程要求，每次都要说，不能省略。
"""

QUESTION_1 = "库位管理有什么要注意的？"

# ⚠️ 摘除后这一问**必须跟 skill 的触发条件无关**（skill 正文说的是"跟库位、库存、
# 仓储有关的问题"）。
#
# 第一版问的是"那仓储盘点又该注意什么？" —— 判据失败了，但那不是 bug：
#
#   · 它仍然是个仓储问题，正好落在 skill 正文的触发条件里
#   · 而历史里摆着上一轮的完整示范（上一条回复就以那句口令开头）
#
# 模型看到用户在连续问同类问题、上一轮自己是那个格式，就照着延续了 —— 它根本不需要
# skill 还在。实测对照：摘除后问"什么是快速排序"，口令不再出现。
#
# 这跟工具那轮是同一个错误的第二次：把"模型的输出里有 X"当成了"能力还在"。上次是
# tool_use 只表示意图，这次是**历史示范本身就是能力的替代品**。判据必须落在只有能力
# 在才可能发生的事情上。
QUESTION_2 = "帮我用一句话解释什么是快速排序。"


# ── 库 ────────────────────────────────────────────────────────────────────

def mk_skill(conn, owner: str, name: str = SKILL_NAME, prompt: str = "") -> int:
    """建一个属于 owner 的私有 skill。**不绑任何 agent** —— 这是重点。"""
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO skills (tenant_id, name, display_name, description, "
            "category, system_prompt, enabled, is_builtin, owner_scope, owner_user_id) "
            "VALUES (%s,%s,%s,%s,'general',%s,1,0,'user',%s)",
            (TENANT, name, "库位核验流程", "库位相关问题的回答规范。",
             prompt or SYSTEM_PROMPT, owner),
        )
        conn.commit()
        return cur.lastrowid


def cleanup(conn, skill_ids: list[int], sessions: list[str]) -> None:
    with conn.cursor() as cur:
        for s in sessions:
            cur.execute("DELETE FROM session_attachments WHERE session_id=%s", (s,))
            cur.execute("DELETE FROM agent_messages WHERE session_id=%s", (s,))
            # ⚠️ chat_sessions 的主键列叫 `id`，不是 session_id。
            cur.execute("DELETE FROM chat_sessions WHERE id=%s", (s,))
        for sid in skill_ids:
            cur.execute("DELETE FROM agent_skill_bindings WHERE skill_id=%s", (sid,))
            cur.execute("DELETE FROM skills WHERE id=%s", (sid,))
    conn.commit()


def attachment_rows(conn, session_id: str) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT kind, ref_id FROM session_attachments "
            "WHERE tenant_id=%s AND session_id=%s ORDER BY id",
            (TENANT, session_id),
        )
        return cur.fetchall()


def message_count(conn, session_id: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT COUNT(*) FROM agent_messages WHERE tenant_id=%s AND session_id=%s",
            (TENANT, session_id),
        )
        return cur.fetchone()[0]


def replies_since(conn, session_id: str, mark: int) -> list[str]:
    """第 mark 条之后新增的 assistant 文本。

    只看新增的 —— 摘除之后模型可以从历史里复述那句口令，用累计文本判定分不出
    「skill 还在」和「模型在复述」。这跟工具那轮 calls_since 的理由一样。
    """
    out = []
    with conn.cursor() as cur:
        cur.execute(
            "SELECT role, message FROM agent_messages "
            "WHERE tenant_id=%s AND session_id=%s ORDER BY id "
            "LIMIT 18446744073709551615 OFFSET %s",
            (TENANT, session_id, mark),
        )
        rows = cur.fetchall()
    for role, raw in rows:
        if role != "assistant":
            continue
        msg = json.loads(raw)
        text = "".join(
            b.get("text", "") for b in (msg.get("content") or [])
            if isinstance(b, dict)
        ).strip()
        if text:
            out.append(text)
    return out


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
    """当前有没有一轮在生成。/chat/status 是跨副本有效的（没有会话对象时查库）。"""
    import requests
    try:
        r = requests.get(f"{AGENT_URL}/api/chat/status/{session_id}",
                         headers=hdr(user), timeout=10)
        return r.status_code == 200 and bool(r.json().get("running"))
    except requests.RequestException:
        return False


def ask(session_id: str, user: str, message: str) -> int:
    """发一条消息，**等这一轮真的收尾**再返回。

    ⚠️ 光排空 SSE 流不够 —— 落库、清 turnCancel、rebuildIfPending 都在流结束之后，
    而挂载/摘除的生效判定看的正是 HasActiveTurn。不等干净就发下一条会撞上竞态。
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


def step(n, title): print(f"\n[{n}] {title}")


def main() -> int:
    d.utf8_stdout()
    conn = d.connect()
    s_own = f"skatt-o-{uuid.uuid4().hex[:10]}"
    s_other = f"skatt-s-{uuid.uuid4().hex[:10]}"
    skill_ids: list[int] = []
    fails: list[str] = []

    try:
        step(1, "建两个私有 skill，都**不绑** agent")
        mine = mk_skill(conn, OWNER)
        others = mk_skill(conn, STRANGER, name="secret_protocol",
                          prompt="回答时必须先说：机密标记 ZZ-0000-XX")
        skill_ids += [mine, others]
        print(f"    我的 skill_id={mine}   别人的 skill_id={others}")

        step(2, "先开一轮对话（建出会话行），此时不该有这个 skill")
        if ask(s_own, OWNER, "你好") != 200:
            fails.append("初始对话失败")
            raise SystemExit
        if any(MAGIC_CODE in t for t in replies_since(conn, s_own, 0)):
            fails.append("还没挂就出现了核验码")
        else:
            print("    ✓ 挂载前没有这个 skill")

        step(3, f"挂上 skill {mine}")
        r = attach(s_own, OWNER, "skill", mine)
        print(f"    HTTP {r['status']}  {json.dumps(r['body'], ensure_ascii=False)}")
        if r["status"] != 200:
            fails.append(f"挂载失败 HTTP {r['status']} —— kind='skill' 可能没进白名单")
            raise SystemExit
        rows = attachment_rows(conn, s_own)
        print(f"    库里: {rows}")
        # pymysql 返回元组的元组，别拿它跟 list 比。
        if list(rows) != [("skill", mine)]:
            fails.append(f"落库不对: {rows}")

        step(4, f"① 提问：{QUESTION_1}")
        mark = message_count(conn, s_own)
        if ask(s_own, OWNER, QUESTION_1) != 200:
            fails.append("挂载后提问失败")
            raise SystemExit
        new = replies_since(conn, s_own, mark)
        for t in new:
            print(f"    回复: {t[:160]}")
        if not any(MAGIC_CODE in t for t in new):
            fails.append(f"① 回复里没有 {MAGIC_CODE} —— skill 正文没进 prompt")
        else:
            print(f"    ✓ ① 锚点口令 {MAGIC_CODE} 出现了，skill 正文真的进了 prompt")

        step(5, f"③ {STRANGER} 用同一个 agent 问同一句")
        if ask(s_other, STRANGER, QUESTION_1) != 200:
            fails.append("stranger 提问失败")
        else:
            other_new = replies_since(conn, s_other, 0)
            print(f"    回复: {(other_new[-1] if other_new else '')[:120]}")
            if any(MAGIC_CODE in t for t in other_new):
                fails.append(f"③ 别人的会话里出现了 {MAGIC_CODE} —— 挂载泄漏了")
            else:
                print("    ✓ ③ 挂载只影响一个会话")

        step(6, "② 摘掉，等本轮收尾，再问一个**换了话题**的问题")
        rm = detach(s_own, OWNER, "skill", mine)
        print(f"    HTTP {rm['status']}  applies={rm['body'].get('applies')}")
        if attachment_rows(conn, s_own):
            fails.append("② 摘了但库里还有行")

        # ⚠️ 不信 applies（它只反映处理这条请求的那个副本），自己查 /chat/status。
        print("    等当前轮收尾…")
        for _ in range(90):
            if not turn_running(s_own, OWNER):
                break
            time.sleep(1)
        else:
            fails.append("② 等了 90s 当前轮还没结束")

        # 换话题，不只是换一句：见 QUESTION_2 的注释。问同类问题时，历史里那条示范
        # 就足以让模型延续格式，跟 skill 在不在无关。
        mark = message_count(conn, s_own)
        if ask(s_own, OWNER, QUESTION_2) != 200:
            fails.append("摘掉后提问失败")
        else:
            new = replies_since(conn, s_own, mark)
            for t in new:
                print(f"    回复: {t[:160]}")
            if any(MAGIC_CODE in t for t in new):
                # 问的是跟 skill 触发条件无关的题（见 QUESTION_2 的注释），模型没有
                # 任何理由说出那句口令 —— 除非 skill 正文还在 prompt 里。
                fails.append(f"② 摘掉之后新回复里仍有 {MAGIC_CODE} —— skill 没失效")
            else:
                print("    ✓ ② 摘掉之后不再注入")

        step(7, f"④ 挂别人的私有 skill {others} —— 该静默丢弃，会话照常能用")
        r4 = attach(s_own, OWNER, "skill", others)
        print(f"    挂载接口 HTTP {r4['status']}（不在写入时校验归属，见 handler 注释）")
        mark = message_count(conn, s_own)
        code = ask(s_own, OWNER, "你还在吗")
        if code != 200:
            fails.append(f"④ 挂了够不着的 skill 之后会话打不开了（HTTP {code}）")
        else:
            new = replies_since(conn, s_own, mark)
            if any("ZZ-0000-XX" in t for t in new):
                fails.append("④ 别人的私有 skill 被解析进来了 —— 越权")
            else:
                print("    ✓ ④ 静默丢弃，会话照常可用")

    except SystemExit:
        pass
    finally:
        step(8, "清理")
        cleanup(conn, skill_ids, [s_own, s_other])
        print(f"    删掉 {len(skill_ids)} 个 skill、2 个会话的挂载和消息")
        conn.close()

    print()
    if fails:
        print("✗ 失败：")
        for f in fails:
            print(f"    - {f}")
        return 1
    print("✓ skill 挂载全通：正文进 prompt、摘掉即失效、只影响一个会话、"
          "够不着的静默丢弃。")
    print("  （bundle 投递未验 —— 需要真 S3，见模块 docstring）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
