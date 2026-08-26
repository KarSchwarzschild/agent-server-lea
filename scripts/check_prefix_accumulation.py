"""前缀注入会不会在 agent_messages 里累积？

agent_session.go 把记忆/计划拼到 message 前面再交给引擎，而引擎的 store 落的是它
收到的那条 llm.Message。如果落库的是拼过的版本，那么每一轮的 user 消息都带一份
完整前缀，ReloadHistory 读回 20 轮就是 20 份。

只读，不改任何数据。
"""
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pymysql

conn = pymysql.connect(
    host=os.getenv("DB_HOST", "202.205.160.9"),
    port=int(os.getenv("DB_PORT", "3307")),
    user=os.getenv("DB_USER", "root"),
    password=os.getenv("DB_PASSWORD", ""),
    database=os.getenv("DB_NAME", "lea_platform"),
    charset="utf8mb4",
)

MARKERS = ["[系统提示 - 用户相关信息]", "[已批准的执行计划]"]

with conn.cursor() as cur:
    cur.execute("SELECT COUNT(*) FROM agent_messages WHERE role='user'")
    print("user 消息总数:", cur.fetchone()[0])

    for marker in MARKERS:
        cur.execute(
            "SELECT COUNT(*) FROM agent_messages WHERE role='user' AND message LIKE %s",
            (f"%{marker}%",),
        )
        n = cur.fetchone()[0]
        print(f"带 {marker!r} 的 user 消息: {n}")

    # 逐会话看：同一个 session 里有多少条 user 消息带前缀
    cur.execute(
        """SELECT session_id, COUNT(*) c
           FROM agent_messages
           WHERE role='user' AND (message LIKE %s OR message LIKE %s)
           GROUP BY session_id ORDER BY c DESC LIMIT 5""",
        tuple(f"%{m}%" for m in MARKERS),
    )
    rows = cur.fetchall()
    print("\n单会话里带前缀的 user 消息条数 top5:")
    for sid, c in rows:
        print(f"  session={sid}  {c} 条")

    if rows:
        sid = rows[0][0]
        cur.execute(
            """SELECT message FROM agent_messages
               WHERE role='user' AND session_id=%s ORDER BY id LIMIT 3""",
            (sid,),
        )
        print(f"\nsession={sid} 前 3 条 user 消息的长度与前缀:")
        for (raw,) in cur.fetchall():
            try:
                content = json.loads(raw).get("content")
                text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
            except Exception:
                text = raw
            head = text[:60].replace("\n", "\\n")
            print(f"  len={len(text):6d}  head={head!r}")

conn.close()
