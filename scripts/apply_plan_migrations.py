"""按序执行 migrations/009 + 010，先备份 session_plans 现有数据。

009 会 DROP 掉 content / status 两列，不可回滚 —— 所以先把整表 dump 成 INSERT 语句
存到 scripts/backup_session_plans_<ts>.sql，出事能手工还原。

每条语句单独执行并打印结果，中间那条自检 SELECT 的输出要人看一眼：
orphan 必须是 0（每行恰好有一列正文非空）。
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


def columns(cur):
    cur.execute("SHOW COLUMNS FROM session_plans")
    return [r[0] for r in cur.fetchall()]


with conn.cursor() as cur:
    before = columns(cur)
    print("迁移前的列:", before)

    if "approved_content" in before and "approved_steps_json" in before:
        print("\n009 和 010 都已经执行过，无事可做。")
        sys.exit(0)

    # ── 备份 ──────────────────────────────────────────────────────────────
    cur.execute(f"SELECT {', '.join(before)} FROM session_plans")
    rows = cur.fetchall()
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = f"scripts/backup_session_plans_{ts}.sql"
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"-- session_plans 备份于 {ts}，迁移 009/010 之前\n")
        f.write(f"-- 列: {before}\n")
        for r in rows:
            vals = ", ".join(
                "NULL" if v is None else "'" + str(v).replace("\\", "\\\\").replace("'", "''") + "'"
                for v in r
            )
            f.write(f"INSERT INTO session_plans ({', '.join(before)}) VALUES ({vals});\n")
    print(f"已备份 {len(rows)} 行 → {path}")

    # ── 009 ───────────────────────────────────────────────────────────────
    if "approved_content" not in before:
        print("\n=== 009: 拆分 draft / approved ===")
        cur.execute("""ALTER TABLE session_plans
            ADD COLUMN approved_content TEXT NULL COMMENT '已批准、正在执行的计划',
            ADD COLUMN draft_content    TEXT NULL COMMENT '待用户批准的计划（首次或修订）'""")
        print("  加列 OK")

        n = cur.execute("UPDATE session_plans SET approved_content = content WHERE status = 'active'")
        print(f"  active → approved_content: {n} 行")
        n = cur.execute("UPDATE session_plans SET draft_content = content WHERE status = 'draft'")
        print(f"  draft  → draft_content:    {n} 行")

        cur.execute("""SELECT COUNT(*), SUM(approved_content IS NOT NULL),
                              SUM(draft_content IS NOT NULL),
                              SUM(approved_content IS NULL AND draft_content IS NULL)
                       FROM session_plans""")
        total, appr, draft, orphan = cur.fetchone()
        print(f"  自检: total={total} approved={appr} draft={draft} orphan={orphan}")
        if orphan:
            conn.rollback()
            sys.exit(f"中止：有 {orphan} 行两列正文都是空的，搬迁规则没覆盖到它们")

        cur.execute("ALTER TABLE session_plans DROP COLUMN content, DROP COLUMN status")
        print("  删掉 content / status OK")
    else:
        print("\n009 已执行过，跳过")

    # ── 010 ───────────────────────────────────────────────────────────────
    print("\n=== 010: 结构化步骤 ===")
    cur.execute("""ALTER TABLE session_plans
        ADD COLUMN approved_steps_json TEXT NULL COMMENT '已批准计划的步骤与进度 JSON',
        ADD COLUMN draft_steps_json    TEXT NULL COMMENT '待批准计划的步骤 JSON'""")
    print("  加列 OK")

    conn.commit()
    print("\n迁移后的列:", columns(cur))

    cur.execute("""SELECT id, tenant_id, session_id,
                          CHAR_LENGTH(approved_content), CHAR_LENGTH(draft_content)
                   FROM session_plans""")
    print("\n现有行:")
    for r in cur.fetchall():
        print(f"  id={r[0]} tenant={r[1]!r} session={r[2]} approved_len={r[3]} draft_len={r[4]}")

conn.close()
print("\n完成。")
