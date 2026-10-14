"""插入一条测试专家，覆盖专家切换 / 人格 / 工具绑定等功能验收。"""
import json

import pymysql

conn = pymysql.connect(
    host="202.205.160.9",
    port=3307,
    user="root",
    password="123456",
    database="lea-platform-test",
    charset="utf8mb4",
)

TENANT_ID = "test2"
CREATED_BY = "luhaoyang"

# soul_prompt 里塞一个不可伪造的 marker，expert_switch_e2e 用来断言人格生效。
SOUL_PROMPT = (
    "你是「测试专家 Alpha」，一个专门用于功能验收的测试助手。\n"
    "无论用户说什么，每次回复的**结尾**必须原样输出这串标记（不得省略、修改或翻译）：\n"
    "【TEST-EXPERT-ALPHA-9F3】"
)

EXAMPLE_QUESTIONS = json.dumps(
    ["帮我测试一下专家切换", "当前是哪个专家？", "运行一段 Python 代码"],
    ensure_ascii=False,
)

sql = """
INSERT INTO agents (
    tenant_id, name, description, avatar, welcome_message,
    soul_prompt, category, example_questions, sort_order,
    model, temperature, max_turns, max_tokens,
    kb_mode, memory_mode, timeout_seconds,
    is_system, enabled, owner_scope, owner_user_id, created_by
) VALUES (
    %s, %s, %s, %s, %s,
    %s, %s, %s, %s,
    %s, %s, %s, %s,
    %s, %s, %s,
    %s, %s, %s, %s, %s
)
"""

values = (
    TENANT_ID,
    "测试专家 Alpha",
    "用于专家切换、人格 marker、工具绑定等功能验收的测试专家",
    "",
    "你好，我是测试专家 Alpha，随时可以验证专家功能。",
    SOUL_PROMPT,
    "general",
    EXAMPLE_QUESTIONS,
    0,
    "",          # model 留空 → 走 lea-config 默认
    7,           # temperature
    20,          # max_turns
    4096,        # max_tokens
    "tool",      # kb_mode
    "always",    # memory_mode
    120,         # timeout_seconds
    False,       # is_system
    True,        # enabled
    "platform",  # owner_scope：租户内所有人可见
    None,        # owner_user_id
    CREATED_BY,
)

with conn:
    with conn.cursor() as cur:
        cur.execute(sql, values)
        agent_id = cur.lastrowid
    conn.commit()

print(f"OK: agent id={agent_id}, tenant={TENANT_ID}")
