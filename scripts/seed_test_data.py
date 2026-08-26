"""种子测试数据 —— 目的是**让授权 bug 暴露出来**，不是让页面上有东西可看。

    uv run python scripts/seed_test_data.py          # 建/重建种子数据
    uv run python scripts/seed_test_data.py --clear  # 只删

## 为什么是这个形状

单租户单用户的种子数据证明不了任何隔离性质 —— 每一个越权 bug 在那种数据下看起来
都是对的。要让 bug 可见，最少需要三组对照：

    alice vs bob      同租户不同用户   → 暴露"没按 owner 过滤"
    alice vs carol    不同租户         → 暴露"没按 tenant 过滤"
    platform vs user  不同归属         → 暴露"把平台资源当私有的"（或反之）

还有一个刻意的安排：**两个租户里的资源同名**。如果某个查询漏了 tenant 过滤，
列表里会出现两个"通用助手" —— 一眼就看见。名字取得不一样反而可能蒙混过去。

## 为什么用专用租户而不是 default

`default` 是 DEFAULT_TENANT_ID，很多代码在缺身份时会落到它（这本身是个待修的问题，
见 lea_rag/server_user_kb.py:32 那个 anonymous 兜底）。远程 MySQL 的 default 租户里
可能有真数据，而幂等重跑需要先删。所以种子数据只住在 SEED_TENANTS 里，
--clear 也只删这两个租户 —— 删不到别的东西。
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import text

# 两个服务各有自己的 engine / Base，但通常指向同一个 MySQL 库（不同表）。
from lea_config.database import (
    OWNER_SCOPE_PLATFORM,
    OWNER_SCOPE_USER,
    SessionLocal as ConfigSession,
)
from lea_config.models.agent import Agent
from lea_config.models.mcp_server import MCPServer
from lea_config.models.skill import Skill
from lea_rag.db import SessionLocal as RagSession
from lea_rag.db.models import KnowledgeBase, UserMemory

TENANT_A = "seed-acme"
TENANT_B = "seed-globex"
SEED_TENANTS = (TENANT_A, TENANT_B)

# 每个租户里的三个身份。名字在两个租户里刻意重复（alice 在两边都存在），
# 这样"只按 user_id 过滤、忘了 tenant"这种 bug 会立刻表现为跨租户串数据。
ADMIN, ALICE, BOB = "admin", "alice", "bob"


def clear(verbose: bool = True) -> None:
    """只删 SEED_TENANTS 里的行。"""
    with ConfigSession() as db:
        for model in (Agent, Skill, MCPServer):
            n = (
                db.query(model)
                .filter(model.tenant_id.in_(SEED_TENANTS))
                .delete(synchronize_session=False)
            )
            if verbose and n:
                print(f"  删 {model.__tablename__:14} {n}")
        # tools 同样走裸 SQL —— 不能用 lea-config 那份缺列的模型（见下面插入处的说明）。
        # 漏了这条的话重跑会撞 (tenant_id, name) 唯一约束。
        res = db.execute(
            text("DELETE FROM tools WHERE tenant_id IN (:a, :b)"),
            {"a": TENANT_A, "b": TENANT_B},
        )
        if verbose and res.rowcount:
            print(f"  删 {'tools':14} {res.rowcount}")
        db.commit()

    with RagSession() as db:
        for model in (KnowledgeBase, UserMemory):
            n = (
                db.query(model)
                .filter(model.tenant_id.in_(SEED_TENANTS))
                .delete(synchronize_session=False)
            )
            if verbose and n:
                print(f"  删 {model.__tablename__:14} {n}")
        db.commit()


def _seed_config_tenant(db, tenant: str) -> None:
    """一个租户里的 agent / skill / tool / mcp。"""

    # ── Agent：一个平台的 + 两个用户各自的 ────────────────────────────────
    db.add(
        Agent(
            tenant_id=tenant,
            name="通用助手",
            description="平台预置，租户内所有人可用",
            owner_scope=OWNER_SCOPE_PLATFORM,
            owner_user_id=None,
            # created_by 是审计字段：记"谁建的"。它是管理员，但这个 agent 的语义是
            # 租户共享 —— 这一行就是"created_by 不能当授权用"的活例子。
            created_by=ADMIN,
            category="general",
            welcome_message="你好，我是通用助手",
        )
    )
    for user in (ALICE, BOB):
        db.add(
            Agent(
                tenant_id=tenant,
                name=f"{user} 的私人助手",
                description=f"{user} 自己建的，别人不该看到",
                owner_scope=OWNER_SCOPE_USER,
                owner_user_id=user,
                created_by=user,
                category="general",
            )
        )

    # ── Skill：平台的 + alice 私有的 ─────────────────────────────────────
    # scope 是旧的归属编码，仍然被 user_skills.py 读着（004 迁移的 expand 阶段
    # 保留它权威），所以这里**两套都要写对且一致** —— 否则切读那一步会看到
    # 种子数据自相矛盾，分不清是代码错了还是数据错了。
    db.add(
        Skill(
            tenant_id=tenant,
            name="ppt_generator",
            display_name="PPT 生成",
            description="平台预置技能",
            owner_scope=OWNER_SCOPE_PLATFORM,
            owner_user_id=None,
            created_by=ADMIN,
            scope="system",          # 旧编码：system = 平台
            status="published",
        )
    )
    db.add(
        Skill(
            tenant_id=tenant,
            name="alice_weekly_report",
            display_name="周报助手",
            description="alice 自己建的私有技能",
            owner_scope=OWNER_SCOPE_USER,
            owner_user_id=ALICE,
            created_by=ALICE,
            scope="private",         # 旧编码：private + created_by = 用户私有
            status="published",
        )
    )

    # ── Tool：只有平台的 ────────────────────────────────────────────────
    #
    # 用户侧工具接口现在有了（POST /tools，普通用户建的落 owner_scope=user），但这里
    # 仍然只造平台工具：种子数据的用处是给授权规则提供固定的对照物，而"用户自建工具"
    # 那一格已经由 tests/test_tools_api.py 覆盖，不需要在库里长期占一行。
    #
    # ⚠️ 这里用裸 SQL 而不是 ORM 模型，原因是 **tools 表被三个服务各定义了一遍**：
    #
    #   lea-tools/db/models.py     执行侧，跟真实表一致
    #   lea-config/models/tool.py  配置侧，现已统一到同一套词汇（见那个文件的说明）
    #   lea-server/models/tool.py  老单体那份，服务已下线
    #
    # 两份活着的定义现在**列名一致**了（migrations/006 删掉了重复的
    # is_enabled / input_schema），所以用哪份插都对。但"谁拥有这张表"仍然没定 ——
    # 三份定义并存本身就是 #20 要收的东西 —— 所以继续用裸 SQL，不在种子脚本里
    # 悄悄宣布某一份是权威的。
    db.execute(
        text(
            """
            INSERT INTO tools
                (tenant_id, name, display_name, description, category,
                 execution_type, schema_json, enabled,
                 owner_scope, owner_user_id, created_at, updated_at)
            VALUES
                (:tenant, :name, '联网搜索', '平台内置工具', 'general',
                 'api', '{}', 1,
                 :scope, NULL, now(), now())
            """
        ),
        # 两个租户都叫 web_search —— 跟其他资源一样刻意同名。
        #
        # 这一度是不可能的：tools 上曾有个遗留的全局 UNIQUE(name)（出处是
        # lea-tools 模型里的 name=Column(unique=True)），工具名全平台唯一。
        # migrations/005 删掉了它，前提是先给 lea-tools 那四处按名字查工具的地方
        # 加上租户过滤 —— 顺序反了会从"建不了同名"退化成"随机拿到别租户的配置"。
        {"tenant": tenant, "name": "web_search", "scope": OWNER_SCOPE_PLATFORM},
    )

    # ── MCP：平台的 + alice 私有的 ──────────────────────────────────────
    db.add(
        MCPServer(
            tenant_id=tenant,
            name="filesystem",
            description="平台配置的 MCP",
            owner_scope=OWNER_SCOPE_PLATFORM,
            owner_user_id=None,
            created_by=ADMIN,
            transport="stdio",
            config={"command": "npx", "args": ["-y", "@modelcontextprotocol/server-filesystem", "/tmp"]},
        )
    )
    db.add(
        MCPServer(
            tenant_id=tenant,
            name="alice-notion",
            description="alice 自己接的 MCP",
            owner_scope=OWNER_SCOPE_USER,
            owner_user_id=ALICE,
            created_by=ALICE,
            transport="streamable_http",
            config={"url": "https://example.invalid/mcp"},
        )
    )


def _seed_rag_tenant(db, tenant: str) -> None:
    """一个租户里的知识库 + 记忆。"""

    # knowledge_bases 是唯一本来就把两个轴分开的表：owner 管可变性，
    # visibility 管可见性。所以这里能造出"alice 的但公开"这种状态 ——
    # 那正是 agents/skills 现在表达不了的一格。
    db.add(
        KnowledgeBase(
            tenant_id=tenant,
            name="alice 的私有资料库",
            description="只有 alice 能看能改",
            owner=ALICE,
            visibility="private",
        )
    )
    db.add(
        KnowledgeBase(
            tenant_id=tenant,
            name="alice 分享的公开库",
            description="alice 拥有，租户内可见 —— 检查'能看'和'能改'有没有被混为一谈",
            owner=ALICE,
            visibility="public",
        )
    )
    db.add(
        KnowledgeBase(
            tenant_id=tenant,
            name="bob 的私有资料库",
            description="alice 不该看到这个",
            owner=BOB,
            visibility="private",
        )
    )

    for user, contents in (
        (ALICE, ["喜欢用 Python", "在做检索服务的性能优化"]),
        (BOB, ["偏好简洁的回答", "负责前端重写"]),
    ):
        for content in contents:
            db.add(
                UserMemory(
                    tenant_id=tenant,
                    user_id=user,
                    content=content,
                    category="preference",
                    importance=5,
                )
            )


def seed() -> None:
    with ConfigSession() as db:
        for tenant in SEED_TENANTS:
            _seed_config_tenant(db, tenant)
        db.commit()

    with RagSession() as db:
        for tenant in SEED_TENANTS:
            _seed_rag_tenant(db, tenant)
        db.commit()


def print_expectations() -> None:
    """把"正确行为"写出来。没有这张表，种子数据只是一堆行。"""
    print(
        f"""
