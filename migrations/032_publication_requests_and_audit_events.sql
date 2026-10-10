-- Generic publication workflow and append-only audit events.

CREATE TABLE IF NOT EXISTS publication_requests (
    id                  BIGINT AUTO_INCREMENT PRIMARY KEY,
    resource_type       VARCHAR(50)  NOT NULL,
    resource_id         VARCHAR(100) NOT NULL,
    resource_version    VARCHAR(100) NOT NULL,
    tenant_id           VARCHAR(64)  NOT NULL,
    source_scope        VARCHAR(20)  NOT NULL,
    target_scope        VARCHAR(20)  NOT NULL,
    status              VARCHAR(20)  NOT NULL DEFAULT 'pending',
    requested_by        VARCHAR(100) NOT NULL,
    request_note        TEXT NULL,
    reviewed_by         VARCHAR(100) NULL,
    review_note         TEXT NULL,
    reviewed_at         DATETIME NULL,
    risk_snapshot       JSON NULL,
    resource_snapshot   JSON NULL,
    pending_key         VARCHAR(160) NULL,
    created_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at          DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    UNIQUE KEY uq_publication_requests_pending_key (pending_key),
    INDEX idx_publication_requests_queue (target_scope, tenant_id, status),
    INDEX idx_publication_requests_resource (resource_type, resource_id),
    INDEX idx_publication_requests_requester (tenant_id, requested_by, status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS audit_events (
    event_id       CHAR(36) PRIMARY KEY,
    tenant_id      VARCHAR(64)  NOT NULL,
    actor_user_id  VARCHAR(100) NOT NULL,
    actor_role     VARCHAR(50)  NOT NULL,
    action         VARCHAR(100) NOT NULL,
    resource_type  VARCHAR(50)  NOT NULL,
    resource_id    VARCHAR(100) NOT NULL,
    request_id     VARCHAR(100) NULL,
    result         VARCHAR(20)  NOT NULL,
    before_summary JSON NULL,
    after_summary  JSON NULL,
    reason         TEXT NULL,
    created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_audit_events_tenant_time (tenant_id, created_at),
    INDEX idx_audit_events_resource (resource_type, resource_id, created_at),
    INDEX idx_audit_events_request (request_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- Preserve the existing Skill approval history. Reusing the legacy id makes this
-- migration idempotent and leaves the auto-increment sequence above migrated rows.
INSERT IGNORE INTO publication_requests (
    id, resource_type, resource_id, resource_version, tenant_id,
    source_scope, target_scope, status, requested_by, request_note,
    reviewed_by, review_note, reviewed_at, risk_snapshot, resource_snapshot,
    pending_key, created_at, updated_at
)
SELECT
    spr.id,
    'skill',
    CAST(spr.skill_id AS CHAR),
    COALESCE(s.version, '1.0.0'),
    spr.tenant_id,
    CASE
        WHEN s.owner_scope = 'user' THEN 'private'
        WHEN s.tenant_id = 'default' THEN 'global'
        ELSE 'tenant'
    END,
    spr.target_scope,
    spr.status,
    spr.requested_by,
    CASE WHEN spr.status = 'pending' THEN spr.review_note ELSE NULL END,
    spr.reviewed_by,
    CASE WHEN spr.status = 'pending' THEN NULL ELSE spr.review_note END,
    spr.reviewed_at,
    JSON_OBJECT('migrated_from', 'skill_publish_requests'),
    JSON_OBJECT(
        'name', s.name,
        'display_name', COALESCE(s.display_name, s.name),
        'description', COALESCE(s.description, ''),
        'category', COALESCE(s.category, 'general'),
        'version', COALESCE(s.version, '1.0.0'),
        'model_tier', COALESCE(s.model_tier, 'standard'),
        'required_tools', COALESCE(s.required_tools, JSON_ARRAY()),
        'enabled', s.enabled,
        'system_prompt_sha256', SHA2(COALESCE(s.system_prompt, ''), 256),
        'bundle_uri_sha256', SHA2(COALESCE(s.bundle_uri, ''), 256)
    ),
    CASE WHEN spr.status = 'pending' THEN CONCAT('skill:', spr.skill_id) ELSE NULL END,
    spr.created_at,
    spr.updated_at
FROM skill_publish_requests spr
JOIN skills s ON s.id = spr.skill_id;
