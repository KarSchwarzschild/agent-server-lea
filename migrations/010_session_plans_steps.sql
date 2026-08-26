-- Migration: session_plans 增加结构化步骤 —— 计划从"一坨文本"变成"可追踪的步骤表"
-- 日期: 2026-08-20
--
-- 起因:
--   计划此前只有正文，没有任何东西记录"第 3 步做完了"。而正文被每轮原样拼进
--   message 前缀，落库的是拼过的那条 llm.Message，所以 ReloadHistory 读回 20 轮
--   就是 20 份计划全文 —— 一份 1500 token 的计划 × 20 轮 = 3 万 token 纯重复，
--   每轮全价重发（这个平台没有可继承的 prompt cache）。
--
-- 改成拉取式:
--   每轮只注入一行指针（"共 7 步，已完成 3 步。用 get_plan 查看全文"），全文由模型
--   按需调 get_plan 取。get_plan 声明了 Reproducible + Identity，所以历史里陈旧的
--   那几份会被引擎自动脱水成指针，始终只留最新一份。
--
--   进度落库而不是用引擎自带的 TodoWrite：那个工具的清单活在对话历史里，而这里
--   压缩是开着的（MaxContextTokens 已设）、跨 pod 只载入最近 20 轮、它给前端的
--   进度事件又是进程内的（"never recorded to telemetry or transcripts"）。三条都会
--   让清单在长执行里静默消失，而那正是最需要它的时候。
--
-- 两套步骤，跟 009 的两列正文一一对应:
--   approved_steps_json  已批准计划的步骤 + 进度，update_plan_step 改的是它
--   draft_steps_json     待批准计划的步骤（状态都是 pending，还没开始做）
--   approve 时跟正文一起搬过去，reject 时跟正文一起清掉。
--
-- 存 TEXT 不存 JSON 类型：SQLite 没有 JSON 类型（测试跑在它上面），而序列化和校验
-- 都在 Go 侧做，用 MySQL 的 JSON 类型换不到什么，却会让两边的 DDL 不再等价。
--
-- 形状: [{"title": "调研认证模块", "status": "completed"}, ...]
--        status ∈ pending / in_progress / completed
--
-- 执行方式: mysql -u root -p <db> < migrations/010_session_plans_steps.sql
--
-- 幂等性: 没有 ADD COLUMN IF NOT EXISTS（MySQL 8/9 不支持），重复执行会报
--         "Duplicate column name"。那是个安全的失败 —— 报错即说明已经加过了。

ALTER TABLE session_plans
    ADD COLUMN approved_steps_json TEXT NULL COMMENT '已批准计划的步骤与进度 JSON',
    ADD COLUMN draft_steps_json    TEXT NULL COMMENT '待批准计划的步骤 JSON';
