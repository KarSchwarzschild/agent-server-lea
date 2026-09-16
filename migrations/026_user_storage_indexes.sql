-- 026 · 用户文件库 + 存储配额：给 chat_attachments 补两条查询形状
--
-- 025 建了 session_artifacts（自带索引）。这一条补的是**另一半** —— 用户上传件。
-- 用户在存储页看到的一个数字和一个列表，各自跨这两张表：
--
--     GET /api/user/storage    已用字节 = 上传件 + 交付物，都去重后
--     GET /api/user/files      一个按时间倒排的合并列表
--
-- chat_attachments 是老 lea-server 用 SQLAlchemy create_all() 建的（仓里没有建表
-- SQL），现有索引只有主键和 019 加的 idx_attachment_sha(sha256)。而这两条查询的
-- WHERE 都是 (tenant_id, user_id) —— 也就是**全表扫**，随附件总量线性变慢。
--
-- ## 为什么配额要 SUM 而不是维护一个计数器
--
-- 跟 file_stager.uploads_bytes 同一个理由，而且这里更强：计数器在多副本下要处理
-- 并发递增、删除对账、以及"进程挂在递增和落盘之间"的漂移。漂移的表现是**配额
-- 莫名其妙变紧**，用户明明没传东西却被 507 挡住，而唯一的修法是手动对账。
--
-- SUM 永远等于真相。代价是每次读存储页多一次聚合查询 —— 有了下面这条索引它是
-- 索引内的顺序扫，而且这个页面是低频的（用户偶尔看一次），不在对话热路径上。
--
-- ## 为什么要 DISTINCT sha256
--
-- OBS 那一层是内容寻址的（同 key 同内容，put_blob 里 head 一下就跳过），所以
-- 一个班 40 个学生传同一份课件在对象存储上**只占一个对象**。裸 SUM(file_size)
-- 会把它算 40 遍 —— 用户看到的数字比真实占用大一个数量级，然后在明明没满的时候
-- 被挡住。
--
-- 折叠是**按 (tenant, sha) 折**，不是全局：upload_key 带 tenant 前缀，跨租户不共享
-- 对象（见 file_stager.upload_key 的注释）。所以同一个 sha 在两个租户里是两个对象，
-- 各算一次才对。
--
-- 索引把 sha256 和 file_size 都收进来，于是去重求和是**覆盖索引扫**，不回表。
-- 唯一要回表的是 `file_key <> ''` 那个过滤（没存住的行不占 OBS 空间，不该计入）——
-- 没把 file_key 放进索引是因为它 VARCHAR(500)，收进来索引会胖到得不偿失，而回表
-- 只发生在这个用户自己的那几十行上。

-- 用户文件库列表：WHERE tenant_id=? AND user_id=? ORDER BY created_at DESC
SET @add_user_idx := IF(
  (SELECT COUNT(*) FROM information_schema.STATISTICS
     WHERE TABLE_SCHEMA = DATABASE()
       AND TABLE_NAME = 'chat_attachments'
       AND INDEX_NAME = 'idx_attachment_user') = 0,
  'ALTER TABLE `chat_attachments`
     ADD KEY `idx_attachment_user` (`tenant_id`, `user_id`, `created_at`)',
  'SELECT ''idx_attachment_user already present''');
PREPARE stmt FROM @add_user_idx; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 配额统计：SELECT SUM(size) FROM (SELECT DISTINCT sha256, file_size ...) —— 覆盖索引。
-- 租户级配额用同一条索引的最左前缀 (tenant_id)。
SET @add_quota_idx := IF(
  (SELECT COUNT(*) FROM information_schema.STATISTICS
     WHERE TABLE_SCHEMA = DATABASE()
       AND TABLE_NAME = 'chat_attachments'
       AND INDEX_NAME = 'idx_attachment_quota') = 0,
  'ALTER TABLE `chat_attachments`
     ADD KEY `idx_attachment_quota` (`tenant_id`, `user_id`, `sha256`, `file_size`)',
  'SELECT ''idx_attachment_quota already present''');
PREPARE stmt FROM @add_quota_idx; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- session_artifacts 那一侧的配额索引：025 建表时给了 idx_artifact_sha
-- (tenant_id, sha256)，但求和还要 size 和 user_id。补一条覆盖的。
-- 建表语句里没直接写，是为了让"配额这件事需要哪些索引"集中在这一个文件里 ——
-- 以后配额口径变了（比如改成按项目算），要改的索引都在这儿。
SET @add_art_quota_idx := IF(
  (SELECT COUNT(*) FROM information_schema.STATISTICS
     WHERE TABLE_SCHEMA = DATABASE()
       AND TABLE_NAME = 'session_artifacts'
       AND INDEX_NAME = 'idx_artifact_quota') = 0,
  'ALTER TABLE `session_artifacts`
     ADD KEY `idx_artifact_quota` (`tenant_id`, `user_id`, `sha256`, `size`)',
  'SELECT ''idx_artifact_quota already present''');
PREPARE stmt FROM @add_art_quota_idx; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 自检
SELECT
  (SELECT COUNT(*) FROM information_schema.STATISTICS
     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'chat_attachments'
       AND INDEX_NAME = 'idx_attachment_user')       AS has_user_index,
  (SELECT COUNT(*) FROM information_schema.STATISTICS
     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'chat_attachments'
       AND INDEX_NAME = 'idx_attachment_quota')      AS attachment_quota_cols,
  (SELECT COUNT(*) FROM information_schema.STATISTICS
     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'session_artifacts'
       AND INDEX_NAME = 'idx_artifact_quota')        AS artifact_quota_cols;
