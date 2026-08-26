-- Skill 版本发布表：一个 Skill 多个不可变版本，类似 GitHub Release。
-- 执行前请确认 014 已完成。

CREATE TABLE IF NOT EXISTS skill_versions (
    id           INT AUTO_INCREMENT PRIMARY KEY,
    skill_id     INT NOT NULL,
    version_num  VARCHAR(50) NOT NULL,
    snapshot     JSON NOT NULL,
    change_log   TEXT NOT NULL,
    bundle_uri   VARCHAR(500) NULL,
    sha256       CHAR(64) NULL,
    size_bytes   BIGINT NOT NULL DEFAULT 0,
    created_by   VARCHAR(100) NULL,
    created_at   DATETIME DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_skill_versions_skill FOREIGN KEY (skill_id)
        REFERENCES skills(id) ON DELETE CASCADE,
    CONSTRAINT uq_skill_versions_skill_version UNIQUE (skill_id, version_num),
    INDEX idx_skill_versions_skill (skill_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO skill_versions
    (skill_id, version_num, snapshot, change_log, bundle_uri, size_bytes, created_by)
SELECT
    s.id,
    COALESCE(NULLIF(s.version, ''), '1.0.0'),
    JSON_OBJECT(
        'name', s.name,
        'display_name', COALESCE(s.display_name, s.name),
        'description', COALESCE(s.description, ''),
        'category', COALESCE(s.category, 'general'),
        'version', COALESCE(NULLIF(s.version, ''), '1.0.0'),
        'model_tier', COALESCE(s.model_tier, 'standard'),
        'required_tools', COALESCE(s.required_tools, JSON_ARRAY()),
        'system_prompt', COALESCE(s.system_prompt, ''),
        'enabled', s.enabled,
        'bundle_uri', s.bundle_uri
    ),
    '', s.bundle_uri, 0, s.created_by
FROM skills s
WHERE NOT EXISTS (
    SELECT 1 FROM skill_versions v WHERE v.skill_id = s.id
);
