"""把 lea-tools 那行 web_search 内置工具停用。

## 为什么

它是 seed.py 里的 5 个内置工具之一，走 serpapi（execution_type=api，
http_adapter_config 里 `api_key` 从 tool_credentials 渲染）。实测现状：

    web_search 行数: 1     id=4 tenant='default' enabled=1 builtin=1
    凭证行数: 0            → {{api_key}} 渲染成空串，serpapi 返 401
    绑定的 agent 数: 0     → 今天没人用它

而 lea-agent 现在注册了引擎自带的 WebSearch（DuckDuckGo，不要 key）。两者做同一件
事、名字只差一个下划线和大小写。今天不冲突只是因为没人绑 web_search —— 一旦有人在
后台看到"联网搜索"这个内置工具就给 agent 绑上，模型会同时看到两个搜索工具，随机挑
一个；挑中 web_search 就撞 401，而那个 401 来自 serpapi，排查时看着像远端挂了。

停用而不是删除：留着这行就留着那份 serpapi 的适配配置（endpoint / request_mapping /
response_mapping 都是对的）。以后真要买商业搜索 API，把凭证补上、enabled 改回 1 就能用，
不用重新拼一遍映射。

只改 enabled 一列，不动别的。幂等：已经是 0 就跳过。
"""
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

with conn.cursor() as cur:
    cur.execute(
        "SELECT id, tenant_id, enabled, is_builtin FROM tools WHERE name = 'web_search'"
    )
    rows = cur.fetchall()
    if not rows:
        print("没有 web_search 这行，无事可做。")
        sys.exit(0)

    print("改之前:")
    for r in rows:
        print(f"  id={r[0]} tenant={r[1]!r} enabled={r[2]} builtin={r[3]}")

    # 安全网：有人绑了就不动，先让人知道
    ids = [r[0] for r in rows]
    ph = ",".join(["%s"] * len(ids))
    cur.execute(
        f"SELECT COUNT(*) FROM agent_tool_bindings WHERE tool_id IN ({ph})", ids
    )
    bound = cur.fetchone()[0]
    if bound:
        conn.rollback()
        sys.exit(
            f"中止：有 {bound} 个 agent 绑了 web_search。停用会让它们的工具清单少一项，"
            f"这个影响得先确认。"
        )

    n = cur.execute(
        "UPDATE tools SET enabled = 0 WHERE name = 'web_search' AND enabled = 1"
    )
    conn.commit()
    print(f"\n停用了 {n} 行。")

    cur.execute(
        "SELECT id, tenant_id, enabled, is_builtin FROM tools WHERE name = 'web_search'"
    )
    print("改之后:")
    for r in cur.fetchall():
        print(f"  id={r[0]} tenant={r[1]!r} enabled={r[2]} builtin={r[3]}")

conn.close()
