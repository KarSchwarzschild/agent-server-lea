-- Migration: 新增 session_summaries —— 上下文压缩摘要落库
-- 日期: 2026-08-19
-- 说明:
--   引擎的 maybeCompact 在上下文接近预算时把旧轮次总结为一条 summary 消息，
--   保留近几轮原始对话。这个 summary 只存在于 Pod A 的内存里 —— DB 里还是
--   全部原始消息。Pod B 接管时 LoadHistory 读到原始消息，又触发一次 compact，
--   浪费一次 LLM 调用且摘要内容可能不同。
--
--   本表持久化 compact 产生的 summary 及其覆盖范围（covered_id），
--   让 LoadHistory 能直接返回 [summary + summary 之后的原始消息]，
--   与 Pod A 内存里的工作历史一致。
--
--   agent_messages 原封不动 —— 原始历史必须保留（审计、重放、前端投影）。
--   本表只是工作视图的缓存：summary 可以从原始消息重建，落库只是为了
--   跨 pod 时省掉重复的 LLM 调用。
--
-- 执行方式: mysql -u root -p <db> < migrations/013_session_summaries.sql
--
-- 幂等：CREATE TABLE IF NOT EXISTS。

CREATE TABLE IF NOT EXISTS session_summaries (
    -- 一个会话只有一条 summary（compact 是增量的，新 summary 替换旧的），
    -- 所以用 (tenant_id, session_id) 做主键，upsert 即可。
    session_id  VARCHAR(64) NOT NULL,
    tenant_id   VARCHAR(64) NOT NULL DEFAULT '',

    -- compact 产生的 summary 消息（引擎原生 llm.Message 的 JSON）。
    -- user role + 单个 text block，内容以 "Summary of earlier conversation" 开头。
    summary JSON NOT NULL,

    -- summary 覆盖到的最后一条 agent_messages.id。
    -- LoadHistory 用 SELECT ... WHERE id > covered_id 取 summary 之后的原始消息。
    covered_id BIGINT NOT NULL,

    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,

    PRIMARY KEY (tenant_id, session_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
