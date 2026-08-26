"""执行 migrations/014 —— skills 三层可见性收口 + 上架审核表。

先把 skills 整表 dump 成 INSERT 备份（DROP COLUMN 不可回滚），再逐步执行并打印
每一步的结果。自检那步 orphan 不为 0 就 rollback。

幂等：已经执行过（enabled 列存在且 scope/status 已删）就跳过。
"""
import os
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pymysql

conn = pymysql.connect(
    host=os.getenv("DB_HOST", "202.205.160.9"),
    port=int(os.getenv("DB_PORT", "3307")),
    user=os.getenv("DB_USER", "root"),
    password=os.getenv("DB_PASSWORD", ""),
    database=os.getenv("DB_NAME", "lea_platform"),
    charset="utf8mb4",
    autocommit=False,
)


def columns(cur, table="skills"):
    cur.execute(f"SHOW COLUMNS FROM {table}")
    return [r[0] for r in cur.fetchall()]


def sql_literal(v):
    if v is None:
        return "NULL"
    s = str(v).replace("\\", "\\\\").replace("'", "''")
    return f"'{s}'"


with conn.cursor() as cur:
    before = columns(cur)
    print("迁移前的列:", before)

    if "enabled" in before and "scope" not in before and "status" not in before:
        print("\n014 已经执行过，无事可做。")
        sys.exit(0)

    # ── 备份 ──────────────────────────────────────────────────────────────
    cur.execute(f"SELECT {', '.join(before)} FROM skills")
    rows = cur.fetchall()
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = f"scripts/backup_skills_{ts}.sql"
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"-- skills 备份于 {ts}，迁移 014 之前\n")
        f.write(f"-- 列: {before}\n")
        for r in rows:
            vals = ", ".join(sql_literal(v) for v in r)
            f.write(f"INSERT INTO skills ({', '.join(before)}) VALUES ({vals});\n")
    print(f"已备份 {len(rows)} 行 → {path}")

    # ── 1. enabled ────────────────────────────────────────────────────────
    if "enabled" not in before:
        print("\n=== 1. 加 enabled 列 ===")
        cur.execute(
            "ALTER TABLE skills ADD COLUMN enabled TINYINT(1) NOT NULL DEFAULT 1 "
            "COMMENT '启用：0 = 停用，已绑的 agent 也不再拿到它'"
        )
        n = cur.execute(
            "UPDATE skills SET enabled = CASE WHEN status = 'draft' THEN 0 ELSE 1 END"
        )
        print(f"  加列 OK，回填 {n} 行")
        cur.execute("SELECT enabled, COUNT(*) FROM skills GROUP BY enabled")
        print(f"  分布: {dict(cur.fetchall())}")
    else:
        print("\n1. enabled 已存在，跳过")

    # ── 2. 自检 ───────────────────────────────────────────────────────────
    print("\n=== 2. 归属自检 ===")
    cur.execute("""
        SELECT COUNT(*),
               SUM(owner_scope = 'user'     AND owner_user_id IS NOT NULL),
               SUM(owner_scope = 'platform' AND tenant_id <> 'default'),
               SUM(owner_scope = 'platform' AND tenant_id  = 'default'),
               SUM(owner_scope IS NULL OR owner_scope = ''
                   OR (owner_scope = 'user' AND owner_user_id IS NULL))
        FROM skills
    """)
    total, private, tenant_v, global_v, orphan = cur.fetchone()
    print(f"  总数={total}  用户私有={private}  租户可见={tenant_v}  "
          f"所有人可见={global_v}  orphan={orphan}")
    if orphan:
        conn.rollback()
        sys.exit(
            f"中止：有 {orphan} 行归属列表达不了（owner_scope 为空，或 user 但没有 "
            f"owner_user_id）。删掉 scope 之后这些行无法归类，必须先修。"
        )

    # ── 3. 删旧列 ─────────────────────────────────────────────────────────
    drops = [c for c in ("scope", "status") if c in columns(cur)]
    if drops:
        print(f"\n=== 3. 删掉 {drops} ===")
        cur.execute(
            "ALTER TABLE skills " + ", ".join(f"DROP COLUMN {c}" for c in drops)
        )
        print("  OK")
    else:
        print("\n3. scope / status 已删，跳过")

    # ── 4. 上架申请表 ─────────────────────────────────────────────────────
    print("\n=== 4. skill_publish_requests ===")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS skill_publish_requests (
            id           BIGINT AUTO_INCREMENT PRIMARY KEY,
            skill_id     INT          NOT NULL,
            tenant_id    VARCHAR(64)  NOT NULL,
            requested_by VARCHAR(100) NOT NULL,
            target_scope VARCHAR(20)  NOT NULL,
            status       VARCHAR(20)  NOT NULL DEFAULT 'pending',
            review_note  TEXT         NULL,
            reviewed_by  VARCHAR(100) NULL,
            reviewed_at  DATETIME     NULL,
            created_at   DATETIME     DEFAULT CURRENT_TIMESTAMP,
            updated_at   DATETIME     DEFAULT CURRENT_TIMESTAMP
                                      ON UPDATE CURRENT_TIMESTAMP,
            CONSTRAINT fk_spr_skill FOREIGN KEY (skill_id)
                REFERENCES skills(id) ON DELETE CASCADE,
            INDEX idx_spr_pending (tenant_id, status),
            INDEX idx_spr_skill (skill_id, status)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """)
    print("  OK")

    conn.commit()

    print("\n迁移后的列:", columns(cur))
    cur.execute("SELECT COUNT(*) FROM skill_publish_requests")
    print(f"skill_publish_requests 行数: {cur.fetchone()[0]}")

conn.close()
print("\n完成。")
