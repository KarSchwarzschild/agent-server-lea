-- Agent 配置版本表：每次修改留一份不可变快照，改坏了能回去。
--
-- 为什么需要：skills 有 skill_versions（015），agents 一直没有。于是一个被调教好
-- 的 agent，谁手滑改了 soul_prompt，上一版就永久没了。发布审批里的
-- resource_snapshot（032）是审批流的留档，只在"提交审批"那一刻产生，不覆盖日常
-- 编辑，也没有回滚路径。
--
-- 设计跟 015 一致：**追加式、不可变**。当前状态也在表里（revision 最大的那条），
-- 所以"回滚到 N"＝读 N 的快照写回 agents，并**再追加一条新 revision** ——
-- 历史永不改写，回滚本身也是一次有记录的变更。
--
-- revision 用每个 agent 自己的单调整数，不用 semver：agent 配置没有对外发布的
-- 版本号语义（skill 有，因为它是 bundle 要被别人引用），编辑序号够用且不会撞。
--
-- 执行前请确认 036 已完成。本文件可重复执行（IF NOT EXISTS + WHERE NOT EXISTS）。

CREATE TABLE IF NOT EXISTS agent_versions (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    agent_id    INT NOT NULL,
    revision    INT NOT NULL,
    snapshot    JSON NOT NULL,
    -- 这一版是怎么来的：create / update / rollback。rollback 时 change_note 里
    -- 记"回滚自 revision N"，否则历史里会出现一条没来由的"内容倒退"。
    change_kind VARCHAR(20) NOT NULL DEFAULT 'update',
    change_note VARCHAR(500) NOT NULL DEFAULT '',
    created_by  VARCHAR(100) NULL,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_agent_versions_agent FOREIGN KEY (agent_id)
        REFERENCES agents(id) ON DELETE CASCADE,
    CONSTRAINT uq_agent_versions_agent_rev UNIQUE (agent_id, revision),
    INDEX idx_agent_versions_agent (agent_id, revision)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 给现存 agent 各补一条 revision=1 作为基线。没有这条的话，第一次编辑之后
-- 历史里只有"改成了什么"，没有"原来是什么"，回滚无从下手。
--
-- 快照字段 = AgentUpdate 允许改的那 16 个。刻意**不含** id / tenant_id /
-- owner_* / is_system / created_* —— 那些不是配置，回滚时也不该被覆盖。
INSERT INTO agent_versions
    (agent_id, revision, snapshot, change_kind, change_note, created_by)
SELECT
    a.id,
    1,
    JSON_OBJECT(
        'name',              a.name,
        'description',       COALESCE(a.description, ''),
        'avatar',            COALESCE(a.avatar, ''),
        'welcome_message',   COALESCE(a.welcome_message, ''),
        'soul_prompt',       COALESCE(a.soul_prompt, ''),
        'category',          COALESCE(a.category, 'general'),
        'example_questions', COALESCE(a.example_questions, JSON_ARRAY()),
        'sort_order',        COALESCE(a.sort_order, 0),
        'model',             COALESCE(a.model, ''),
        'temperature',       COALESCE(a.temperature, 7),
        'max_turns',         COALESCE(a.max_turns, 100),
        'max_tokens',        COALESCE(a.max_tokens, 4096),
        'kb_mode',           COALESCE(a.kb_mode, 'tool'),
        'memory_mode',       COALESCE(a.memory_mode, 'always'),
        'timeout_seconds',   COALESCE(a.timeout_seconds, 120),
        'enabled',           IF(a.enabled, TRUE, FALSE)
    ),
    'baseline',
    '037 迁移补的基线版本（此前无版本记录）',
    a.created_by
FROM agents a
WHERE NOT EXISTS (
    SELECT 1 FROM agent_versions v WHERE v.agent_id = a.id
);
