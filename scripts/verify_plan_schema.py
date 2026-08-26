"""迁移后的收尾核对：库里的形状跟代码读的那套对得上吗。只读。

两件事，任何一条不对，计划功能在生产上就是坏的：
  1. session_plans 是 009+010 之后的形状（四列，没有 content/status 残留）
  2. web_search 已停用（它跟引擎自带的 WebSearch 重复，而且没凭证会 401）

注：agents.plan_mode 曾经存在过（migrations/011），后来判断那是个错误的抽象 ——
计划模式是所有 agent 都支持的能力，不是管理员配出来的；要不要进规划态由每次请求
带来（SendRequest.plan_mode）。列已删除，这里也不再检查。
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

ok = True

with conn.cursor() as cur:
    # ── 1. session_plans 的形状 ───────────────────────────────────────────
    cur.execute("SHOW COLUMNS FROM session_plans")
    cols = {r[0] for r in cur.fetchall()}

    want = {"approved_content", "draft_content", "approved_steps_json", "draft_steps_json"}
    missing = want - cols
    leftover = {"content", "status"} & cols

    print("session_plans:")
    print(f"  期望的四列都在: {'✓' if not missing else '✗ 缺 ' + str(missing)}")
    print(f"  老列已删除:     {'✓' if not leftover else '✗ 残留 ' + str(leftover)}")
    ok &= not missing and not leftover

    # 唯一约束还在吗 —— SaveDraftPlan 的 upsert 补偿路径靠它收敛
    cur.execute("SHOW INDEX FROM session_plans WHERE Non_unique = 0")
    idx = {r[2] for r in cur.fetchall()}
    has_uq = any(name != "PRIMARY" for name in idx)
    print(f"  唯一约束还在:   {'✓' if has_uq else '✗ 没了 —— upsert 会打转'}")
    ok &= has_uq

    cur.execute("SELECT COUNT(*) FROM session_plans")
    print(f"  行数: {cur.fetchone()[0]}")

    # ── 2. web_search 已停用 ─────────────────────────────────────────────
    cur.execute("SELECT id, enabled FROM tools WHERE name = 'web_search'")
    rows = cur.fetchall()
    print("\nweb_search（serpapi 那行）:")
    for r in rows:
        mark = "✓ 已停用" if r[1] == 0 else "✗ 还开着"
        print(f"  id={r[0]} enabled={r[1]}  {mark}")
        ok &= r[1] == 0

print("\n" + ("=" * 40))
print("全部通过" if ok else "有不对的地方，见上面的 ✗")
conn.close()
sys.exit(0 if ok else 1)