── 种子数据 ──────────────────────────────────────────────────────────────
两个租户 {TENANT_A} / {TENANT_B}，每个里面 {ADMIN} / {ALICE} / {BOB} 三个身份。
**两个租户里的资源同名** —— 漏 tenant 过滤时列表里会出现重复项，一眼可见。

每个租户里：
  Agent    通用助手(platform) · alice 的私人助手(user) · bob 的私人助手(user)
  Skill    ppt_generator(platform/system) · alice_weekly_report(user/private)
  Tool     web_search(platform)                     ← 无用户侧接口，故不造用户工具
  MCP      filesystem(platform) · alice-notion(user)
  知识库    alice 私有 · alice 公开 · bob 私有        ← 唯一能表达"我的但公开"的资源
  记忆      alice ×2 · bob ×2

── 正确行为（不符合就是有 bug）────────────────────────────────────────────
以 {TENANT_A} 的 alice 身份调用：
  Agent 列表      应该看到 2 个：通用助手 + alice 的私人助手
                  看到 3 个 → 没按 owner 过滤（能看到 bob 的）
                  看到 4+  → 没按 tenant 过滤（串到 {TENANT_B} 了）
  改 bob 的助手    应该 403
  改通用助手       应该 403（platform 归属，只有管理员能改）
  知识库列表       应该看到 2 个：alice 私有 + alice 公开
                  看到 bob 私有 → 没按 owner 过滤
  改 alice 公开库   应该允许（她是 owner）—— 若被拒，说明把"可见性"当成了"可变性"
  记忆列表         应该只有 alice 那 2 条

