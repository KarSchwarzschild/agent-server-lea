"""web_search 这条路今天到底通不通？只读。

三件事：
  1. 现网有没有 web_search 这行内置工具
  2. 它有没有凭证（serpapi 要 api_key，没有的话 {{api_key}} 会渲染成空）
  3. 有多少 agent 绑了它
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
        "SELECT id, tenant_id, name, execution_type, enabled, is_builtin "
        "FROM tools WHERE name = 'web_search'"
    )
    rows = cur.fetchall()
    print(f"web_search 行数: {len(rows)}")
    for r in rows:
        print(f"  id={r[0]} tenant={r[1]!r} exec={r[3]} enabled={r[4]} builtin={r[5]}")

    if rows:
        ids = [r[0] for r in rows]
        ph = ",".join(["%s"] * len(ids))

        cur.execute(
            f"SELECT tool_id, tenant_id, provider, is_platform_default, "
            f"CHAR_LENGTH(api_key_enc) FROM tool_credentials WHERE tool_id IN ({ph})",
            ids,
        )
        creds = cur.fetchall()
        print(f"\n凭证行数: {len(creds)}")
        for c in creds:
            print(f"  tool_id={c[0]} tenant={c[1]!r} provider={c[2]!r} "
                  f"platform_default={c[3]} key_len={c[4]}")
        if not creds:
            print("  ⚠️ 没有凭证 —— {{api_key}} 会渲染成空串，serpapi 会返 401")

        cur.execute(
            f"SELECT COUNT(*) FROM agent_tool_bindings WHERE tool_id IN ({ph})", ids
        )
        print(f"\n绑定了它的 agent 数: {cur.fetchone()[0]}")

conn.close()
