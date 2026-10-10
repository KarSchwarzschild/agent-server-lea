"""Generic publication workflow and append-only audit records."""

from __future__ import annotations

import uuid

from sqlalchemy import Column, DateTime, Index, Integer, JSON, String, Text, UniqueConstraint, func

from lea_config.database import Base

PUBLICATION_PENDING = "pending"
PUBLICATION_APPROVED = "approved"
PUBLICATION_REJECTED = "rejected"
PUBLICATION_CANCELED = "canceled"
PUBLICATION_STATUSES = (
    PUBLICATION_PENDING,
    PUBLICATION_APPROVED,
    PUBLICATION_REJECTED,
    PUBLICATION_CANCELED,
)

PUBLICATION_TARGET_TENANT = "tenant"
PUBLICATION_TARGET_GLOBAL = "global"
PUBLICATION_TARGETS = (PUBLICATION_TARGET_TENANT, PUBLICATION_TARGET_GLOBAL)

RESOURCE_PUBLICATION_ACTIVE = "active"
RESOURCE_PUBLICATION_SUSPENDED = "suspended"
RESOURCE_PUBLICATION_ARCHIVED = "archived"
RESOURCE_PUBLICATION_STATUSES = (
    RESOURCE_PUBLICATION_ACTIVE,
    RESOURCE_PUBLICATION_SUSPENDED,
    RESOURCE_PUBLICATION_ARCHIVED,
)

RESOURCE_CHECK_PASSED = "passed"
RESOURCE_CHECK_FAILED = "failed"
RESOURCE_CHECK_STATUSES = (RESOURCE_CHECK_PASSED, RESOURCE_CHECK_FAILED)


class PublicationRequest(Base):
    __tablename__ = "publication_requests"
    __table_args__ = (
        Index("idx_publication_requests_queue", "target_scope", "tenant_id", "status"),
        Index("idx_publication_requests_resource", "resource_type", "resource_id"),
        Index("idx_publication_requests_requester", "tenant_id", "requested_by", "status"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    resource_type = Column(String(50), nullable=False)
    resource_id = Column(String(100), nullable=False)
    resource_version = Column(String(100), nullable=False)
    tenant_id = Column(String(64), nullable=False)
    source_scope = Column(String(20), nullable=False)
    target_scope = Column(String(20), nullable=False)
    status = Column(String(20), nullable=False, default=PUBLICATION_PENDING)

    requested_by = Column(String(100), nullable=False)
    request_note = Column(Text, nullable=True)
    reviewed_by = Column(String(100), nullable=True)
    review_note = Column(Text, nullable=True)
    reviewed_at = Column(DateTime, nullable=True)

    risk_snapshot = Column(JSON, nullable=True)
    resource_snapshot = Column(JSON, nullable=True)

    # NULL after the request leaves pending. The unique constraint gives MySQL
    # the partial-unique behavior it does not support natively.
    pending_key = Column(String(160), nullable=True, unique=True)

    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("idx_audit_events_tenant_time", "tenant_id", "created_at"),
        Index("idx_audit_events_resource", "resource_type", "resource_id", "created_at"),
        Index("idx_audit_events_request", "request_id"),
    )

    event_id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    tenant_id = Column(String(64), nullable=False)
    actor_user_id = Column(String(100), nullable=False)
    actor_role = Column(String(50), nullable=False)
    action = Column(String(100), nullable=False)
    resource_type = Column(String(50), nullable=False)
    resource_id = Column(String(100), nullable=False)
    request_id = Column(String(100), nullable=True)
    result = Column(String(20), nullable=False)
    before_summary = Column(JSON, nullable=True)
    after_summary = Column(JSON, nullable=True)
    reason = Column(Text, nullable=True)
    created_at = Column(DateTime, nullable=False, default=func.now())


class ResourcePublication(Base):
    __tablename__ = "resource_publications"
    __table_args__ = (
        UniqueConstraint(
            "resource_type", "resource_id", name="uq_resource_publications_resource"
        ),
        Index(
            "idx_resource_publications_tenant_status",
            "tenant_id",
            "status",
            "updated_at",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    resource_type = Column(String(50), nullable=False)
    resource_id = Column(String(100), nullable=False)
    tenant_id = Column(String(64), nullable=False)
    scope = Column(String(20), nullable=False)
    status = Column(String(20), nullable=False, default=RESOURCE_PUBLICATION_ACTIVE)
    published_by = Column(String(100), nullable=False)
    published_at = Column(DateTime, nullable=False, default=func.now())
    updated_by = Column(String(100), nullable=False)
    updated_at = Column(DateTime, nullable=False, default=func.now(), onupdate=func.now())


class ResourceCheck(Base):
    __tablename__ = "resource_checks"
    __table_args__ = (
        Index(
            "idx_resource_checks_latest",
            "resource_type",
            "resource_id",
            "created_at",
        ),
        Index(
            "idx_resource_checks_tenant_status",
            "tenant_id",
            "status",
            "created_at",
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    resource_type = Column(String(50), nullable=False)
    resource_id = Column(String(100), nullable=False)
    resource_version = Column(String(100), nullable=False)
    tenant_id = Column(String(64), nullable=False)
    status = Column(String(20), nullable=False)
    summary = Column(String(500), nullable=False, default="")
    details = Column(JSON, nullable=True)
    checked_by = Column(String(100), nullable=False)
    created_at = Column(DateTime, nullable=False, default=func.now())
