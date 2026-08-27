-- 017 · 会话级挂载：用户在**这一个会话里**临时挂上的资源
--
-- 跟 agent_*_bindings 是两回事，别混：
--
--     agent_tool_bindings     agent 定义的一部分，所有使用者相同，改它=改这个 agent
--     session_attachments     只属于这一个会话，别人用同一个 agent 完全不受影响
--
-- 所以共享 agent 的发布门（api/agents.py 的 _share_blockers）查的是**绑定**，
-- 跟这张表无关：别人在自己会话里挂了什么，不影响作者能不能共享他的 agent。
--
-- ## 为什么必须落库，而不是只改内存
--
-- lea-agent 把活着的会话放在内存里，满了就回收最久未活跃的闲置会话
-- （session/manager.go 的 ensureCapacity）。回收不丢对话——历史在 agent_messages
-- 里——但挂上的工具如果只存在于那个内存对象里，重建之后就没了：
--
--     10:00  挂上 stock_lookup        内存里的工具数组多一个
--     12:30  会话闲置被回收            内存对象没了
--     13:00  用户回来发消息            重建：读 agent 配置 + 回放历史
--     13:01  「我没有查库存的工具」     ← 用户什么都没做错，上午还在用
--
-- 而且没有任何提示。所以挂载要落一行，重建时读回来一起装进工具集。
--
-- ## kind 用 VARCHAR 不用 ENUM
--
-- 这是 016 那一轮的教训：SQLAlchemy 的 Enum 读到不认识的值直接抛 LookupError，
-- 于是"加一种新资源"变成"必须先改模型再改库、两边部署还得排序"。VARCHAR 加一种
-- 只是多一个字符串常量，老代码读到不认识的 kind 跳过就行（重建本来就要静默丢弃
-- 无效项，见下）。
--
-- ## 为什么没有外键
--
-- ref_id 是多态的（kind=tool 指 tools.id，kind=kb 指 knowledge_bases.id），一个列
-- 挂不了多个外键。代价是可能出现悬空行：工具被删了、或者被取消共享了，行还在。
--
-- **这不是缺陷，是要求**：重建时必须重新按归属解析一遍，够不着的**静默丢掉**而不是
-- 报错。否则一个无关的删除操作会让用户的老会话直接打不开——那比少一个工具糟得多。
--
-- ## user_id 为什么要存
--
-- 重建时不 join 就能重新校验归属（"这行是不是这个人挂的"），顺带留一条审计线索。
-- 会话本身有归属（verifySessionOwner），但那是另一层，多存一列很便宜。

CREATE TABLE IF NOT EXISTS `session_attachments` (
  `id`         BIGINT       NOT NULL AUTO_INCREMENT,
  `session_id` VARCHAR(64)  NOT NULL,
  `tenant_id`  VARCHAR(50)  NOT NULL,
  `user_id`    VARCHAR(64)  NOT NULL,
  `kind`       VARCHAR(20)  NOT NULL COMMENT 'tool | mcp_server | skill | kb —— 不用 ENUM，见文件头',
  `ref_id`     INT          NOT NULL COMMENT '多态外键：按 kind 指向 tools/mcp_servers/skills/knowledge_bases 的 id',
  `created_at` DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  -- 重复挂载幂等：POST 同一个工具两次不该 500 在唯一索引上，接口层按这个做 upsert
  UNIQUE KEY `uq_session_attachment` (`session_id`, `kind`, `ref_id`),
  -- 重建时的查询形状：WHERE tenant_id=? AND session_id=?
  KEY `idx_session` (`tenant_id`, `session_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
  COMMENT='会话级临时挂载的资源；跟 agent_*_bindings 不同，只影响单个会话';

-- 自检
SELECT
  (SELECT COUNT(*) FROM information_schema.TABLES
     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'session_attachments')      AS table_created,
  (SELECT COUNT(*) FROM information_schema.STATISTICS
     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'session_attachments'
       AND INDEX_NAME = 'uq_session_attachment')                                  AS unique_idx_cols,
  (SELECT DATA_TYPE FROM information_schema.COLUMNS
     WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'session_attachments'
       AND COLUMN_NAME = 'kind')                                                  AS kind_type;
