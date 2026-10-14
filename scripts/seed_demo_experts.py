"""给前端测试造两个**持久、能真跑**的配置：一个专家 + 一个专家团（STAR/团长模式）。

    python scripts/seed_demo_experts.py          # 建（先清掉上一次的同名 demo）
    python scripts/seed_demo_experts.py --drop    # 只清理

跟 e2e 里那些 marker agent 不同：这些是真实人格、平台共享、enabled，前端目录能列出、
能召唤、能真跑任务。用 created_by='demo-experts' 打标，方便整体清理。

专家团用 STAR（团长）模式：团长自己的 soul_prompt + decomposer/synthesizer_prompt 就是
"主持人"，成员是被团长通过 ask_expert 调用的专家。strategy=round_robin → 引擎按星型跑。
"""

from __future__ import annotations

import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import _dbconn as d

TENANT = "default"
MODEL = "qwen3-max"          # 现网 agent 都用它，provider 一定匹配得上
TAG = "demo-experts"          # created_by 打标，便于清理


def _agent(cur, name, desc, soul, *, category="general", welcome="", examples=None,
           memory_mode="off", kb_mode="none", max_turns=8, max_tokens=2000):
    cur.execute(
        "INSERT INTO agents "
        "(tenant_id, name, description, avatar, welcome_message, soul_prompt, category, "
        " example_questions, sort_order, model, temperature, max_turns, max_tokens, "
        " kb_mode, memory_mode, timeout_seconds, is_system, enabled, created_by, "
        " owner_scope, owner_user_id) "
        "VALUES (%s,%s,%s,'',%s,%s,%s,%s,100,%s,7,%s,%s,%s,%s,180,0,1,%s,'platform',NULL)",
        (TENANT, name, desc, welcome, soul, category,
         json.dumps(examples or [], ensure_ascii=False),
         MODEL, max_turns, max_tokens, kb_mode, memory_mode, TAG),
    )
    return cur.lastrowid


