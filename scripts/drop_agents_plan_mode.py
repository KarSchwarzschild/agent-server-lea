"""回滚 migrations/011 —— 删掉 agents.plan_mode。

## 为什么加了又删

加它的理由是"跟 kb_mode / memory_mode / sandbox_enabled 一个形状，按 agent 决定要不要
注册计划三件套"。那个类比不成立：那三个各自关的是真花钱的东西（知识库检索、记忆读写、
沙箱），而计划工具只是三个描述。

更重要的是产品语义搞错了：**计划模式是所有 agent 都支持的能力**，不是管理员配出来的。
要不要进规划态由每次请求带来（SendRequest.plan_mode），跟 agent 怎么配没关系。同名的
两个东西——请求参数和这个配置列——本来就是我命名撞车造成的混淆。

删之前确认所有行都是默认值 'on'，也就是这列从没被真正用过。

幂等：列不在就跳过。
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
    cur.execute("SHOW COLUMNS FROM agents LIKE 'plan_mode'")
    if not cur.fetchone():
        print("plan_mode 列不存在，无事可做。")
        sys.exit(0)

    # 安全网：有人改过默认值就先别删，那说明它已经在被用了
    cur.execute("SELECT plan_mode, COUNT(*) FROM agents GROUP BY plan_mode")
    dist = dict(cur.fetchall())
    print(f"删除前的分布: {dist}")

    non_default = {k: v for k, v in dist.items() if k != "on"}
    if non_default:
        conn.rollback()
        sys.exit(
            f"中止：有行不是默认值 {non_default} —— 说明这列已经在用了，"
            f"删掉会丢配置。先确认这些 agent 的预期行为。"
        )

    cur.execute("ALTER TABLE agents DROP COLUMN plan_mode")
    conn.commit()
    print("plan_mode 列已删除。")

    cur.execute("SHOW COLUMNS FROM agents LIKE 'plan_mode'")
    print(f"复查: {'✗ 还在' if cur.fetchone() else '✓ 已删除'}")

conn.close()
