"""013 之后的验收：摘要落库这条路真的通了吗。只读，不写业务数据。

跑迁移之后跑这个。它回答三件事，按"能静默失败的程度"从高到低排：

  1. 表在不在、列对不对        —— 迁移跑没跑
  2. 有没有真的落进行           —— SaveSummary 是否还在静默失败
  3. covered_id 指向的边界合不合理 —— 数学对不对

## 为什么第 2 条必须单独验

引擎那边是 `_ = ss.SaveSummary(...)`（agentloop internal/repl/session.go:809），
错误整个扔掉。加上 lea-agent 的 loadHistoryWithSummary 把「表不存在」和「这个会话
没压缩过」当成同一件事回落，两边合起来的结果是：**表没建的时候，功能表现完全正常，
一条日志都没有。**所以"跑完迁移没报错"不能当作验收通过，必须看到行。

## 为什么第 3 条要验

covered_id 是拿 `ORDER BY id DESC LIMIT 1 OFFSET recentCount` 算的，前提是
「工作历史的条数 == DB 行数」（maxHistoryMessages=0 不裁剪，Reduce 不改条数）。
这个前提哪天被打破，表现是 covered_id 偏移——恢复出来的历史多一段或少一段，
而不是报错。所以这里查它落在不落在合理区间。
"""

from __future__ import annotations

import json
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from lea_config.database import engine
from sqlalchemy import text


def main() -> int:
    print(f"目标={engine.url.host}/{engine.url.database}\n")

    with engine.connect() as conn:
        # ── 1. 表结构 ────────────────────────────────────────────────────
        cols = conn.execute(text(
            "SELECT column_name, column_type, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_schema = :db AND table_name = 'session_summaries' "
            "ORDER BY ordinal_position"
        ), {"db": engine.url.database}).fetchall()

        if not cols:
            print("✗ session_summaries 不存在 —— 迁移 013 没跑。")
            return 1

        print("session_summaries 列:")
        for name, typ, nullable in cols:
            print(f"    {name:12} {typ:16} null={nullable}")

        want = {"session_id", "tenant_id", "summary", "covered_id", "created_at"}
        missing = want - {c[0] for c in cols}
        if missing:
            print(f"\n✗ 缺列: {missing}")
            return 1
        print("\n✓ 结构对上了")

        # ── 2. 有没有行 ──────────────────────────────────────────────────
        rows = conn.execute(text(
            "SELECT tenant_id, session_id, covered_id, created_at, "
            "       CHAR_LENGTH(summary) AS summary_bytes "
            "FROM session_summaries ORDER BY created_at DESC LIMIT 10"
        )).fetchall()

        n = conn.execute(text("SELECT COUNT(*) FROM session_summaries")).scalar()
        print(f"\n共 {n} 条摘要。")

        if n == 0:
            print(
                "  尚无摘要。这**不一定**是失败 —— 还没有会话压到触发线也会是 0。\n"
                "  要确认链路通，去跑一个长会话（超过 contextWindowFor(model) 的\n"
                "  压缩阈值）再回来看。在看到第一行之前，第 2 条验收是未完成状态，\n"
                "  不是通过。"
            )
            return 0

        print(f"\n最近 {len(rows)} 条:")
        for tenant, sid, covered, created, size in rows:
            print(f"    {tenant:12} {sid:24} covered_id={covered:<8} {size}B  {created}")

        # ── 3. covered_id 的边界 ─────────────────────────────────────────
        print("\ncovered_id 落点检查（应落在该会话 id 区间内，且不是最大的那条 ——\n"
              "等于最大值意味着摘要覆盖了全部消息、近期窗口是空的）:")
        bad = 0
        for tenant, sid, covered, _created, _size in rows:
            lo, hi, cnt = conn.execute(text(
                "SELECT MIN(id), MAX(id), COUNT(*) FROM agent_messages "
                "WHERE tenant_id = :t AND session_id = :s"
            ), {"t": tenant, "s": sid}).fetchone()

            if lo is None:
                print(f"    ✗ {sid}: 摘要在，但 agent_messages 里一条都没有")
                bad += 1
                continue

            after = conn.execute(text(
                "SELECT COUNT(*) FROM agent_messages "
                "WHERE tenant_id = :t AND session_id = :s AND id > :c"
            ), {"t": tenant, "s": sid, "c": covered}).scalar()

            ok = lo <= covered < hi and after > 0
            mark = "✓" if ok else "✗"
            bad += 0 if ok else 1
            print(f"    {mark} {sid}: id∈[{lo},{hi}] 共{cnt}条, "
                  f"covered={covered}, 摘要后剩 {after} 条")

        # ── 4. 摘要长什么样 ──────────────────────────────────────────────
        raw = conn.execute(text(
            "SELECT summary FROM session_summaries ORDER BY created_at DESC LIMIT 1"
        )).scalar()
        try:
            msg = json.loads(raw)
            text_blocks = [
                b.get("text", "") for b in (msg.get("content") or [])
                if isinstance(b, dict)
            ]
            head = (text_blocks[0] if text_blocks else "")[:160]
            print(f"\n最新摘要: role={msg.get('role')} "
                  f"blocks={len(msg.get('content') or [])}")
            print(f"  开头: {head!r}")
            # 引擎的 compaction.IsSummaryMessage 认的就是这个开头；对不上的话
            # 恢复时那条会被当成普通 user 消息，摘要路径静默失效。
            if "Summary of earlier conversation" not in "".join(text_blocks)[:200]:
                print("  ⚠ 开头不是 'Summary of earlier conversation' —— "
                      "IsSummaryMessage 可能认不出它")
        except Exception as exc:
            print(f"\n✗ 摘要 JSON 解不开: {exc}")
            bad += 1

        return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
