-- 027 · 项目归档状态
ALTER TABLE projects
  ADD COLUMN is_archived TINYINT(1) NOT NULL DEFAULT 0 AFTER description;

ALTER TABLE projects
  ADD KEY idx_projects_active_owner (tenant_id, user_id, is_archived, updated_at);
