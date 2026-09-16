-- 019 · 用户上传的文件落对象存储：chat_attachments 补 sha256 + staged_path
--
-- ## 在补什么
--
-- 这张表原来是"只存抽出来的文本"：`file_key` 一直写空串（router/attachment.go 的
-- 注释写着 "H5 版本不上 OBS"），于是**上传的字节流在 HTTP 响应返回后就被丢了**。
--
-- 后果不是"少个下载功能"，是模型**结构上无法处理文件本身**：
--
--     用户上传 课件.pptx，说"帮我改第 3 页"
--     → 模型只拿到抽出来的 markdown
--     → 沙箱里 ls 不到这个文件，Read 也读不到
--     → 它只能照着文本**重写一份**，母版/主题/其余页全丢
--
-- 补完之后一份文件有三个消费口，各自独立：
--
--     extracted_text   注入进对话（问答、总结）
--     staged_path      NFS 上的会话副本，RO 挂进沙箱 /mnt/uploads（python 处理）
--     file_key         OBS 上的长期真源（下载、会话重建时重新物化）
--
-- ## 为什么 file_key 里带 sha256，还要单独存一列
--
-- key 形如 `uploads/<tenant>/<sha256>.<ext>` —— sha 已经在里面了，这一列确实冗余。
--
-- 留着的理由是**不让别处去解析那个字符串**：以后 key 加日期分层、换算法、或者
-- 迁移 bucket 布局，`rsplit('.', 1)` 那类代码会静默解出错的值，而"错的 sha"的表现是
-- 缓存命中到别的文件的解析结果 —— 静默且难查。一个独立列 64 字节换掉这个耦合。
--
-- 而 sha 要能被查，是因为它同时是**解析结果的缓存键**（parse(bytes) 是纯函数，
-- 见 lea-tools 的 parsed:<sha>）和**去重键**：同一个班 40 个学生传同一份课件，
-- OBS 上只有一个对象。
--
-- ## staged_path 为什么要存，不现算
--
-- 它是 `<sha8>-<原始文件名>`，看起来能从另外两列拼出来。不存的问题是**拼的人得知道
-- 清洗规则**（原名里的 `/`、控制字符、超长名字都要处理），而那规则住在 lea-tools 的
-- `safe_stage_name`。两处各实现一遍的话，注入给模型的路径和磁盘上真实的文件名会在
-- 边缘情况下不一致 —— 模型拿着一个不存在的路径去 open()，报错信息还指向"文件不存在"，
-- 排查会绕远。存下来就只有一个真相。
--
-- 空串 = 没物化（沙箱没开、或者物化失败）。注入时据此告诉模型"只能问不能改"，
-- 而不是给一个打不开的路径。
--
-- ## 为什么是 ALTER 而不是 CREATE
--
-- chat_attachments 是老 lea-server 用 SQLAlchemy create_all() 建的（见
-- lea_server/models/attachment.py），仓里没有它的建表 SQL。所以这里只加列。
-- 每条 ALTER 前面查一次 information_schema：MySQL 的 ADD COLUMN 没有 IF NOT EXISTS，
-- 而这个脚本必须能重复跑（部署脚本会无脑按序执行全部 migrations）。

-- sha256：内容指纹。file_key / 解析缓存 / 去重都用它。
SET @add_sha := IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS
     WHERE TABLE_SCHEMA = DATABASE()
       AND TABLE_NAME = 'chat_attachments'
       AND COLUMN_NAME = 'sha256') = 0,
  'ALTER TABLE `chat_attachments`
     ADD COLUMN `sha256` VARCHAR(64) NOT NULL DEFAULT ''''
     COMMENT ''内容 sha256（十六进制）；同时是 file_key 里的文件名和解析缓存键''',
  'SELECT ''sha256 already present''');
PREPARE stmt FROM @add_sha; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- staged_path：NFS 会话目录 uploads/ 下的文件名（不是完整路径，租户和会话由调用方拼）。
SET @add_staged := IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS
     WHERE TABLE_SCHEMA = DATABASE()
       AND TABLE_NAME = 'chat_attachments'
       AND COLUMN_NAME = 'staged_path') = 0,
  'ALTER TABLE `chat_attachments`
     ADD COLUMN `staged_path` VARCHAR(300) NOT NULL DEFAULT ''''
     COMMENT ''会话 uploads/ 目录下的文件名；空串=没物化，模型只能问不能改''',
  'SELECT ''staged_path already present''');
PREPARE stmt FROM @add_staged; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- file_key 原来是 NOT NULL 无默认值，而现在"没上传成功"是一种正常状态（OBS 不可达时
-- 仍然要让附件能用文本路径工作）。给个默认值，免得插入时必须显式传空串。
SET @fix_key := IF(
  (SELECT COUNT(*) FROM information_schema.COLUMNS
     WHERE TABLE_SCHEMA = DATABASE()
       AND TABLE_NAME = 'chat_attachments'
       AND COLUMN_NAME = 'file_key'
       AND COLUMN_DEFAULT IS NULL) = 1,
  'ALTER TABLE `chat_attachments`
     MODIFY COLUMN `file_key` VARCHAR(500) NOT NULL DEFAULT ''''
     COMMENT ''OBS 对象 key（uploads/<tenant>/<sha>.<ext>）；空串=没落对象存储''',
  'SELECT ''file_key default already set''');
PREPARE stmt FROM @fix_key; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 按 sha 查"这份内容传过没有"。不做唯一索引：同一份文件被不同人、不同会话上传是
-- 正常的，每次都该有自己的一行（各自的 session_id / created_at / 审计线索）。
-- 去重发生在 OBS 那一层（同 key 覆盖），不在这张表。
SET @add_idx := IF(
  (SELECT COUNT(*) FROM information_schema.STATISTICS
     WHERE TABLE_SCHEMA = DATABASE()
       AND TABLE_NAME = 'chat_attachments'
       AND INDEX_NAME = 'idx_attachment_sha') = 0,
  'ALTER TABLE `chat_attachments` ADD KEY `idx_attachment_sha` (`sha256`)',
  'SELECT ''idx_attachment_sha already present''');
PREPARE stmt FROM @add_idx; EXECUTE stmt; DEALLOCATE PREPARE stmt;

-- 自检
SELECT
  (SELECT COUNT(*) FROM information_schema.COLUMNS
     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'chat_attachments'
       AND COLUMN_NAME = 'sha256')                        AS has_sha256,
  (SELECT COUNT(*) FROM information_schema.COLUMNS
     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'chat_attachments'
       AND COLUMN_NAME = 'staged_path')                   AS has_staged_path,
  (SELECT COUNT(*) FROM information_schema.STATISTICS
     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'chat_attachments'
       AND INDEX_NAME = 'idx_attachment_sha')             AS has_sha_index;
