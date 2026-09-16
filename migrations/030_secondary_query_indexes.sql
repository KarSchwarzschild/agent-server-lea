-- 030 · 次要查询索引：中频路径优化
--
-- 这两个索引优化的是中频路径（不在每条消息的热路径上），可以在 Beta 阶段部署。
--
-- ## 场景
--
-- 1. **chat_attachments.GetAttachmentByStagedPath**（Read 工具读 /mnt/uploads）
--    查询：WHERE tenant_id=? AND session_id=? AND staged_path=? ORDER BY id DESC LIMIT 1
--    现有：无匹配索引
--    影响：Read 工具读用户上传的文件时反查附件记录（提取文本、判断权限），虽然
--          不高频（用户明确调 Read 才触发），但 staged_path 是 VARCHAR(500) 的等值查询，
--          全表扫会很慢。
--
-- 2. **session_artifacts.ListSessionArtifacts**（会话成品列表）
--    查询：WHERE tenant_id=? AND session_id=? ORDER BY created_at DESC, id DESC
--    现有：idx_session_artifacts(tenant_id, session_id)
--    问题：缺排序字段导致 filesort（成品通常 < 10 个，但用户频繁刷新"文件"页时触发）
--    影响：前端轮询查成品列表时多一次 filesort

-- 4. staged_path 反查索引（Read 工具读 /mnt/uploads）
SET @add_attachment_staged_idx := IF(
  (SELECT COUNT(*) FROM information_schema.STATISTICS
     WHERE TABLE_SCHEMA = DATABASE()
       AND TABLE_NAME = 'chat_attachments'
       AND INDEX_NAME = 'idx_attachment_staged') = 0,
  'ALTER TABLE `chat_attachments`
     ADD KEY `idx_attachment_staged` (`tenant_id`, `session_id`, `staged_path`(255))',
  'SELECT ''idx_attachment_staged already present''');
PREPARE stmt FROM @add_attachment_staged_idx; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 5. 会话成品列表索引（扩展加排序字段）
SET @drop_old_artifact_idx := IF(
  (SELECT COUNT(*) FROM information_schema.STATISTICS
     WHERE TABLE_SCHEMA = DATABASE()
       AND TABLE_NAME = 'session_artifacts'
       AND INDEX_NAME = 'idx_session_artifacts') > 0,
  'ALTER TABLE `session_artifacts` DROP KEY `idx_session_artifacts`',
  'SELECT ''idx_session_artifacts not present, skip drop''');
PREPARE stmt FROM @drop_old_artifact_idx; EXECUTE stmt; DEALLOCATE PREPARE stmt;

SET @add_new_artifact_idx := IF(
  (SELECT COUNT(*) FROM information_schema.STATISTICS
     WHERE TABLE_SCHEMA = DATABASE()
       AND TABLE_NAME = 'session_artifacts'
       AND INDEX_NAME = 'idx_session_artifacts') = 0,
  'ALTER TABLE `session_artifacts`
     ADD KEY `idx_session_artifacts` (`tenant_id`, `session_id`, `created_at`, `id`)',
  'SELECT ''idx_session_artifacts already present''');
PREPARE stmt FROM @add_new_artifact_idx; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 自检
SELECT
  (SELECT COUNT(*) FROM information_schema.STATISTICS
     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'chat_attachments'
       AND INDEX_NAME = 'idx_attachment_staged')     AS attachment_staged_cols,
  (SELECT COUNT(*) FROM information_schema.STATISTICS
     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'session_artifacts'
       AND INDEX_NAME = 'idx_session_artifacts')     AS artifact_sort_cols;
