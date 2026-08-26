"""库里到底有哪些角色、谁有 platform_admin。只读。

问题：代码里 ROLE_PLATFORM_ADMIN 定义了、can()/scoped_filter 也认它，但
lea_authkit 的头解析只放行 "admin"。所以要分清两件事：

  1. 这个角色在**数据层**存在吗（roles 表里有没有这一行、有没有人挂着它）
  2. 它能不能**传到**服务里（这是已知的：不能，头解析白名单只有 admin）
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
    cur.execute("SHOW TABLES LIKE 'roles'")
    if not cur.fetchone():
        print("没有 roles 表。")
        sys.exit(0)

    cur.execute("SHOW COLUMNS FROM roles")
    print("roles 列:", [r[0] for r in cur.fetchall()])

    cur.execute("SELECT * FROM roles")
    rows = cur.fetchall()
    print(f"\nroles 共 {len(rows)} 行:")
    for r in rows:
        print("  ", r)

    cur.execute("""
        SELECT r.name, COUNT(ur.user_id)
        FROM roles r LEFT JOIN user_roles ur ON ur.role_id = r.id
        GROUP BY r.id, r.name
    """)
    print("\n每个角色挂了几个人:")
    for name, n in cur.fetchall():
        print(f"  {name}: {n}")

    cur.execute("SELECT COUNT(*) FROM users")
    print(f"\nusers 共 {cur.fetchone()[0]} 人")

conn.close()
