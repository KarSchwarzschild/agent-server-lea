"""SG-2 迁移脚本 —— 把 skill 表结构切到 OBS bundle 模式。

破坏性操作：
- DROP TABLE skill_resources（内容改存 OBS）
- DROP TABLE skill_versions（结构重构，snapshot → bundle_*）
- DROP TABLE skills（system_prompt 列改存 OBS bundle）
  然后 Base.metadata.create_all() 用新 model 重建三张表

项目未上线、skills 数据全靠启动时 seed，可放心跑。跑完重启 uvicorn，
启动阶段会自动重新 seed 内置 skill（SG-4 完成后 bundle_uri 也会一起塞好）。

跑法：
  cd D:/gitlab/lea-platform
  uv run --project packages/lea-server python packages/lea-server/scripts/sg2_migrate.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# 让脚本能独立跑（不依赖 pip install -e）
_ROOT = Path(__file__).resolve().parents[3]
for pkg in ("lea-server", "lea-graph", "lea-apps", "lea-rag"):
    p = _ROOT / "packages" / pkg / "src"
    if p.exists():
        sys.path.insert(0, str(p))

# Windows GBK 兜底
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
except Exception:
    pass


def main() -> int:
    from sqlalchemy import inspect, text

    # 触发所有 model 注册到 Base.metadata（不启动 server）
    from lea_server import main as _server_main  # noqa: F401
    from lea_server.database import Base, engine

    inspector = inspect(engine)
    existing = set(inspector.get_table_names())

    # DROP 顺序反向（skill_resources / skill_versions 都 FK → skills）
    tables_to_drop = ["skill_resources", "skill_versions", "skill_test_runs", "skills"]

    with engine.begin() as conn:
        # SQLite 需要显式关闭外键约束才能 DROP
        dialect = engine.dialect.name
        if dialect == "sqlite":
            conn.execute(text("PRAGMA foreign_keys = OFF"))
        elif dialect == "mysql":
            conn.execute(text("SET FOREIGN_KEY_CHECKS = 0"))

        for tbl in tables_to_drop:
            if tbl in existing:
                conn.execute(text(f"DROP TABLE {tbl}"))
                print(f"  [DROP] {tbl}")
            else:
                print(f"  [SKIP] {tbl} (not exists)")

        if dialect == "sqlite":
            conn.execute(text("PRAGMA foreign_keys = ON"))
        elif dialect == "mysql":
            conn.execute(text("SET FOREIGN_KEY_CHECKS = 1"))

    print(f"\n[CREATE] Base.metadata.create_all() ({dialect})")
    Base.metadata.create_all(bind=engine)

    # 验证新列存在
    inspector = inspect(engine)
    skill_cols = {c["name"] for c in inspector.get_columns("skills")}
    required = {"bundle_uri", "bundle_hash", "bundle_size", "bundle_manifest"}
    missing = required - skill_cols
    dropped = {"system_prompt"} & skill_cols
    if missing:
        print(f"[FAIL] skills 表缺列: {missing}")
        return 2
    if dropped:
        print(f"[FAIL] skills 表还残留旧列: {dropped}")
        return 3
    if "skill_resources" in inspector.get_table_names():
        print("[FAIL] skill_resources 还在")
        return 4

    sv_cols = {c["name"] for c in inspector.get_columns("skill_versions")}
    sv_required = {"bundle_uri", "bundle_hash", "bundle_size", "is_current"}
    sv_missing = sv_required - sv_cols
    if sv_missing:
        print(f"[FAIL] skill_versions 表缺列: {sv_missing}")
        return 5
    if "snapshot" in sv_cols:
        print("[FAIL] skill_versions 表还残留旧列 snapshot")
        return 6

    print("\n[OK] skills / skill_versions / skill_test_runs 已重建")
    print("[OK] skill_resources 已 DROP")
    print("[OK] skills 新列：bundle_uri / bundle_hash / bundle_size / bundle_manifest")
    print("[OK] skill_versions 新列：bundle_uri / bundle_hash / bundle_size / bundle_manifest / is_current")
    print("\n== SG-2 迁移完成 ==")
    print("下一步：重启 uvicorn。启动会 seed 内置 skill 元数据；SG-4 完成后 bundle_uri 才会一起塞好。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
