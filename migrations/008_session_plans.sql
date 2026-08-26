-- Migration: 新增 session_plans —— Plan 模式的持久化计划存储
-- 日期: 2026-08-19
-- 说明:
--   Plan 模式产出的计划作为独立 Workflow State 存储，不依赖对话历史窗口。
--   一会话一行（upsert），计划内容随 AskUser 提交时写入，用户批准后激活，
--   执行阶段每轮从这张表加载并注入 message 前缀。
--
--   状态流转:
--     draft  → 计划已提交，等用户表态（不注入）
--     active → 用户已进执行阶段（每轮注入）
--   新计划覆盖旧计划（REPLACE INTO 同一 session_id）。
--
-- 执行方式: mysql -u root -p <db> < migrations/008_session_plans.sql
--
-- 幂等：CREATE TABLE IF NOT EXISTS。

CREATE TABLE IF NOT EXISTS session_plans (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    session_id VARCHAR(64) NOT NULL,
    tenant_id  VARCHAR(64) NOT NULL DEFAULT '',
    content    TEXT NOT NULL,
    status     VARCHAR(20) NOT NULL DEFAULT 'draft',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_session (tenant_id, session_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
