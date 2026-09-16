-- 025 · 交付物登记表：会话产物里**哪些是给用户的成品**
--
-- ## 这张表回答的唯一问题
--
-- 会话工作区里有几十个文件：中间脚本、调试用的 csv、模型自己 mkdir 出来的临时目录、
-- 以及最后那份 40 页的报告。前三类是过程，第四类是用户要的东西 —— 而**在文件系统层
-- 面它们长得一模一样**。
--
-- 现有代码里唯一的判据是 `in_output = path.startswith("output/")`
-- （lea-tools/services/file_store.py 的 FileEntry.as_dict）。它算出来，传给前端，
-- 然后前端一行都没用 —— 因为它不可信：模型没有任何理由把成品写进 output/，
-- 而它也确实经常不写。
--
-- 于是这张表把判据从"猜"换成"声明"：模型调 deliver_artifact 落一行，那就是成品。
-- 一行都没落 = 这轮没有成品（不是"我们没识别出来"）。
--
-- 这跟 session_plans 是同一条线：save_plan 是 plans 表的**唯一写入者**，
-- deliver_artifact 是这张表的唯一写入者。有一个显式的动作，就不需要一套启发式。
--
-- ## 为什么不复用 chat_attachments
--
-- 那张表的语义是"用户交给我们的东西"，归属是上传者，生命周期跟着上传者。这张是
-- "我们交给用户的东西"，归属是**生成它的那个会话**，而 rel_path 只在会话内有意义。
-- 混一张表的话，`file_key` 空串那套语义（"没落 OBS"）要同时表达两个方向的降级，
-- 而两个方向的降级处理完全不同（上传失败要拒绝，产物落库失败要保留）。
--
-- 两张表在配额上是**加起来算**的，见 027。
--
-- ## obs_key 是内容寻址的，且跟上传件同一个命名空间的兄弟前缀
--
--     library/<tenant_id>/<sha256>.<ext>
--
-- 不是 `sessions/<sid>/<rel_path>`（artifact_pusher.artifact_key 那个形状）。三个理由：
--
--  1. **跨会话导入只要一次 stage()**。用户在新对话里想用上次生成的报告，
--     走的是已经验证过的 /api/files/uploads/stage —— 那个端点只认内容寻址的 key。
--     按 session 分层的 key 得另写一条物化路径。
--  2. **天然去重**。同一份 PPT 交付三次（改了两轮又回到原样）只占一个对象。
--  3. **配额算的是去重后的字节**，跟用户在存储页看到的数字一致。
--
-- 代价是 obs_key 看不出它来自哪个会话 —— 那个信息在这张表的 session_id 列里，
-- 而对象存储的 key 本来就不该承载业务关系。
--
-- ⚠️ obs_key 允许为空串，含义是"**登记了但没存住**"（OBS 不可达、或用户配额满）。
-- 这跟上传路径的处理刻意相反：上传超配额是硬拒（507，用户还拿着原件，重传很便宜），
-- 交付超配额是**软放行**（行落下来、obs_key 留空、给模型一句降级说明）—— 那份产物
-- 背后是几分钟的模型劳动，为了配额把它丢掉换不来任何东西，而 NFS 会话目录里那份
-- 还在（30 天内用户仍能下载）。
--
-- ## 为什么 rel_path 仍然存
--
-- 它是"这份成品在当时那个工作区里的位置"，用于：会话内下载走 NFS 那份（不必回
-- OBS）、以及 UNIQUE KEY 的去重键 —— 同一个会话对同一个路径反复 deliver 是常态
-- （改了三轮才定稿），每轮该覆盖同一行而不是堆三行。
--
-- ## 没有外键
--
-- session_id 指向 chat_sessions.id，project_id 指向 projects.id。都不加 FK：
-- 删项目、归档会话都不该级联删掉用户的成品 —— 用户在存储页看到的是**他自己的文件
-- 库**，一个跟他无关的清理动作让文件消失是最坏的形状（那正是 ChatGPT 刻意避开的：
-- 删对话不删文件）。悬空行由 API 层按归属解析，解析不到就不显示 session 链接。

CREATE TABLE IF NOT EXISTS `session_artifacts` (
  `id`          BIGINT       NOT NULL AUTO_INCREMENT,
  `tenant_id`   VARCHAR(50)  NOT NULL,
  `user_id`     VARCHAR(64)  NOT NULL,
  `session_id`  VARCHAR(64)  NOT NULL,
  `project_id`  INT          NULL COMMENT '生成时会话所属项目；无项目为 NULL。不加 FK，删项目不删成品',

  `rel_path`    VARCHAR(500) NOT NULL COMMENT '会话工作区内的相对路径，如 output/report.pptx',
  `filename`    VARCHAR(255) NOT NULL COMMENT '展示名，默认取 rel_path 的 basename',
  `title`       VARCHAR(200) NOT NULL DEFAULT '' COMMENT '模型声明的成品标题，给人看',
  `description` VARCHAR(500) NOT NULL DEFAULT '' COMMENT '模型声明的一句话说明；空串表示没说',

  `obs_key`     VARCHAR(512) NOT NULL DEFAULT '' COMMENT 'library/<tenant>/<sha256>.<ext>；空串=登记了但没存住，见文件头',
  `sha256`      CHAR(64)     NOT NULL DEFAULT '' COMMENT '内容指纹，同时是 obs_key 的文件名。空串=没算成（没存住时）',
  `size`        BIGINT       NOT NULL DEFAULT 0,
  `mime_type`   VARCHAR(120) NOT NULL DEFAULT '',

  `created_at`  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at`  DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

  PRIMARY KEY (`id`),
  -- 同一会话同一路径反复交付 → upsert 同一行。改了三轮才定稿是常态，不该堆三行。
  UNIQUE KEY `uq_session_artifact` (`session_id`, `rel_path`),
  -- 用户文件库的查询形状：WHERE tenant_id=? AND user_id=? ORDER BY created_at DESC
  KEY `idx_user_artifacts` (`tenant_id`, `user_id`, `created_at`),
  -- 项目视角（"这个项目产出过什么"）。project_id 可为 NULL，MySQL 索引照常能用。
  KEY `idx_project_artifacts` (`tenant_id`, `project_id`, `created_at`),
  -- 配额算的是**去重后**的字节：SUM(size) 会把同一个 sha 重复计入，所以要能按
  -- (tenant, sha) 折叠。见 027 的覆盖索引。
  KEY `idx_artifact_sha` (`tenant_id`, `sha256`),
  -- 会话抽屉列这个会话交付过什么
  KEY `idx_session_artifacts` (`tenant_id`, `session_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='模型显式声明的交付物；唯一写入者是 deliver_artifact 工具';

-- 自检
SELECT
  (SELECT COUNT(*) FROM information_schema.TABLES
     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'session_artifacts')        AS table_created,
  (SELECT COUNT(*) FROM information_schema.STATISTICS
     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'session_artifacts'
       AND INDEX_NAME = 'uq_session_artifact')                                    AS unique_idx_cols,
  (SELECT COUNT(*) FROM information_schema.STATISTICS
     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'session_artifacts'
       AND INDEX_NAME = 'idx_user_artifacts')                                     AS user_idx_cols;
