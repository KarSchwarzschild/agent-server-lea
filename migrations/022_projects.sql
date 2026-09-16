-- 022 · 项目作为会话归属容器
CREATE TABLE IF NOT EXISTS projects (
    id INT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    tenant_id VARCHAR(50) NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    name VARCHAR(120) NOT NULL,
    description VARCHAR(500) NOT NULL DEFAULT '',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    UNIQUE KEY uq_projects_owner_name (tenant_id, user_id, name),
    KEY idx_projects_owner (tenant_id, user_id, updated_at)
-- COLLATE 必须显式写。不写的话 MySQL 8 用服务端默认 utf8mb4_0900_ai_ci，而本库其余
-- 表建得早，是 utf8mb4_unicode_ci —— 于是 ListProjects 里
-- `chat_sessions s ON s.tenant_id = p.tenant_id` 这个 JOIN 直接报
-- "Illegal mix of collations"，/api/projects 永久 500。
-- 症状还会骗人：建表本身成功，单表查询也正常，只有跨表 JOIN 才炸。
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

ALTER TABLE chat_sessions ADD COLUMN project_id INT NULL AFTER tenant_id;
ALTER TABLE chat_sessions ADD KEY idx_chat_sessions_project (tenant_id, user_id, project_id, last_active_at);
