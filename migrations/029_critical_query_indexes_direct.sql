-- 029 · 关键查询索引：修复三个高频全表扫描（直接执行版本）
--
-- 原版本使用 PREPARE/EXECUTE 动态 SQL 做幂等检查，但 pymysql 不支持。
-- 此版本改为直接执行，依赖 MySQL 8+ 的 IF NOT EXISTS 语法。
--
-- 如果是 MySQL 5.7，需要手动检查索引是否存在再执行。

-- 1. 会话列表索引（覆盖 WHERE + ORDER BY + 主键）
ALTER TABLE `chat_sessions`
  ADD KEY IF NOT EXISTS `idx_sessions_list` (`tenant_id`, `user_id`, `is_archived`, `last_active_at`, `id`);

-- 2. 会话挂载索引（扩展加 id，避免 filesort）
-- MySQL 不支持 DROP IF EXISTS，需要先检查
DROP INDEX `idx_session` ON `session_attachments`;

ALTER TABLE `session_attachments`
  ADD KEY `idx_session` (`tenant_id`, `session_id`, `id`);

-- 3. 会话附件查询索引（按 session_id 而非 user_id）
ALTER TABLE `chat_attachments`
  ADD KEY IF NOT EXISTS `idx_attachment_session` (`tenant_id`, `session_id`, `id`);

-- 自检（应该各输出 > 0）
SELECT
  (SELECT COUNT(*) FROM information_schema.STATISTICS
     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'chat_sessions'
       AND INDEX_NAME = 'idx_sessions_list')       AS sessions_list_cols,
  (SELECT COUNT(*) FROM information_schema.STATISTICS
     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'session_attachments'
       AND INDEX_NAME = 'idx_session')             AS attachment_session_id_cols,
  (SELECT COUNT(*) FROM information_schema.STATISTICS
     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'chat_attachments'
       AND INDEX_NAME = 'idx_attachment_session')  AS attachment_session_cols;
