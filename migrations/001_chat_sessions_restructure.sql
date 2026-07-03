-- Migration: 重构 chat_sessions 表结构
-- 日期: 2026-06-08
-- 说明: 统一会话管理到平台层，新增 message_count/is_archived/tenant_id/last_active_at 字段
--       id 从 auto-increment int 改为 UUID varchar(64)
--       user_id 从 int FK 改为 varchar(64)（对齐 user.user_id）
--
-- 注意：此迁移有破坏性变更（id 类型变化），执行前请备份数据。
-- 执行方式: mysql -u root -p lifelong-edu-agent-prod < migrations/001_chat_sessions_restructure.sql

-- 1. 备份旧表
CREATE TABLE IF NOT EXISTS chat_sessions_backup AS SELECT * FROM chat_sessions;
CREATE TABLE IF NOT EXISTS chat_messages_backup AS SELECT * FROM chat_messages;

-- 2. 删除外键约束（如果存在）
SET @fk_exists = (
    SELECT COUNT(*) FROM information_schema.TABLE_CONSTRAINTS
    WHERE CONSTRAINT_TYPE = 'FOREIGN KEY'
    AND TABLE_NAME = 'chat_messages'
    AND TABLE_SCHEMA = DATABASE()
);
-- 手动执行: ALTER TABLE chat_messages DROP FOREIGN KEY <fk_name>; (如有)

-- 3. 删除旧表
DROP TABLE IF EXISTS chat_messages;
DROP TABLE IF EXISTS chat_sessions;

-- 4. 创建新表结构
CREATE TABLE chat_sessions (
    id VARCHAR(64) NOT NULL PRIMARY KEY,
    user_id VARCHAR(64) NOT NULL,
    title VARCHAR(200) DEFAULT '新对话',
    app_name VARCHAR(50) DEFAULT 'chat',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    last_active_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    message_count INT DEFAULT 0,
    is_archived TINYINT(1) DEFAULT 0,
    tenant_id VARCHAR(50) NOT NULL DEFAULT 'default',
    INDEX idx_user_id (user_id),
    INDEX idx_tenant_id (tenant_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE chat_messages (
    id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    session_id VARCHAR(64) NOT NULL,
    role VARCHAR(20),
    content TEXT,
    token_count INT,
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_session_id (session_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 5. 数据迁移（将旧数据写入新表）
-- 注意：旧表 user_id 是 int，新表是 varchar，需要关联 users 表取 user_id 字段
-- INSERT INTO chat_sessions (id, user_id, title, app_name, created_at, last_active_at)
-- SELECT id, (SELECT user_id FROM users WHERE users.id = b.user_id), title, app_name, created_at, updated_at
-- FROM chat_sessions_backup b;
--
-- 如不需要保留旧对话数据，可跳过此步。
