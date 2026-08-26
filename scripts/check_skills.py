"""现网 skill 数据长什么样。只读。

想知道的：
  1. 有多少 skill，归属怎么分布（scope 旧列 vs owner_scope 新列）
  2. 有没有 bundle_uri（有 = 打过包传过 S3；没有 = 纯 prompt 型）
  3. status 分布（draft / active / published —— 三个词哪些在用）
  4. 谁绑了哪些
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
    cur.execute("SHOW COLUMNS FROM skills")
    print("skills 列:", [r[0] for r in cur.fetchall()])

    cur.execute(
        "SELECT id, tenant_id, name, status, scope, owner_scope, owner_user_id, "
        "is_builtin, CASE WHEN bundle_uri IS NULL THEN '(无)' ELSE bundle_uri END "
        "FROM skills ORDER BY id"
    )
    rows = cur.fetchall()
    print(f"\n共 {len(rows)} 个 skill:")
    for r in rows:
        print(f"  id={r[0]} tenant={r[1]!r} name={r[2]!r}")
        print(f"      status={r[3]!r} scope={r[4]!r} owner_scope={r[5]!r} "
              f"owner_user={r[6]!r} builtin={r[7]}")
        print(f"      bundle={r[8]}")

    print("\n按 status:", end=" ")
    cur.execute("SELECT status, COUNT(*) FROM skills GROUP BY status")
    print(dict(cur.fetchall()))

    print("按 scope:  ", end=" ")
    cur.execute("SELECT scope, COUNT(*) FROM skills GROUP BY scope")
    print(dict(cur.fetchall()))

    print("按 owner_scope:", end=" ")
    cur.execute("SELECT owner_scope, COUNT(*) FROM skills GROUP BY owner_scope")
    print(dict(cur.fetchall()))

    cur.execute(
        "SELECT s.name, COUNT(b.id) FROM skills s "
        "LEFT JOIN agent_skill_bindings b ON b.skill_id = s.id "
        "GROUP BY s.id, s.name ORDER BY s.id"
    )
    print("\n绑定数:")
    for name, n in cur.fetchall():
        print(f"  {name}: {n} 个 agent")

conn.close()
