"""跑一个手写的编号 SQL 迁移。

    uv run python scripts/apply_migration.py migrations/004_unify_ownership.sql
    uv run python scripts/apply_migration.py migrations/004_unify_ownership.sql --dry-run

这个仓库的迁移是手写编号 SQL（migrations/001..），没有 alembic，也没有 mysql 客户端
可用（Windows 开发机）。所以需要这么个东西，否则迁移只能靠手动粘贴。

**它不记录已执行过哪些迁移** —— 没有 schema_migrations 表。所以：
  - 重复执行会撞 "Duplicate column name" / "Duplicate key name"，这两类错误被当成
    "这条已经跑过了"跳过并计数，其余错误一律中止。
  - 也就是说它是**尽力幂等**，不是真幂等。上生产前应该换成 alembic 或者加一张
    版本表；现在这个规模不值得。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from lea_config.database import engine
from sqlalchemy import text

# 这两类错误意味着"这条 DDL 之前跑过了"，跳过是对的。
ALREADY_APPLIED = re.compile(
    r"Duplicate column name|Duplicate key name|already exists", re.I
)


def split_statements(sql: str) -> list[str]:
    """按分号切，并剥掉整行的 -- 注释。

    只处理这个仓库实际用到的形态（DDL + 简单 UPDATE）。带分号的字符串字面量、
    存储过程、DELIMITER 一概不支持 —— 真需要时换 alembic，不要往这里加。
    """
    lines = [
        ln for ln in sql.splitlines() if not ln.lstrip().startswith("--")
    ]
    return [s.strip() for s in "\n".join(lines).split(";") if s.strip()]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path", type=Path)
    ap.add_argument("--dry-run", action="store_true", help="只打印会执行什么")
    args = ap.parse_args()

    stmts = split_statements(args.path.read_text(encoding="utf-8"))
    print(f"{args.path}: {len(stmts)} 条语句  目标={engine.url.host}/{engine.url.database}\n")

    if args.dry_run:
        for i, s in enumerate(stmts, 1):
            print(f"  [{i}] {' '.join(s.split())[:110]}")
        return 0

    ran = skipped = 0
    with engine.begin() as conn:
        for i, stmt in enumerate(stmts, 1):
            brief = " ".join(stmt.split())[:80]
            try:
                conn.execute(text(stmt))
                ran += 1
                print(f"  OK    [{i}] {brief}")
            except Exception as exc:
                if ALREADY_APPLIED.search(str(exc)):
                    skipped += 1
                    print(f"  跳过  [{i}] {brief}  （已存在）")
                    continue
                print(f"  失败  [{i}] {brief}\n        {str(exc).splitlines()[0]}")
                return 1

    print(f"\n执行 {ran} 条，跳过 {skipped} 条（已存在）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
