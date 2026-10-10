-- Admin-console governance state: published-resource lifecycle and automatic checks.

CREATE TABLE IF NOT EXISTS resource_publications (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    resource_type   VARCHAR(50)  NOT NULL,
    resource_id     VARCHAR(100) NOT NULL,
    tenant_id       VARCHAR(64)  NOT NULL,
    scope           VARCHAR(20)  NOT NULL,
    status          VARCHAR(20)  NOT NULL DEFAULT 'active',
    published_by    VARCHAR(100) NOT NULL,
    published_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_by      VARCHAR(100) NOT NULL,
    updated_at      DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    UNIQUE KEY uq_resource_publications_resource (resource_type, resource_id),
    INDEX idx_resource_publications_tenant_status (tenant_id, status, updated_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS resource_checks (
    id               BIGINT AUTO_INCREMENT PRIMARY KEY,
    resource_type    VARCHAR(50)  NOT NULL,
    resource_id      VARCHAR(100) NOT NULL,
    resource_version VARCHAR(100) NOT NULL,
    tenant_id        VARCHAR(64)  NOT NULL,
    status           VARCHAR(20)  NOT NULL,
    summary          VARCHAR(500) NOT NULL DEFAULT '',
    details          JSON NULL,
    checked_by       VARCHAR(100) NOT NULL,
    created_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,

    INDEX idx_resource_checks_latest (resource_type, resource_id, created_at),
    INDEX idx_resource_checks_tenant_status (tenant_id, status, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
