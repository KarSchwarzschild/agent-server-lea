"""为「压缩端到端验证」建一套隔离的测试数据，或清掉它。

    python scripts/ctx_e2e_seed.py          建
    python scripts/ctx_e2e_seed.py --drop   删

## 为什么要单独一套

要看到 session_summaries 落第一行，得让一个真实会话的历史越过压缩触发线。生产
配置下那条线是 180,633 token（qwen3-max 窗口 258048），把历史堆到那么大要几十轮、
几百万 token 的输入费，而且每轮全价重发。

所以这里建一个**独立租户**，它的 provider 行把 qwen3-max 的窗口声明成 30000：

    budget  = 30000 × 0.875 = 26,250
    reduce  = 26,250 × 0.6  = 15,750
    compact = 26,250 × 0.8  = 21,000

模型仍然是真的 qwen3-max、真的 base_url 和 key —— 变的只是「我们告诉引擎这个模型
有多大」。走的是完全相同的代码路径，只是把触发线挪到几毛钱能到达的地方。

**不动现有那行 provider**：新增一行，tenant_id 不同。现网租户的窗口仍是 258048。
"""

from __future__ import annotations

import json
import sys

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))

import _dbconn as d

TENANT = "ctxtest"
AGENT_NAME = "ctx-e2e-agent"
WINDOW = 30_000


def drop(conn) -> None:
    with conn.cursor() as cur:
        for table in (
            "session_summaries", "agent_messages", "session_plans",
            "agents", "llm_providers",
        ):
            cur.execute(f"DELETE FROM `{table}` WHERE tenant_id = %s", (TENANT,))
            print(f"  清 {table}: {cur.rowcount} 行")


def seed(conn) -> int:
    with conn.cursor() as cur:
        # 真的 base_url / api_key —— 从现网那行抄，模型调用必须是真的。
        cur.execute(
            "SELECT base_url, api_key FROM llm_providers "
            "WHERE tenant_id = 'default' AND enabled = 1 LIMIT 1"
        )
        row = cur.fetchone()
        if not row:
            raise SystemExit("现网没有可抄的 provider 行")
        base_url, api_key = row

        cur.execute(
            "INSERT INTO llm_providers "
            "(tenant_id, name, base_url, api_key, models, model_limits, is_default, enabled) "
            "VALUES (%s, %s, %s, %s, %s, %s, 1, 1)",
            (TENANT, "ctxtest_provider", base_url, api_key, "qwen3-max",
             json.dumps({"qwen3-max": WINDOW})),
        )
        print(f"  provider: model_limits={{'qwen3-max': {WINDOW}}}  base_url={base_url}")

        cur.execute(
            "INSERT INTO agents "
            "(tenant_id, name, description, model, temperature, max_turns, max_tokens, "
            " kb_mode, memory_mode, timeout_seconds, is_system, enabled, created_by, "
            " owner_scope, soul_prompt) "
            "VALUES (%s, %s, %s, 'qwen3-max', 7, 4, 512, 'none', 'none', 120, 0, 1, "
            "        'ctx-e2e', 'platform', %s)",
            (TENANT, AGENT_NAME, "压缩端到端验证用，可删",
             "你是一个测试助手。无论用户说什么，都只回复一句不超过 20 字的确认。"),
        )
        agent_id = cur.lastrowid
        print(f"  agent: id={agent_id} model=qwen3-max max_tokens=512（回复短一点，"
              f"历史主要靠用户消息堆）")
        return agent_id


def main() -> int:
    d.utf8_stdout()
    conn = d.connect()
    try:
        if "--drop" in sys.argv:
            print(f"清理租户 {TENANT!r}:")
            drop(conn)
            return 0

        print(f"清掉上一次的残留（租户 {TENANT!r}）:")
        drop(conn)
        print("建新的:")
        agent_id = seed(conn)
        print(f"\n压缩触发线 = {WINDOW} × 0.875 × 0.8 = "
              f"{int(WINDOW * 0.875 * 0.8):,} token")
        print(f"AGENT_ID={agent_id}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
