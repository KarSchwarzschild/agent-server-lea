"""看一眼 session_plans 现有的行 —— 迁移前确认要搬多少数据。只读。"""
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
    cur.execute("SHOW COLUMNS FROM session_plans")
    print("列:", [r[0] for r in cur.fetchall()])

    cur.execute("SELECT id, tenant_id, session_id, status, CHAR_LENGTH(content) FROM session_plans")
    rows = cur.fetchall()
    print(f"\n共 {len(rows)} 行:")
    for r in rows:
        print(f"  id={r[0]} tenant={r[1]!r} session={r[2]} status={r[3]!r} content_len={r[4]}")

    cur.execute("SELECT status, COUNT(*) FROM session_plans GROUP BY status")
    print("\n按 status:", dict(cur.fetchall()))

conn.close()
