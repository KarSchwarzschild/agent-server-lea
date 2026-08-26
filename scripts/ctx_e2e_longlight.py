"""直接演示这次修的那个 bug：又长又轻的会话会不会失忆。

    python scripts/ctx_e2e_longlight.py <agent_id> [turns]

## 修的是什么

改之前 loadHistoryRaw 按「最近 20 轮」硬截。一个 50 轮的闲聊会话总共可能才 1.5 万
token —— 远在压缩触发线以下，所以永远没有摘要行，永远走 raw 路径，于是**每一轮**都
只看得见最近 20 轮。第 21 轮起，最早那些轮次就再也读不回来了。

而且这跟跨不跨 pod 无关：LoadHistory 的结果会被 ReplaceHistory 整体替换成工作历史，
所以同一个进程里每轮也砍一次。

## 这个脚本怎么证明

第 1 轮塞一个模型猜不到的暗号，然后灌 N 轮极短的对话把它推到 20 轮开外，最后问暗号。

    改之前   第 1 轮落在窗口外 → 答不出来
    改之后   总量远低于预算 → 全量载入 → 答得出来

全程用极短消息，所以总 token 数始终在压缩触发线以下 —— 确保走的是 raw 路径，
而不是被摘要救了。跑完会打印实际 token 占用，好确认这一点。
"""

from __future__ import annotations

import pathlib
import sys
import time
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import _dbconn as d
from ctx_e2e_drive import TENANT, mint_token, send
from ctx_e2e_rawpath import SECRET_WORD, counts, last_assistant_text

OLD_TURN_CAP = 20  # 换掉的那个 maxLoadTurns


def approx_tokens(conn, session_id: str) -> int:
    """按引擎的估算法粗算这个会话的历史占多少 token（CJK 1/字，其余 4 字符 1）。"""
    import json
    import unicodedata

    with conn.cursor() as cur:
        cur.execute("SELECT message FROM agent_messages "
                    "WHERE tenant_id=%s AND session_id=%s", (TENANT, session_id))
        rows = cur.fetchall()

    total = 0
    for (raw,) in rows:
        msg = json.loads(raw)
        for b in msg.get("content") or []:
            if not isinstance(b, dict):
                continue
            text = b.get("text") or ""
            cjk = sum(1 for ch in text if unicodedata.category(ch).startswith("Lo"))
            total += cjk + (len(text) - cjk + 3) // 4
    return total


def main() -> int:
    d.utf8_stdout()
    agent_id = int(sys.argv[1]) if len(sys.argv) > 1 else 51
    filler = int(sys.argv[2]) if len(sys.argv) > 2 else OLD_TURN_CAP + 2

    session_id = f"ctxlong-{uuid.uuid4().hex[:12]}"
    token = mint_token()
    conn = d.connect()

    print(f"会话 {session_id}  agent={agent_id}")
    print(f"第 1 轮存暗号，再灌 {filler} 轮把它推到 {OLD_TURN_CAP} 轮开外\n")

    try:
        code, _ = send(session_id, agent_id,
                       f"请记住这个暗号：{SECRET_WORD}。只回复「记住了」。", token)
        if code != 200:
            print(f"第 1 轮失败 HTTP {code}")
            return 1
        print(f"第  1 轮  存暗号  回复={last_assistant_text(conn, session_id).strip()[:20]!r}")

        t0 = time.time()
        for i in range(2, 2 + filler):
            code, _ = send(session_id, agent_id, f"第{i}句。只回复「好」。", token)
            if code != 200:
                print(f"第 {i} 轮失败 HTTP {code}")
                return 1
            if i % 5 == 0 or i == 1 + filler:
                print(f"第 {i:2} 轮  ...  {time.time()-t0:.0f}s")

        msgs, summaries = counts(conn, session_id)
        tokens = approx_tokens(conn, session_id)
        user_turns = 1 + filler
        print(f"\n灌完：{user_turns} 轮用户消息，{msgs} 条消息行，约 {tokens:,} token")
        print(f"  摘要行={summaries}  "
              f"{'（触发线以下，走 raw 路径 ✓）' if summaries == 0 else '（被摘要救了，这次验不到 raw ✗）'}")

        if summaries != 0:
            print("\n✗ 触发了压缩，这一轮验不到 raw 路径的行为。把 filler 调小重跑。")
            return 1
        if user_turns <= OLD_TURN_CAP:
            print(f"\n✗ 只有 {user_turns} 轮，没超过旧的 {OLD_TURN_CAP} 轮上限，证明不了什么。")
            return 1

        code, _ = send(session_id, agent_id, "最开始我让你记的暗号是什么？原样复述。", token)
        answer = last_assistant_text(conn, session_id)
        print(f"\n问暗号  HTTP {code}  回答={answer.strip()[:60]!r}")

        if SECRET_WORD in answer:
            print(f"\n✓ {user_turns} 轮之后仍复述出暗号。")
            print(f"  改之前第 1 轮落在「最近 {OLD_TURN_CAP} 轮」之外，这里必然答不出来。")
            return 0
        print(f"\n✗ 答不出暗号 —— 第 1 轮没被读回来。")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