def drop(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM agents WHERE created_by=%s", (TAG,))
        ids = [r[0] for r in cur.fetchall()]
        for aid in ids:
            cur.execute("DELETE FROM agent_team_members WHERE member_agent_id=%s", (aid,))
            cur.execute("DELETE FROM agent_teams WHERE agent_id=%s", (aid,))
        # 团成员绑定可能按 team_id 挂在团长下，单独再清一遍团长的 team 行的成员
        for aid in ids:
            cur.execute("SELECT id FROM agent_teams WHERE agent_id=%s", (aid,))
            for (tid,) in cur.fetchall():
                cur.execute("DELETE FROM agent_team_members WHERE team_id=%s", (tid,))
            cur.execute("DELETE FROM agent_teams WHERE agent_id=%s", (aid,))
        if ids:
            cur.execute("DELETE FROM agents WHERE created_by=%s", (TAG,))
        print(f"清掉 {len(ids)} 个旧 demo agent: {ids}")
    conn.commit()


def seed(conn):
    with conn.cursor() as cur:
        # ── 专家（单体）───────────────────────────────────────
        expert = _agent(
            cur,
            name="教学设计专家",
            desc="面向中小学与高校的课程/单元/课时教学设计顾问，给出结构化、可落地的设计方案。",
            category="教学",
            welcome="你好，我是教学设计专家。告诉我学段、学科和主题，我来帮你把一节课/一个单元设计出来。",
            examples=[
                "帮我设计一节初中物理《浮力》的探究课",
                "把这个单元目标拆成 4 课时，并给出每课时的评价方式",
                "这节课怎么融入项目式学习？",
            ],
            soul=(
                "你是一位资深教学设计专家，熟悉建构主义、UbD 逆向设计和项目式学习。"
                "面对任何教学设计需求，你都：①先确认学段/学科/学情等关键前提，缺了就先问；"
                "②按'学习目标→评价方式→学习活动'的逆向顺序组织方案；"
                "③目标写成可观测的行为动词，活动落到时间分配和分组形式。"
                "回答用清晰的小标题和条目，避免空泛套话。"
            ),
        )
        print(f"专家：教学设计专家 id={expert}")

        # ── 专家团（STAR / 团长）──────────────────────────────
        # 团长：主持人。它的 soul_prompt 是团队对外的人格；decomposer/synthesizer 控制
        # "怎么把问题拆给专家、怎么把专家的结论汇总"。
        leader = _agent(
            cur,
            name="教研工作室（专家团）",
            desc="一个由课程设计与学习评价两位专家组成的教研团队，团长负责拆解任务、汇总成完整方案。",
            category="教学",
            welcome="欢迎来到教研工作室。给我一个教学任务，我会协调课程设计和评价两位专家一起产出方案。",
            examples=[
                "为高一《函数》单元做一套完整的教学+评价方案",
                "设计一节公开课并配好评价量规",
            ],
            soul=(
                "你是教研工作室的主持人（团长），负责统筹课程设计专家和学习评价专家协作，"
                "为用户产出完整、连贯的教研方案。你自己不直接写细节，而是把任务合理拆分给合适的专家，"
                "再把他们的产出整合成一份统一、无重复、可直接使用的方案。"
            ),
            max_turns=12, max_tokens=3000,
        )
        member_course = _agent(
            cur,
            name="课程设计专家（团队成员）",
            desc="教研工作室成员：负责学习目标、学习活动与课时安排。",
            category="团队成员",
            soul=(
                "你是课程设计专家。收到主持人分派的任务时，只负责'学习目标、学习活动、课时安排'"
                "这一块：目标用可观测行为动词，活动写清形式与时间，课时给出总数和分配。"
                "简洁、专业，不越界去写评价细则（那是评价专家的事）。"
            ),
        )
        member_assess = _agent(
            cur,
            name="学习评价专家（团队成员）",
            desc="教研工作室成员：负责评价方式、量规与测评工具。",
            category="团队成员",
            soul=(
                "你是学习评价专家。收到主持人分派的任务时，只负责'评价'这一块：给出过程性与"
                "终结性评价的配比、可操作的评价量规（分维度分等级）、以及配套测评工具建议。"
                "与课程目标对齐，不去重写教学活动。"
            ),
        )
        print(f"团长 id={leader}  成员：课程设计 id={member_course}  评价 id={member_assess}")

        # agent_teams：strategy=round_robin → 引擎按 STAR 跑（团长 + ask_expert）。
        # decomposer/synthesizer 就是"主持人的提示词"。
        cur.execute(
            "INSERT INTO agent_teams (agent_id, strategy, decomposer_prompt, synthesizer_prompt, max_subtasks) "
            "VALUES (%s,'round_robin',%s,%s,4)",
            (leader,
             "把用户的教研任务拆成给'课程设计专家'和'学习评价专家'的子任务，"
             "各自任务描述清楚、不重叠。先课程设计、再学习评价。",
             "把两位专家的产出整合成一份完整方案：先课程（目标/活动/课时），"
             "后评价（配比/量规/工具），去掉重复，保证目标与评价一一对应。"),
        )
        team_id = cur.lastrowid
        for aid, nm in ((member_course, "课程设计专家"), (member_assess, "学习评价专家")):
            cur.execute(
                "INSERT INTO agent_team_members (team_id, member_agent_id, role, name) "
                "VALUES (%s,%s,'worker',%s)", (team_id, aid, nm))
        print(f"专家团 team_id={team_id} 已挂 2 名成员")

        conn.commit()
        print("\n完成。前端目录里应能看到：")
        print(f"  · 教学设计专家            agent_id={expert}")
        print(f"  · 教研工作室（专家团）    agent_id={leader}  ← 召唤这个跑团队协作")
        print(f"    （成员 {member_course}/{member_assess} 也会作为普通 agent 出现在目录里）")
        return expert, leader


def main():
    d.utf8_stdout()
    conn = d.connect()
    try:
        drop(conn)  # 先清上一次的，保证可重复运行
        if "--drop" in sys.argv:
            return 0
        seed(conn)
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