以 {TENANT_A} 的 admin 身份：
  跨租户列表       **不应该**看到 {TENANT_B} 的任何东西。租户管理员 ≠ 平台管理员。
                  （这一条曾经必然失败：/admin/knowledge-bases 的 tenant_id 是个
                  可选 query 过滤器，不传就返回全平台。现已改成从 actor 取，
                  四个端点都加了 require_admin —— 见 server_admin_kb.py。）

⚠️ "租户管理员能不能看用户的私有资源"是个产品决策，不是技术决策。上面按
   **不能看** 写的（最小权限）。如果产品上要能看（比如合规审计），那应该是一个
   独立的、带审计日志的接口，而不是让列表接口默默返回更多东西。
"""
    )


def main() -> int:
    # Windows 控制台默认 GBK，打印 ⚠️ / 框线字符会 UnicodeEncodeError —— 而那时
    # 数据已经写进去了，只是报告打不出来，看起来像整个脚本失败了。
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, OSError):
        pass

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--clear", action="store_true", help="只删种子数据，不重建")
    args = ap.parse_args()

    print(f"种子租户: {', '.join(SEED_TENANTS)}（只动这两个）")
    clear()
    if args.clear:
        print("已清空。")
        return 0
    seed()
    print_expectations()
    return 0


if __name__ == "__main__":
    sys.exit(main())
