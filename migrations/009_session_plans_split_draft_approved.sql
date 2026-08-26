-- Migration: session_plans 拆分「已批准的计划」和「待批准的修订」
-- 日期: 2026-08-20
--
-- 起因:
--   008 的形状是一行两列 content + status，status 在 draft/active 之间翻。这意味着
--   「已批准的那份」和「等着批准的那份」抢同一行。而 SavePlan 用的是
--   REPLACE INTO ... VALUES(..., 'draft')，所以执行中途提交一份修订版会**在用户还
--   没表态之前**就把已批准的计划删掉。
--
--   实测（SQLite 上跑的同一套 store 代码）：
--     批准后        approved = "计划 v1"
--     提交 v2 之后  approved = ""        ← v1 没了
--     表里共 1 行:  content="计划 v2" status="draft"
--
--   用户此时点「否决」，v2 不激活、v1 也回不来，执行阶段从此一份计划都没有 ——
--   而且没有任何提示：LoadActivePlan 返回空串，注入那段直接跳过，模型继续跑，
--   只是不再有计划约束了。用户的本意是「别改了，按原来的走」。
--
-- 改成:
--   approved_content  已批准、正在执行的那份。只有 approve 动作会写它。
--   draft_content     待批准的那份（首次提交或修订版）。save_plan 只写它。
--
--   两者独立存在，于是三个动作各自只碰自己该碰的列：
--     save_plan  →  draft = 新内容        （approved 不动）
--     approve    →  approved = draft, draft = NULL
--     reject     →  draft = NULL          （approved 不动 → 按原计划继续）
--
--   status 列跟着删掉：状态现在由「哪一列非空」表达，不需要第二个真相来源。
--   这个库已经被「一张表两种含义」坑过两次（tools / mcp_servers），不再留一份。
--
-- 数据搬迁:
--   现网只有 1 行且是测试数据（tenant='plan-tenant'，content 实际是一句追问而不是
--   计划——那正是 AskUser 和 save_plan 耦合留下的痕迹）。仍然按状态老实搬，因为
--   本地/测试环境可能有别的行。
--
-- 执行方式: mysql -u root -p <db> < migrations/009_session_plans_split_draft_approved.sql
--
-- ⚠️ 不可回滚：最后两步 DROP 掉了 content 和 status。执行前确认 008 的数据已按上面
--    的规则搬进新列（中间的 SELECT 会把结果打出来）。

ALTER TABLE session_plans
    ADD COLUMN approved_content TEXT NULL COMMENT '已批准、正在执行的计划',
    ADD COLUMN draft_content    TEXT NULL COMMENT '待用户批准的计划（首次或修订）';

-- 老 active → 已批准；老 draft → 待批准。两者互斥，所以搬完只会有一列非空。
UPDATE session_plans SET approved_content = content WHERE status = 'active';
UPDATE session_plans SET draft_content    = content WHERE status = 'draft';

-- 搬迁结果自检：应当每行恰好有一列非空，orphan 必须是 0。
SELECT
    COUNT(*)                                                                   AS total,
    SUM(approved_content IS NOT NULL)                                          AS approved_rows,
    SUM(draft_content    IS NOT NULL)                                          AS draft_rows,
    SUM(approved_content IS NULL AND draft_content IS NULL)                    AS orphan
FROM session_plans;

ALTER TABLE session_plans
    DROP COLUMN content,
    DROP COLUMN status;
