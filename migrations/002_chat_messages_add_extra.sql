-- Migration: chat_messages 表加 extra JSON 列
-- 日期: 2026-07-08
-- 说明: 用于承载前端可视化字段 —— intent/skill/tool_calls/references/plan/parallel_subtasks/...
--       薄结构 + JSON blob 方案：schema 稳定、未来新字段零迁移。
--
-- 执行方式: mysql -u root -p <db> < migrations/002_chat_messages_add_extra.sql
--
-- 幂等：如果列已存在（例如新库通过 create_all() 直接建出来），INFORMATION_SCHEMA 判空后跳过。

SET @col_exists = (
    SELECT COUNT(*)
    FROM INFORMATION_SCHEMA.COLUMNS
    WHERE TABLE_SCHEMA = DATABASE()
      AND TABLE_NAME = 'chat_messages'
      AND COLUMN_NAME = 'extra'
);

SET @sql := IF(
    @col_exists = 0,
    'ALTER TABLE chat_messages ADD COLUMN extra JSON NULL',
    'SELECT ''chat_messages.extra already exists, skipping'' AS note'
);

PREPARE stmt FROM @sql;
EXECUTE stmt;
DEALLOCATE PREPARE stmt;
