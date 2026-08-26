-- 004 统一归属模型 —— expand 阶段（只加列 + 回填，不改任何读路径）
--
-- 背景：同一个"这个对象属于谁"的概念在库里被重新发明了四遍，编码各不相同：
--
--   skills           scope ∈ system/public/private  +  created_by
--   mcp_servers      owner_scope ∈ platform/user    +  owner_user_id   ← 唯一分对的
--   knowledge_bases  owner + visibility                                （在 lea-rag，本迁移不动）
--   agents           光用 created_by
--   tools            没有
--
-- 后果是同一批资源存在两套平行 CRUD（/agents 与 /admin/agents、/user-skills 与
-- /admin/skills、/knowledge-bases 与 /admin/knowledge-bases），只有一套加了归属
-- 约束 —— 因为从来没有一个统一的东西可以让检查建在上面。
--
-- ── 为什么分三步而不是一把改完 ────────────────────────────────────────────
--
-- expand-migrate-contract：
--
--   004（本文件）  加 owner_scope / owner_user_id 并回填。**旧列继续权威**，
--                  所有查询照旧读 scope / created_by。这一步不可能弄坏任何东西。
--   下一步         把查询切到新列（api/user_skills.py:94-95、api/user_agents.py:124 等）。
--   再下一步       删掉 skills.scope，created_by 降级为纯审计字段。
--
-- 一把改完的问题是：改列和改读必须同时上线，而它们在两个不同的部署单元里
-- （SQL 手动跑、代码走镜像）。中间那一小段时间里读的是还没回填的列。
--
-- ── ⚠️ 必须手动执行 ───────────────────────────────────────────────────────
--
-- lea-config 和 lea-rag 启动时都跑 Base.metadata.create_all()，它**只建表不改表**。
-- 所以：新建的库（本机 sqlite / 新 MySQL）自动就有这些列，而已有数据的库不会有 ——
-- 且在有查询引用这些列之前不报任何错。典型的"我这儿好的，线上炸了"。
--
--   mysql -h 202.205.160.9 -P 3307 -u <user> -p <dbname> < migrations/004_unify_ownership.sql
--
-- ── 回填规则 ──────────────────────────────────────────────────────────────
--
-- 一律回填成 platform（= 租户内共享，管理员可改），这是**唯一保持现状语义**的选择。
-- 现在这些资源实际上就是租户内共享的。回填成 user 会让存量资源突然只有创建者
-- 看得见，用户那边看起来就是"我的东西没了"。
--
-- MySQL 5.7 没有 IF NOT EXISTS for ADD COLUMN。这些语句重复执行会报
-- "Duplicate column name"，那是预期的 —— 说明已经跑过了，忽略即可。

-- ── agents ────────────────────────────────────────────────────────────────
ALTER TABLE agents
  ADD COLUMN owner_scope   VARCHAR(20) NOT NULL DEFAULT 'platform',
  ADD COLUMN owner_user_id VARCHAR(64) NULL;
CREATE INDEX ix_agents_owner_scope   ON agents (owner_scope);
CREATE INDEX ix_agents_owner_user_id ON agents (owner_user_id);

-- 存量全部视为平台提供。created_by 保留但不再承担授权语义 —— 平台预置的 agent
-- 的 created_by 是某个管理员的 user_id，跟"这是他私有的"不是一回事。
UPDATE agents SET owner_scope = 'platform', owner_user_id = NULL;

-- ── skills ────────────────────────────────────────────────────────────────
ALTER TABLE skills
  ADD COLUMN owner_scope   VARCHAR(20) NOT NULL DEFAULT 'platform',
  ADD COLUMN owner_user_id VARCHAR(64) NULL;
CREATE INDEX ix_skills_owner_scope   ON skills (owner_scope);
CREATE INDEX ix_skills_owner_user_id ON skills (owner_user_id);

-- skills 是唯一已经有可用信息能精确回填的表：api/user_skills.py:94-95 的
-- "系统的 OR 公开的 OR (私有的 AND 我建的)" 就是这个模型的完整表达。
--
--   scope='private' 且 created_by 非空  →  用户私有
--   其余（system / public / created_by 为空）→  平台
--
-- 注意 scope='public' 映射成 platform 只对**今天**成立：目前只有管理员接口能设
-- public（api/skills.py:230），用户建的一律 private（user_skills.py:156）。将来要
-- 支持"用户发布自己的技能"时，那是 owner_scope='user' + visibility='public' ——
-- 正是这两个轴要分开的原因。
UPDATE skills
   SET owner_scope   = 'user',
       owner_user_id = created_by
 WHERE scope = 'private'
   AND created_by IS NOT NULL
   AND created_by <> '';

UPDATE skills
   SET owner_scope   = 'platform',
       owner_user_id = NULL
 WHERE owner_scope <> 'user';

-- ── tools ─────────────────────────────────────────────────────────────────
-- 这张表压根没有归属信息，所以只能全部回填 platform。
-- 目前也确实只有管理员接口能建工具（/admin/tools），没有用户侧入口。
ALTER TABLE tools
  ADD COLUMN owner_scope   VARCHAR(20) NOT NULL DEFAULT 'platform',
  ADD COLUMN owner_user_id VARCHAR(64) NULL;
CREATE INDEX ix_tools_owner_scope   ON tools (owner_scope);
CREATE INDEX ix_tools_owner_user_id ON tools (owner_user_id);

UPDATE tools SET owner_scope = 'platform', owner_user_id = NULL;

-- ── mcp_servers：不动 ─────────────────────────────────────────────────────
-- 它本来就有 owner_scope + owner_user_id，列定义跟 OwnerMixin 完全一致。
-- 代码里只是把定义搬去了共享 mixin，不是 schema 变更。
--
-- ── agent_teams：不动，且**不该加** ──────────────────────────────────────
-- AgentTeam 跟 Agent 是 1:1（UniqueConstraint agent_id）且 ON DELETE CASCADE，
-- 它的归属应该从所属 agent 派生。加一份自己的归属列就允许出现
-- team.owner ≠ agent.owner —— 那是个等着发生的越权，而不是灵活性。
--
-- ── knowledge_bases：不动 ────────────────────────────────────────────────
-- 在 lea-rag，已经有 owner + visibility，两轴本来就是分开的（这张表是对的）。
-- 切读那一步再统一命名。
