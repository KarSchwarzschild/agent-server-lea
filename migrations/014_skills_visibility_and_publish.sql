-- Migration: skills 三层可见性收口 + 上架审核
-- 日期: 2026-08-21
--
-- ## 起因
--
-- 产品要三层可见性：用户可见 / 租户下可见 / 所有人可见，而且从低层往高层「上架」
-- 需要审核。表上其实已经有能表达这三层的东西（OwnerMixin + TenantMixin），缺的是
-- 把旧编码撤掉、把审核这件事建模出来。
--
--     用户可见    (t1,      user,     alice)
--     租户下可见  (t1,      platform, NULL)
--     所有人可见  (default, platform, NULL)
--
-- 「上架」= 改这两三列。跟 tools 表和 MCP 方案是同一套编码，不用学两遍。
--
-- ## 删掉的两列
--
-- **scope**（private/system/public）—— 旧的归属编码。model 注释自己写着这是
-- expand-migrate-contract 的 expand 阶段、切读之后就删。现在切读，所以删。
--
-- 而且它跟归属列在现网数据上**是矛盾的**：6 个真 skill（pptx / xlsx / pdf /
-- pptx-generator / skill-creator / internal-comms）是 (default, platform, NULL)，
-- 新编码里这是「所有人可见」；但 scope='private'，旧编码里这是「只有创建者可见」。
-- 原因是 upload 的 scope 参数默认 "private"，传的人没填。归属列是对的 —— 这几个
-- 显然是平台级能力。删掉 scope，矛盾随之消失。
--
-- **status**（draft/active/published）—— 它一列兼三个意思：
--
--     q.filter(Skill.status == "published")     ① 上架与否
--     "enabled": skill.status == "published"    ② 启用与否
--     status = Column(..., default="draft")     ③ 生命周期
--
-- 而 upload 写的是 'active' —— 这个词在上面哪个语义里都对不上。实测后果：6 个真
-- skill 全部返回 enabled=false，普通用户列表里一个都看不见。**但投递不看它**
-- （internal.py 取绑定 skill 时无 status 过滤），所以卡住的是「发现」不是「使用」，
-- 已经绑上的照常工作 —— 这就是它一直没显形的原因。
--
-- 拆开之后：可见性归三元组，启用归新的 enabled 列，生命周期不需要单独表达
-- （草稿就是「用户私有 + 没申请上架」）。
--
-- ## 为什么这轮敢删
--
-- 读这两列的是 lea-server 的 SkillService。而 infra/k8s/README.md 明写「lea-server
-- 不在列——它已经退出调用链」，k8s 清单里只有 lea-agent / lea-config / lea-rag /
-- lea-tools 四个。infra/docker-compose.dev.yml 里那个 backend 条目是过期的，
-- 同一份 README 点名说了别参照。
--
-- ## skill_publish_requests
--
-- 上架申请单独一张表，不是往 skill 行上加几列。理由是**审核有历史价值**：
-- 「上次申请被驳回过，理由是 X」在第二次申请时仍然有用，而放在 skill 行上第二次
-- 申请会把第一次的记录冲掉。另外 skill 行保持干净 —— 它的归属列永远是「现在是什么」，
-- 不掺「有人申请了什么」。
--
-- 审核人按目标层分：
--     用户私有 → 租户下可见    租户管理员审
--     租户下   → 所有人可见    平台管理员审
--
-- 执行方式: mysql -u root -p <db> < migrations/014_skills_visibility_and_publish.sql
--
-- ⚠️ 不可回滚：DROP 掉了 scope 和 status。执行前先跑 scripts/backup_skills.py。

-- ── 1. enabled ────────────────────────────────────────────────────────────
--
-- 从 status 回填：只有 draft 算停用，active / published 都算启用。现网 6 个 active
-- + 4 个 published，0 个 draft，所以回填后全是 1 —— 那正是既有行为（投递侧本来就
-- 不看 status，绑了就投）。

ALTER TABLE skills
    ADD COLUMN enabled TINYINT(1) NOT NULL DEFAULT 1
    COMMENT '启用：0 = 停用，已绑的 agent 也不再拿到它';

UPDATE skills SET enabled = CASE WHEN status = 'draft' THEN 0 ELSE 1 END;

-- ── 2. 自检 ───────────────────────────────────────────────────────────────
--
-- 归属三元组必须能表达每一行。owner_scope 为空的行删了 scope 之后就无法归类了，
-- 那种行必须先修。orphan 不为 0 就别往下走。

SELECT
    COUNT(*)                                                        AS total,
    SUM(owner_scope = 'user'     AND owner_user_id IS NOT NULL)      AS 用户私有,
    SUM(owner_scope = 'platform' AND tenant_id <> 'default')         AS 租户可见,
    SUM(owner_scope = 'platform' AND tenant_id  = 'default')         AS 所有人可见,
    SUM(owner_scope IS NULL OR owner_scope = ''
        OR (owner_scope = 'user' AND owner_user_id IS NULL))         AS orphan
FROM skills;

-- ── 3. 删掉旧编码 ─────────────────────────────────────────────────────────

ALTER TABLE skills DROP COLUMN scope, DROP COLUMN status;

-- ── 4. 上架申请 ───────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS skill_publish_requests (
    id           BIGINT AUTO_INCREMENT PRIMARY KEY,
    skill_id     INT         NOT NULL,
    -- 冗余存一份租户，为的是「列出本租户的待审申请」不用 JOIN skills。
    -- 申请归属跟着 skill 走，skill 删了申请级联删，所以不会漂。
    tenant_id    VARCHAR(64) NOT NULL,
    requested_by VARCHAR(100) NOT NULL,
    -- tenant = 上架到租户内所有人可见；global = 上架到所有租户可见。
    -- 刻意不复用 owner_scope 的词汇（platform/user）—— 那两个值区分不了
    -- 「租户内共享」和「全平台共享」，它们的 owner_scope 都是 platform，
    -- 差别在 tenant_id。
    target_scope VARCHAR(20) NOT NULL,
    status       VARCHAR(20) NOT NULL DEFAULT 'pending',
    review_note  TEXT        NULL,
    reviewed_by  VARCHAR(100) NULL,
    reviewed_at  DATETIME    NULL,
    created_at   DATETIME    DEFAULT CURRENT_TIMESTAMP,
    updated_at   DATETIME    DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    CONSTRAINT fk_spr_skill FOREIGN KEY (skill_id)
        REFERENCES skills(id) ON DELETE CASCADE,

    -- 待审列表的主查询：某租户下所有 pending。
    INDEX idx_spr_pending (tenant_id, status),
    INDEX idx_spr_skill (skill_id, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 「同一个 skill 同时只能有一个在审」用唯一索引表达不了（MySQL 没有部分索引），
-- 所以由接口层挡：提交申请前先查有没有 pending 的。这条写在这里是为了让下一个人
-- 知道那个检查为什么必须在应用层，而不是以为漏了约束。
