"""清掉 session_attach_e2e.py 上一轮崩在清理步骤时留下的测试数据。

    python scripts/_cleanup_attach_e2e.py

只删**测试自己造的东西**：session_id 以 attach- 开头的会话数据，以及
owner_scope='user' 的工具行（这个库里真实业务不该有 user 归属的工具，
0 行是常态 —— 见 project_auth_gaps 里那句"库里 0 行 user 工具"）。

先打印再删，删完复查。
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import _dbconn as d


def main() -> int:
    d.utf8_stdout()
    conn = d.connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, name, owner_user_id FROM tools WHERE owner_scope='user'"
            )
            tools = cur.fetchall()
            print(f"待删工具 {len(tools)} 行:")
            for t in tools:
                print(f"  id={t[0]} name={t[1]!r} owner={t[2]!r}")

            cur.execute(
                "SELECT session_id, COUNT(*) FROM session_attachments "
                "WHERE session_id LIKE 'attach-%' GROUP BY session_id"
            )
            atts = cur.fetchall()
            print(f"待删挂载行: {atts or '（无）'}")

            for s, _ in atts:
                cur.execute("DELETE FROM session_attachments WHERE session_id=%s", (s,))
            cur.execute("DELETE FROM agent_messages WHERE session_id LIKE 'attach-%'")
            print(f"  消息删 {cur.rowcount} 条")
            cur.execute("DELETE FROM chat_sessions WHERE id LIKE 'attach-%'")
            print(f"  会话行删 {cur.rowcount} 条")
            for t in tools:
                cur.execute("DELETE FROM agent_tool_bindings WHERE tool_id=%s", (t[0],))
                cur.execute("DELETE FROM tools WHERE id=%s", (t[0],))
            print(f"  工具删 {len(tools)} 行")
        conn.commit()

        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM tools WHERE owner_scope='user'")
            a = cur.fetchone()[0]
            cur.execute(
                "SELECT COUNT(*) FROM session_attachments WHERE session_id LIKE 'attach-%'"
            )
            b = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM agent_tool_bindings WHERE agent_id=2")
            n = cur.fetchone()[0]
        print(f"\n复查: user 工具={a}  attach 挂载行={b}  agent 2 绑定数={n}（应为 5）")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
