-- 029 · 关键查询索引：修复三个高频全表扫描
--
-- 发现时机：生产就绪评估 2026-09-14，代码全扫 + 查询模式分析
--
-- ## 问题
--
-- 1. **chat_sessions.ListSessions 全表扫**（最高频）
--    查询：WHERE tenant_id=? AND user_id=? AND is_archived=0 ORDER BY last_active_at DESC
--    现有：idx_chat_sessions_project(tenant_id, user_id, project_id, last_active_at)
--    问题：查询不带 project_id，且 is_archived=0 的过滤无法用到索引
--    影响：每次打开对话页都扫全表，1000 会话后 p99 延迟 > 500ms
--
-- 2. **session_attachments.LoadSessionAttachments filesort**（每条消息）
--    查询：WHERE tenant_id=? AND session_id=? ORDER BY id
--    现有：idx_session(tenant_id, session_id)
--    问题：缺 id 列导致 filesort（虽然行数少，但每条消息都调）
--    影响：attachmentsChanged 重建检查，多副本下每秒数十次
--
-- 3. **chat_attachments 按 session_id 查询失配**（沙箱启动）
--    查询：WHERE tenant_id=? AND session_id=? ORDER BY id
--    现有：idx_attachment_user(tenant_id, user_id, created_at)
--    问题：索引按 user_id 设计，但查询是 session_id，完全不匹配
--    影响：LoadSessionAttachmentsForStage（沙箱物化）扫全表
--
-- ## 设计偏差根因
--
-- 早期索引按**用户级**（user_id）设计，但实际查询是**会话级**（session_id）。
-- 会话挂载、附件查询都需要"这个会话的附件"，而不是"这个用户的附件"。
--
-- ## 索引开销
--
-- 假设 10k 会话、5k 挂载、2k 附件：
--   idx_sessions_list:        10k × (4+4+1+8+36) ≈ 530 KB
--   session_attachments.id:   5k  × 4            ≈  20 KB
--   idx_attachment_session:   2k  × (4+36+4)    ≈  88 KB
-- 总计 < 1 MB，相对数据量可忽略。

-- 1. 会话列表索引（覆盖 WHERE + ORDER BY + 主键）
SET @add_sessions_list_idx := IF(
  (SELECT COUNT(*) FROM information_schema.STATISTICS
     WHERE TABLE_SCHEMA = DATABASE()
       AND TABLE_NAME = 'chat_sessions'
       AND INDEX_NAME = 'idx_sessions_list') = 0,
  'ALTER TABLE `chat_sessions`
     ADD KEY `idx_sessions_list` (`tenant_id`, `user_id`, `is_archived`, `last_active_at`, `id`)',
  'SELECT ''idx_sessions_list already present''');
PREPARE stmt FROM @add_sessions_list_idx; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 2. 会话挂载索引（扩展加 id，避免 filesort）
--
-- 先删旧索引（只有 tenant_id, session_id），再加新索引（加上 id）。
-- 如果旧索引不存在（比如这张表是后来建的，压根没索引），DROP 会报错，用
-- IF EXISTS 绕过。新索引用幂等检查，防止这条迁移重复跑。
SET @drop_old_session_idx := IF(
  (SELECT COUNT(*) FROM information_schema.STATISTICS
     WHERE TABLE_SCHEMA = DATABASE()
       AND TABLE_NAME = 'session_attachments'
       AND INDEX_NAME = 'idx_session') > 0,
  'ALTER TABLE `session_attachments` DROP KEY `idx_session`',
  'SELECT ''idx_session not present, skip drop''');
PREPARE stmt FROM @drop_old_session_idx; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @add_new_session_idx := IF(
  (SELECT COUNT(*) FROM information_schema.STATISTICS
     WHERE TABLE_SCHEMA = DATABASE()
       AND TABLE_NAME = 'session_attachments'
       AND INDEX_NAME = 'idx_session') = 0,
  'ALTER TABLE `session_attachments`
     ADD KEY `idx_session` (`tenant_id`, `session_id`, `id`)',
  'SELECT ''idx_session already present''');
PREPARE stmt FROM @add_new_session_idx; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 3. 会话附件查询索引（按 session_id 而非 user_id）
SET @add_attachment_session_idx := IF(
  (SELECT COUNT(*) FROM information_schema.STATISTICS
     WHERE TABLE_SCHEMA = DATABASE()
       AND TABLE_NAME = 'chat_attachments'
       AND INDEX_NAME = 'idx_attachment_session') = 0,
  'ALTER TABLE `chat_attachments`
     ADD KEY `idx_attachment_session` (`tenant_id`, `session_id`, `id`)',
  'SELECT ''idx_attachment_session already present''');
PREPARE stmt FROM @add_attachment_session_idx; EXECUTE stmt; DEALLOCATE PREPARE stmt;

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
