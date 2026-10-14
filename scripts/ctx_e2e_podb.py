"""Pod B 接管：换一个**全新进程**给同一个会话再发一轮，看它读回什么。

    python scripts/ctx_e2e_podb.py <agent_id> <session_id>

新进程的 Manager 缓存是空的，所以它必须走 Build → Resume → LoadHistory。库里已经有
摘要行了，于是该走摘要路径，读回 [摘要 + covered_id 之后的原文]。

判据不是「请求成功」—— 走 raw 路径也会成功。判据是**这一轮之后摘要行有没有被换掉**
以及消息有没有接着长：

    covered_id 不倒退   摘要仍然覆盖它该覆盖的那一段
    消息行 +2           新进程确实把这一轮接在了同一段对话后面

要是恢复读的是原始行而不是摘要，这一轮会重新压一次（一次白花的模型调用），
表现为 covered_id 往前跳一大截。
"""

from __future__ import annotations

import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import _dbconn as d
from ctx_e2e_drive import db_state, mint_token, send


def main() -> int:
    d.utf8_stdout()
    agent_id, session_id = int(sys.argv[1]), sys.argv[2]
    conn = d.connect()
    try:
        before_msgs, _, before_covered = db_state(conn, session_id)
        print(f"接管前: 消息行={before_msgs}  covered_id={before_covered}")

        t0 = time.time()
        code, reply = send(session_id, agent_id,
                           "刚才我一共发了几轮？只回数字。", mint_token())
        el = time.time() - t0

        after_msgs, has_summary, after_covered = db_state(conn, session_id)
        print(f"接管后: HTTP {code}  {el:.1f}s  消息行={after_msgs}  "
              f"covered_id={after_covered}")

        ok = True
        if code != 200:
            print(f"  ✗ 请求失败: {reply[:200]}")
            return 1
        if after_msgs != before_msgs + 2:
            print(f"  ✗ 消息行没有 +2（{before_msgs} → {after_msgs}）")
            ok = False
        if not has_summary:
            print("  ✗ 摘要行没了")
            ok = False
        elif after_covered < before_covered:
            print(f"  ✗ covered_id 倒退 {before_covered} → {after_covered}")
            ok = False
        elif after_covered > before_covered:
            print(f"  · covered_id 前移 {before_covered} → {after_covered}"
                  f"（这一轮又压了一次；历史仍在触发线以上时是正常的）")
        else:
            print("  · covered_id 不变 —— 这一轮没重新压缩，摘要被复用了")

        print("\n" + ("✓ 新进程接上了同一段对话。" if ok else "✗ 有问题，见上。"))
        return 0 if ok else 1
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
