"""压缩**之前**换进程：验 raw 路径（recentWithinBudget）。

    python scripts/ctx_e2e_rawpath.py <agent_id>

## 为什么这一格必须单独验

ctx_e2e_podb.py 验的是压缩**之后**换进程 —— 那条走 loadHistoryWithSummary，而那段
代码这次没改。真正被改写的是 raw 路径：原来按「最近 20 轮」截，现在按 token 预算收。

一个刚聊了一轮的会话不会有摘要行，所以新进程必然走 loadHistoryRaw →
recentWithinBudget。这是这次改动里唯一真正的新代码，也是唯一还没在活进程里跑过的。

## 判据不是「请求成功」

走空历史也会成功 —— 模型会一本正经地编。所以第一轮塞一个模型不可能猜到的暗号，
换进程之后问它。答得出来 = 历史真的读回来了。

答案从 **agent_messages 读**，不解析 SSE：库里那条是落盘的真相，而 SSE 的事件形状
是另一回事（踩过一次，有些 data: 是裸数字）。
"""

from __future__ import annotations

import pathlib
import sys
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import _dbconn as d
from ctx_e2e_drive import TENANT, mint_token, send

SECRET_WORD = "紫水晶47号档案"


def last_assistant_text(conn, session_id: str) -> str:
    """最后一条 assistant 消息的文本。SSE 解析不可靠，库里这条才是落盘的真相。"""
    import json

    with conn.cursor() as cur:
        cur.execute(
            "SELECT message FROM agent_messages "
            "WHERE tenant_id=%s AND session_id=%s AND role='assistant' "
            "ORDER BY id DESC LIMIT 1",
            (TENANT, session_id),
        )
        row = cur.fetchone()
    if not row:
        return ""
    msg = json.loads(row[0])
    return "".join(
        b.get("text", "") for b in (msg.get("content") or []) if isinstance(b, dict)
    )


def counts(conn, session_id: str) -> tuple[int, int]:
    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM agent_messages WHERE tenant_id=%s AND session_id=%s",
                    (TENANT, session_id))
        msgs = cur.fetchone()[0]
        cur.execute("SELECT COUNT(*) FROM session_summaries WHERE tenant_id=%s AND session_id=%s",
                    (TENANT, session_id))
        summaries = cur.fetchone()[0]
    return msgs, summaries


def main() -> int:
    d.utf8_stdout()
    agent_id = int(sys.argv[1]) if len(sys.argv) > 1 else 50
    mode = sys.argv[2] if len(sys.argv) > 2 else "turn1"
    session_id = sys.argv[3] if len(sys.argv) > 3 else f"ctxraw-{uuid.uuid4().hex[:12]}"

    conn = d.connect()
    try:
        if mode == "turn1":
            code, _ = send(session_id, agent_id,
                           f"请记住这个暗号：{SECRET_WORD}。只回复「记住了」。",
                           mint_token())
            msgs, summaries = counts(conn, session_id)
            print(f"SESSION_ID={session_id}")
            print(f"第 1 轮  HTTP {code}  消息行={msgs}  摘要行={summaries}")
            print(f"  回复: {last_assistant_text(conn, session_id).strip()[:60]!r}")
            if summaries != 0:
                print("  ✗ 才一轮就有摘要行？触发线配错了")
                return 1
            print("  · 没有摘要行 —— 换进程后必然走 loadHistoryRaw（这正是要验的那段）")
            return 0

        # mode == "ask"：由**新进程**接手
        before_msgs, before_sum = counts(conn, session_id)
        print(f"接管前: 消息行={before_msgs}  摘要行={before_sum}")
        if before_sum != 0:
            print("  ✗ 已经有摘要了，这一轮验不到 raw 路径")
            return 1

        code, _ = send(session_id, agent_id, "我刚才让你记的暗号是什么？原样复述。",
                       mint_token())
        answer = last_assistant_text(conn, session_id)
        after_msgs, _ = counts(conn, session_id)
        print(f"接管后: HTTP {code}  消息行={after_msgs}")
        print(f"  回答: {answer.strip()[:80]!r}")

        if code != 200:
            print("  ✗ 请求失败")
            return 1
        if SECRET_WORD in answer:
            print(f"\n✓ 新进程复述出了暗号 {SECRET_WORD!r} —— raw 路径把历史完整读回来了。")
            return 0
        print("\n✗ 没复述出暗号。历史没读回来，或者只读回了一部分。")
        print("  （模型编不出这个词，所以答不出来就是历史没到它面前。）")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
