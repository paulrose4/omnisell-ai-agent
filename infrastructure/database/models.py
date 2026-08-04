from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from .session import Base


class TenantRow(Base):
    __tablename__ = "tenants"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    amazon_writes_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class UserRow(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    oidc_subject: Mapped[str | None] = mapped_column(String(255), unique=True)


class MembershipRow(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("tenant_id", "user_id", name="uq_membership_tenant_user"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(40), nullable=False)


class ChannelConnectionRow(Base):
    __tablename__ = "channel_connections"
    __table_args__ = (
        UniqueConstraint("tenant_id", "provider", "selling_partner_id", "region", name="uq_channel_seller_region"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    selling_partner_id: Mapped[str] = mapped_column(String(100), nullable=False)
    region: Mapped[str] = mapped_column(String(20), nullable=False)
    marketplace_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    secret_ref: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ProductRow(Base):
    __tablename__ = "products"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(500), nullable=False)
    brand: Mapped[str | None] = mapped_column(String(255))
    version: Mapped[int] = mapped_column(Integer, default=1)


class VariantRow(Base):
    __tablename__ = "variants"
    __table_args__ = (UniqueConstraint("tenant_id", "sku", name="uq_variant_tenant_sku"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    product_id: Mapped[str] = mapped_column(ForeignKey("products.id", ondelete="CASCADE"), index=True)
    sku: Mapped[str] = mapped_column(String(100), nullable=False)
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    barcode: Mapped[str | None] = mapped_column(String(100))
    cost_amount: Mapped[Decimal | None] = mapped_column(Numeric(19, 4))
    cost_currency: Mapped[str | None] = mapped_column(String(3))


class ListingRow(Base):
    __tablename__ = "listings"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "channel_connection_id", "marketplace_id", "seller_sku", name="uq_listing_channel_sku"
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    channel_connection_id: Mapped[str] = mapped_column(
        ForeignKey("channel_connections.id", ondelete="CASCADE"), index=True
    )
    variant_id: Mapped[str] = mapped_column(ForeignKey("variants.id", ondelete="CASCADE"), index=True)
    marketplace_id: Mapped[str] = mapped_column(String(50), nullable=False)
    seller_sku: Mapped[str] = mapped_column(String(100), nullable=False)
    external_product_id: Mapped[str | None] = mapped_column(String(100))
    title: Mapped[str | None] = mapped_column(String(1000))
    price_amount: Mapped[Decimal | None] = mapped_column(Numeric(19, 4))
    price_currency: Mapped[str | None] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(50), default="draft")
    content_version: Mapped[int] = mapped_column(Integer, default=1)
    platform_extensions: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class InventoryPositionRow(Base):
    __tablename__ = "inventory_positions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "channel_connection_id",
            "marketplace_id",
            "seller_sku",
            "fulfillment_mode",
            "warehouse_code",
            name="uq_inventory_position",
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    channel_connection_id: Mapped[str] = mapped_column(
        ForeignKey("channel_connections.id", ondelete="CASCADE"), index=True
    )
    seller_sku: Mapped[str] = mapped_column(String(100), nullable=False)
    marketplace_id: Mapped[str] = mapped_column(String(50), nullable=False)
    fulfillment_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    available: Mapped[int] = mapped_column(Integer, default=0)
    reserved: Mapped[int] = mapped_column(Integer, default=0)
    inbound: Mapped[int] = mapped_column(Integer, default=0)
    unsellable: Mapped[int] = mapped_column(Integer, default=0)
    warehouse_code: Mapped[str | None] = mapped_column(String(100))
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    platform_extensions: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class OrderRow(Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("tenant_id", "channel_connection_id", "external_order_id", name="uq_order_external"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    channel_connection_id: Mapped[str] = mapped_column(
        ForeignKey("channel_connections.id", ondelete="CASCADE"), index=True
    )
    external_order_id: Mapped[str] = mapped_column(String(100), nullable=False)
    marketplace_id: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    fulfillment_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    platform_status: Mapped[str | None] = mapped_column(String(100))
    purchased_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    raw_object_key: Mapped[str | None] = mapped_column(String(1000))
    version: Mapped[int] = mapped_column(Integer, default=1)
    platform_extensions: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class OrderLineRow(Base):
    __tablename__ = "order_lines"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id", ondelete="CASCADE"), index=True)
    external_line_id: Mapped[str | None] = mapped_column(String(100))
    seller_sku: Mapped[str] = mapped_column(String(100), nullable=False)
    title: Mapped[str | None] = mapped_column(String(1000))
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price_amount: Mapped[Decimal | None] = mapped_column(Numeric(19, 4))
    unit_price_currency: Mapped[str | None] = mapped_column(String(3))


class SyncCursorRow(Base):
    __tablename__ = "sync_cursors"
    __table_args__ = (
        UniqueConstraint("tenant_id", "channel_connection_id", "resource_type", name="uq_sync_cursor_resource"),
        Index("ix_sync_cursor_tenant_status", "tenant_id", "status"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    channel_connection_id: Mapped[str] = mapped_column(
        ForeignKey("channel_connections.id", ondelete="CASCADE"), index=True
    )
    resource_type: Mapped[str] = mapped_column(String(40), nullable=False)
    cursor: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), default="never_run", nullable=False)
    item_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failure_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    last_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_succeeded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_failed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class DurableTaskRow(Base):
    __tablename__ = "durable_tasks"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_task_tenant_idempotency"),
        Index("ix_task_tenant_status_created", "tenant_id", "status", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    queue_name: Mapped[str] = mapped_column(String(50), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    replayed_from_task_id: Mapped[str | None] = mapped_column(String(36))
    replayed_to_task_id: Mapped[str | None] = mapped_column(String(36))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_by: Mapped[str | None] = mapped_column(String(255))
    close_reason: Mapped[str | None] = mapped_column(Text)


class AgentRunRow(Base):
    __tablename__ = "agent_runs"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "workflow_type", "idempotency_key", name="uq_agent_run_tenant_workflow_idempotency"
        ),
        Index("ix_agent_runs_tenant_workflow_created", "tenant_id", "workflow_type", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    workflow_type: Mapped[str] = mapped_column(String(100), nullable=False)
    agent_type: Mapped[str | None] = mapped_column(String(100))
    schema_version: Mapped[str | None] = mapped_column(String(50))
    correlation_id: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    idempotency_key: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(100), nullable=False)
    model: Mapped[str | None] = mapped_column(String(255))
    token_usage: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost: Mapped[Decimal] = mapped_column(Numeric(19, 6), default=Decimal("0"))
    model_usage: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    evidence_manifest: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    analysis_result: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error_category: Mapped[str | None] = mapped_column(String(100))
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    state: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ApprovalRequestRow(Base):
    __tablename__ = "approval_requests"
    __table_args__ = (
        CheckConstraint(
            "policy_decision IN ('allow', 'require_approval')",
            name="ck_approval_requests_policy_decision",
        ),
        CheckConstraint(
            "risk_level IN ('low', 'medium', 'high', 'critical')",
            name="ck_approval_requests_risk_level",
        ),
        CheckConstraint(
            "(policy_decision = 'allow' AND risk_level = 'low' AND status = 'auto_approved' "
            "AND required_approval_count = 0) "
            "OR (policy_decision = 'require_approval' "
            "AND ((risk_level = 'critical' AND required_approval_count = 2) "
            "OR (risk_level <> 'critical' AND required_approval_count = 1)) "
            "AND status IN "
            "('pending', 'awaiting_second_approval', 'approved', 'rejected', 'expired'))",
            name="ck_approval_requests_policy_status",
        ),
        CheckConstraint(
            "length(trim(policy_version)) > 0",
            name="ck_approval_requests_policy_version",
        ),
        CheckConstraint(
            "length(trim(policy_reason)) > 0",
            name="ck_approval_requests_policy_reason",
        ),
        CheckConstraint(
            "required_approval_count IN (0, 1, 2)",
            name="ck_approval_requests_required_count",
        ),
        CheckConstraint(
            "execution_contract_version IS NULL OR length(trim(execution_contract_version)) > 0",
            name="ck_approval_requests_execution_contract_version",
        ),
        Index("ix_approval_requests_tenant_status_created", "tenant_id", "status", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    action_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_decision: Mapped[str] = mapped_column(String(30), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_reason: Mapped[str] = mapped_column(Text, nullable=False)
    required_approval_count: Mapped[int] = mapped_column(Integer, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False)
    before_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    proposed_changes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    execution_contract_version: Mapped[str | None] = mapped_column(String(64))
    execution_plan: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(30), default="pending")
    approver_id: Mapped[str | None] = mapped_column(String(255))
    first_approver_id: Mapped[str | None] = mapped_column(String(255))
    first_approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    first_approval_reason: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_reason: Mapped[str | None] = mapped_column(Text)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ApprovalDecisionRow(Base):
    __tablename__ = "approval_decisions"
    __table_args__ = (
        UniqueConstraint("approval_request_id", "sequence", name="uq_approval_decision_request_sequence"),
        Index("ix_approval_decisions_tenant_request_created", "tenant_id", "approval_request_id", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    approval_request_id: Mapped[str] = mapped_column(ForeignKey("approval_requests.id", ondelete="CASCADE"), index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    stage: Mapped[str] = mapped_column(String(30), nullable=False)
    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AuditEventRow(Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_tenant_created", "tenant_id", "created_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), index=True, nullable=False)
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    actor: Mapped[str] = mapped_column(String(255), nullable=False)
    action: Mapped[str] = mapped_column(String(150), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InboxEventRow(Base):
    __tablename__ = "inbox_events"
    __table_args__ = (UniqueConstraint("tenant_id", "provider", "external_event_id", name="uq_inbox_external_event"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    provider: Mapped[str] = mapped_column(String(40), nullable=False)
    external_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(150), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(30), default="received", nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)


class OutboxEventRow(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "dedupe_key", name="uq_outbox_tenant_dedupe"),
        Index("ix_outbox_dispatch", "tenant_id", "status", "next_retry_at", "created_at"),
        Index("ix_outbox_unpublished", "published_at", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    aggregate_type: Mapped[str] = mapped_column(String(100), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(100), nullable=False)
    event_type: Mapped[str] = mapped_column(String(150), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(255), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)


class CustomerCaseRow(Base):
    __tablename__ = "customer_cases"
    __table_args__ = (
        UniqueConstraint("tenant_id", "source", "external_case_id", name="uq_customer_case_external"),
        Index("ix_customer_case_queue", "tenant_id", "status", "priority_score", "created_at"),
        Index("ix_customer_case_order", "tenant_id", "external_order_id"),
        Index("ix_customer_case_sku", "tenant_id", "marketplace_id", "seller_sku"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    external_case_id: Mapped[str] = mapped_column(String(255), nullable=False)
    marketplace_id: Mapped[str] = mapped_column(String(50), nullable=False)
    external_order_id: Mapped[str | None] = mapped_column(String(100))
    seller_sku: Mapped[str | None] = mapped_column(String(100))
    asin: Mapped[str | None] = mapped_column(String(100))
    language: Mapped[str] = mapped_column(String(16), nullable=False)
    intent: Mapped[str] = mapped_column(String(100), nullable=False)
    sentiment: Mapped[str] = mapped_column(String(30), nullable=False)
    urgency: Mapped[str] = mapped_column(String(30), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False)
    priority_score: Mapped[Decimal] = mapped_column(Numeric(19, 6), nullable=False)
    customer_message: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    assignee_id: Mapped[str | None] = mapped_column(String(255))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    draft_reply: Mapped[str | None] = mapped_column(Text)
    approved_reply: Mapped[str | None] = mapped_column(Text)
    actual_reply: Mapped[str | None] = mapped_column(Text)
    confidence: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    missing_data: Mapped[list[str]] = mapped_column(JSON, default=list)
    policy_checks: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    playbook_key: Mapped[str | None] = mapped_column(String(100))
    playbook_version: Mapped[str | None] = mapped_column(String(40))
    approval_request_id: Mapped[str | None] = mapped_column(String(36), index=True)
    resolution_code: Mapped[str | None] = mapped_column(String(100))
    root_cause_code: Mapped[str | None] = mapped_column(String(100))
    source: Mapped[str] = mapped_column(String(50), nullable=False)
    verified_amazon_data: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CustomerCaseEventRow(Base):
    __tablename__ = "customer_case_events"
    __table_args__ = (Index("ix_customer_case_event_timeline", "tenant_id", "case_id", "created_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("customer_cases.id", ondelete="CASCADE"), index=True)
    event_type: Mapped[str] = mapped_column(String(100), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CustomerCaseClusterRow(Base):
    __tablename__ = "customer_case_clusters"
    __table_args__ = (
        UniqueConstraint("tenant_id", "dedupe_key", name="uq_case_cluster_tenant_dedupe"),
        Index("ix_case_cluster_queue", "tenant_id", "status", "severity", "updated_at"),
        Index("ix_case_cluster_scope", "tenant_id", "marketplace_id", "seller_sku", "root_cause_code"),
        Index("ix_case_cluster_work_item", "tenant_id", "work_item_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    marketplace_id: Mapped[str] = mapped_column(String(50), nullable=False)
    seller_sku: Mapped[str] = mapped_column(String(100), nullable=False)
    root_cause_code: Mapped[str] = mapped_column(String(100), nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    minimum_samples: Mapped[int] = mapped_column(Integer, nullable=False)
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="collecting")
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    domain: Mapped[str] = mapped_column(String(50), nullable=False)
    team: Mapped[str] = mapped_column(String(50), nullable=False)
    operational_issue_id: Mapped[str | None] = mapped_column(ForeignKey("operational_issues.id", ondelete="SET NULL"))
    recommended_action_id: Mapped[str | None] = mapped_column(ForeignKey("recommended_actions.id", ondelete="SET NULL"))
    work_item_id: Mapped[str | None] = mapped_column(ForeignKey("operational_work_items.id", ondelete="SET NULL"))
    evidence_manifest: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    verified_amazon_data: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    dedupe_key: Mapped[str] = mapped_column(String(64), nullable=False)
    actionable_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CustomerCaseClusterMemberRow(Base):
    __tablename__ = "customer_case_cluster_members"
    __table_args__ = (
        UniqueConstraint("tenant_id", "case_id", name="uq_case_cluster_member_case"),
        Index("ix_case_cluster_member_cluster", "tenant_id", "cluster_id", "linked_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    cluster_id: Mapped[str] = mapped_column(ForeignKey("customer_case_clusters.id", ondelete="CASCADE"), nullable=False)
    case_id: Mapped[str] = mapped_column(ForeignKey("customer_cases.id", ondelete="CASCADE"), nullable=False)
    linked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SOPPlaybookRow(Base):
    __tablename__ = "sop_playbooks"
    __table_args__ = (
        UniqueConstraint("tenant_id", "playbook_key", "version", name="uq_sop_playbook_version"),
        Index("ix_sop_playbook_active", "tenant_id", "playbook_key", "active"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    playbook_key: Mapped[str] = mapped_column(String(100), nullable=False)
    version: Mapped[str] = mapped_column(String(40), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


TENANT_SCOPED_TABLES = (
    "memberships",
    "channel_connections",
    "products",
    "variants",
    "listings",
    "inventory_positions",
    "orders",
    "order_lines",
    "sync_cursors",
    "durable_tasks",
    "agent_runs",
    "approval_requests",
    "approval_decisions",
    "audit_events",
    "inbox_events",
    "outbox_events",
    "sku_cost_profiles",
    "sku_profit_snapshots",
    "data_import_jobs",
    "inventory_policies",
    "inventory_health_snapshots",
    "operational_issues",
    "recommended_actions",
    "manual_execution_results",
    "customer_cases",
    "customer_case_events",
    "customer_case_clusters",
    "customer_case_cluster_members",
    "sop_playbooks",
    "daily_operations_briefs",
    "price_recommendations",
    "listing_quality_snapshots",
    "listing_optimization_experiments",
    "reconciliation_runs",
    "reconciliation_differences",
)


class ReconciliationRunRow(Base):
    __tablename__ = "reconciliation_runs"
    __table_args__ = (Index("ix_reconciliation_tenant_created", "tenant_id", "created_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    channel_connection_id: Mapped[str] = mapped_column(
        ForeignKey("channel_connections.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReconciliationDifferenceRow(Base):
    __tablename__ = "reconciliation_differences"
    __table_args__ = (Index("ix_reconciliation_difference_run", "tenant_id", "run_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("reconciliation_runs.id", ondelete="CASCADE"), index=True)
    resource_type: Mapped[str] = mapped_column(String(40), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(255), nullable=False)
    difference_type: Mapped[str] = mapped_column(String(100), nullable=False)
    amazon_value: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    internal_value: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SKUCostProfileRow(Base):
    __tablename__ = "sku_cost_profiles"
    __table_args__ = (
        UniqueConstraint("tenant_id", "marketplace_id", "seller_sku", "version", name="uq_cost_profile_scope_version"),
        Index("ix_cost_profile_active", "tenant_id", "marketplace_id", "seller_sku", "effective_from", "effective_to"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    seller_sku: Mapped[str] = mapped_column(String(100), nullable=False)
    marketplace_id: Mapped[str] = mapped_column(String(50), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    product_cost: Mapped[Decimal | None] = mapped_column(Numeric(19, 6))
    domestic_shipping_cost: Mapped[Decimal | None] = mapped_column(Numeric(19, 6))
    international_freight_cost: Mapped[Decimal | None] = mapped_column(Numeric(19, 6))
    duty_cost: Mapped[Decimal | None] = mapped_column(Numeric(19, 6))
    prep_cost: Mapped[Decimal | None] = mapped_column(Numeric(19, 6))
    packaging_cost: Mapped[Decimal | None] = mapped_column(Numeric(19, 6))
    estimated_referral_fee_rate: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    estimated_fulfillment_fee: Mapped[Decimal | None] = mapped_column(Numeric(19, 6))
    estimated_storage_cost: Mapped[Decimal | None] = mapped_column(Numeric(19, 6))
    estimated_return_loss_rate: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    exchange_rate_buffer_rate: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    target_margin_rate: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    minimum_margin_rate: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    import_job_id: Mapped[str | None] = mapped_column(String(36), index=True)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SKUProfitSnapshotRow(Base):
    __tablename__ = "sku_profit_snapshots"
    __table_args__ = (
        Index("ix_profit_snapshot_scope_window", "tenant_id", "marketplace_id", "seller_sku", "window_end"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    seller_sku: Mapped[str] = mapped_column(String(100), nullable=False)
    marketplace_id: Mapped[str] = mapped_column(String(50), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revenue: Mapped[Decimal] = mapped_column(Numeric(19, 4), nullable=False)
    units: Mapped[int] = mapped_column(Integer, nullable=False)
    refunds: Mapped[Decimal] = mapped_column(Numeric(19, 4), nullable=False)
    promotions: Mapped[Decimal] = mapped_column(Numeric(19, 4), nullable=False)
    referral_fees: Mapped[Decimal | None] = mapped_column(Numeric(19, 4))
    fulfillment_fees: Mapped[Decimal | None] = mapped_column(Numeric(19, 4))
    storage_cost: Mapped[Decimal | None] = mapped_column(Numeric(19, 4))
    advertising_cost: Mapped[Decimal | None] = mapped_column(Numeric(19, 4))
    landed_cost: Mapped[Decimal | None] = mapped_column(Numeric(19, 4))
    return_loss: Mapped[Decimal | None] = mapped_column(Numeric(19, 4))
    other_variable_cost: Mapped[Decimal | None] = mapped_column(Numeric(19, 4))
    contribution_profit: Mapped[Decimal | None] = mapped_column(Numeric(19, 4))
    contribution_margin: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    break_even_price: Mapped[Decimal | None] = mapped_column(Numeric(19, 4))
    break_even_acos: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    calculation_version: Mapped[str] = mapped_column(String(100), nullable=False)
    missing_inputs: Mapped[list[str]] = mapped_column(JSON, default=list)
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    degraded_reason: Mapped[str | None] = mapped_column(String(200))
    input_manifest: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    estimated: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DataImportJobRow(Base):
    __tablename__ = "data_import_jobs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "import_type", "idempotency_key", name="uq_import_tenant_type_idempotency"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    import_type: Mapped[str] = mapped_column(String(50), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    dry_run: Mapped[bool] = mapped_column(default=True)
    total_rows: Mapped[int] = mapped_column(Integer, default=0)
    valid_rows: Mapped[int] = mapped_column(Integer, default=0)
    committed_rows: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InventoryPolicyRow(Base):
    __tablename__ = "inventory_policies"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "marketplace_id", "seller_sku", "version", name="uq_inventory_policy_scope_version"
        ),
        Index(
            "ix_inventory_policy_active", "tenant_id", "marketplace_id", "seller_sku", "effective_from", "effective_to"
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    seller_sku: Mapped[str] = mapped_column(String(100), nullable=False)
    marketplace_id: Mapped[str] = mapped_column(String(50), nullable=False)
    supplier_lead_time_days: Mapped[int] = mapped_column(Integer, nullable=False)
    production_lead_time_days: Mapped[int] = mapped_column(Integer, nullable=False)
    shipping_lead_time_days: Mapped[int] = mapped_column(Integer, nullable=False)
    amazon_receiving_days: Mapped[int] = mapped_column(Integer, nullable=False)
    safety_stock_days: Mapped[int] = mapped_column(Integer, nullable=False)
    target_coverage_days: Mapped[int] = mapped_column(Integer, nullable=False)
    minimum_order_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    case_pack_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    preferred_shipping_mode: Mapped[str] = mapped_column(String(30), nullable=False)
    sales_weight_7d: Mapped[Decimal] = mapped_column(Numeric(7, 6), nullable=False)
    sales_weight_14d: Mapped[Decimal] = mapped_column(Numeric(7, 6), nullable=False)
    sales_weight_30d: Mapped[Decimal] = mapped_column(Numeric(7, 6), nullable=False)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class InventoryHealthSnapshotRow(Base):
    __tablename__ = "inventory_health_snapshots"
    __table_args__ = (
        Index("ix_inventory_health_scope_calculated", "tenant_id", "marketplace_id", "seller_sku", "calculated_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    seller_sku: Mapped[str] = mapped_column(String(100), nullable=False)
    marketplace_id: Mapped[str] = mapped_column(String(50), nullable=False)
    policy_id: Mapped[str] = mapped_column(String(36), nullable=False)
    policy_version: Mapped[int] = mapped_column(Integer, nullable=False)
    available: Mapped[int] = mapped_column(Integer, nullable=False)
    reserved: Mapped[int] = mapped_column(Integer, nullable=False)
    inbound: Mapped[int] = mapped_column(Integer, nullable=False)
    in_transit: Mapped[int] = mapped_column(Integer, nullable=False)
    unsellable: Mapped[int] = mapped_column(Integer, nullable=False)
    inventory_position: Mapped[int] = mapped_column(Integer, nullable=False)
    weighted_daily_sales: Mapped[Decimal] = mapped_column(Numeric(19, 6), nullable=False)
    days_of_supply: Mapped[Decimal | None] = mapped_column(Numeric(19, 4))
    expected_stockout_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    total_lead_time_days: Mapped[int] = mapped_column(Integer, nullable=False)
    safety_stock_units: Mapped[Decimal] = mapped_column(Numeric(19, 4), nullable=False)
    reorder_point: Mapped[Decimal] = mapped_column(Numeric(19, 4), nullable=False)
    recommended_reorder_quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    excess_units: Mapped[int] = mapped_column(Integer, nullable=False)
    recommended_shipping_mode: Mapped[str] = mapped_column(String(30), nullable=False)
    inventory_health: Mapped[str] = mapped_column(String(50), nullable=False)
    stockout_risk: Mapped[str] = mapped_column(String(20), nullable=False)
    excess_risk: Mapped[str] = mapped_column(String(20), nullable=False)
    forecast_confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    missing_data: Mapped[list[str]] = mapped_column(JSON, default=list)
    data_warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    degraded_reason: Mapped[str | None] = mapped_column(String(200))
    automation_eligible: Mapped[bool] = mapped_column(default=False)
    inventory_source: Mapped[str] = mapped_column(String(40), nullable=False)
    inventory_collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    calculation_version: Mapped[str] = mapped_column(String(100), nullable=False)
    input_manifest: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    estimated_revenue_at_risk: Mapped[Decimal | None] = mapped_column(Numeric(19, 4))
    estimated_profit_at_risk: Mapped[Decimal | None] = mapped_column(Numeric(19, 4))
    estimated_extra_logistics_cost: Mapped[Decimal | None] = mapped_column(Numeric(19, 4))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OperationalIssueRow(Base):
    __tablename__ = "operational_issues"
    __table_args__ = (
        UniqueConstraint("tenant_id", "dedupe_key", name="uq_operational_issue_tenant_dedupe"),
        Index("ix_operational_issue_queue", "tenant_id", "status", "severity", "last_detected_at"),
        Index("ix_operational_issue_sku", "tenant_id", "marketplace_id", "seller_sku"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    seller_sku: Mapped[str] = mapped_column(String(100), nullable=False)
    marketplace_id: Mapped[str] = mapped_column(String(50), nullable=False)
    issue_type: Mapped[str] = mapped_column(String(80), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(64), nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    data_freshness_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    missing_data: Mapped[list[str]] = mapped_column(JSON, default=list)
    currency: Mapped[str | None] = mapped_column(String(3))
    estimated_revenue_impact: Mapped[Decimal | None] = mapped_column(Numeric(19, 6))
    estimated_profit_impact: Mapped[Decimal | None] = mapped_column(Numeric(19, 6))
    estimated_cost_impact: Mapped[Decimal | None] = mapped_column(Numeric(19, 6))
    degraded_reason: Mapped[str | None] = mapped_column(String(255))
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    resolution_note: Mapped[str | None] = mapped_column(Text)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    assignee_id: Mapped[str | None] = mapped_column(String(255))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_code: Mapped[str | None] = mapped_column(String(100))
    execution_result_id: Mapped[str | None] = mapped_column(String(36))
    reopened_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    priority_score: Mapped[Decimal] = mapped_column(Numeric(19, 6), nullable=False, default=Decimal("0"))


class ManualExecutionResultRow(Base):
    __tablename__ = "manual_execution_results"
    __table_args__ = (
        UniqueConstraint("tenant_id", "approval_request_id", name="uq_manual_execution_tenant_approval"),
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_manual_execution_tenant_idempotency"),
        CheckConstraint("status IN ('executing', 'executed', 'failed')", name="ck_manual_execution_status"),
        CheckConstraint("length(action_hash) = 64", name="ck_manual_execution_action_hash"),
        CheckConstraint(
            "length(trim(execution_contract_version)) > 0",
            name="ck_manual_execution_contract_version",
        ),
        CheckConstraint("length(trim(idempotency_key)) > 0", name="ck_manual_execution_idempotency_key"),
        Index("ix_manual_execution_tenant_issue", "tenant_id", "issue_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    issue_id: Mapped[str] = mapped_column(ForeignKey("operational_issues.id", ondelete="CASCADE"), nullable=False)
    approval_request_id: Mapped[str] = mapped_column(
        ForeignKey("approval_requests.id", ondelete="RESTRICT"), nullable=False
    )
    recommended_action_id: Mapped[str] = mapped_column(String(36), nullable=False)
    action_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    execution_contract_version: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    action_type: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    before_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    approved_change: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    actual_change: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    execution_evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    executed_by: Mapped[str] = mapped_column(String(255), nullable=False)
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_reason: Mapped[str | None] = mapped_column(Text)
    verification_status: Mapped[str] = mapped_column(String(40), nullable=False)
    verification_details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)


class RecommendedActionRow(Base):
    __tablename__ = "recommended_actions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "dedupe_key", name="uq_recommended_action_tenant_dedupe"),
        Index("ix_recommended_action_queue", "tenant_id", "status", "risk_level", "created_at"),
        Index("ix_recommended_action_issue", "tenant_id", "issue_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    issue_id: Mapped[str] = mapped_column(ForeignKey("operational_issues.id", ondelete="CASCADE"), nullable=False)
    action_type: Mapped[str] = mapped_column(String(80), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    execution_mode: Mapped[str] = mapped_column(String(30), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    expected_outcome: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    automation_eligible: Mapped[bool] = mapped_column(nullable=False, default=False)
    dedupe_key: Mapped[str] = mapped_column(String(64), nullable=False)
    currency: Mapped[str | None] = mapped_column(String(3))
    expected_revenue_impact: Mapped[Decimal | None] = mapped_column(Numeric(19, 6))
    expected_profit_impact: Mapped[Decimal | None] = mapped_column(Numeric(19, 6))
    expected_cost_impact: Mapped[Decimal | None] = mapped_column(Numeric(19, 6))
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OperationalWorkItemRow(Base):
    __tablename__ = "operational_work_items"
    __table_args__ = (
        UniqueConstraint("tenant_id", "source_type", "source_id", name="uq_work_item_tenant_source"),
        Index("ix_work_item_queue", "tenant_id", "status", "severity", "due_at"),
        Index("ix_work_item_assignee", "tenant_id", "assignee_id", "status", "due_at"),
        Index("ix_work_item_scope", "tenant_id", "marketplace_id", "seller_sku", "domain"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    source_type: Mapped[str] = mapped_column(String(50), nullable=False)
    source_id: Mapped[str] = mapped_column(String(100), nullable=False)
    operational_issue_id: Mapped[str] = mapped_column(
        ForeignKey("operational_issues.id", ondelete="CASCADE"), nullable=False
    )
    recommended_action_id: Mapped[str | None] = mapped_column(ForeignKey("recommended_actions.id", ondelete="CASCADE"))
    domain: Mapped[str] = mapped_column(String(50), nullable=False)
    team: Mapped[str] = mapped_column(String(50), nullable=False)
    seller_sku: Mapped[str] = mapped_column(String(100), nullable=False)
    marketplace_id: Mapped[str] = mapped_column(String(50), nullable=False)
    brand: Mapped[str | None] = mapped_column(String(255))
    assignee_id: Mapped[str | None] = mapped_column(String(255))
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    priority_score: Mapped[Decimal] = mapped_column(Numeric(19, 6), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sla_policy_version: Mapped[str] = mapped_column(String(50), nullable=False)
    escalation_level: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expected_outcome: Mapped[str] = mapped_column(Text, nullable=False)
    baseline_metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result_metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    verification_status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    verification_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    evidence_manifest: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    action_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    handoff_note: Mapped[str | None] = mapped_column(Text)
    blocked_reason: Mapped[str | None] = mapped_column(Text)
    last_handoff_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DailyOperationsBriefRow(Base):
    __tablename__ = "daily_operations_briefs"
    __table_args__ = (
        UniqueConstraint("tenant_id", "brief_date", "timezone", name="uq_daily_brief_tenant_date_timezone"),
        Index("ix_daily_brief_latest", "tenant_id", "generated_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    brief_date: Mapped[date] = mapped_column(Date, nullable=False)
    timezone: Mapped[str] = mapped_column(String(100), nullable=False)
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    issue_counts: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    domain_counts: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    total_open_issues: Mapped[int] = mapped_column(Integer, nullable=False)
    data_quality_issue_count: Mapped[int] = mapped_column(Integer, nullable=False)
    impact_by_currency: Mapped[dict[str, dict[str, str]]] = mapped_column(JSON, default=dict)
    unallocated_impact_issue_count: Mapped[int] = mapped_column(Integer, nullable=False)
    priority_issue_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    priority_action_ids: Mapped[list[str]] = mapped_column(JSON, default=list)
    sku_health: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    source_manifest: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class PriceRecommendationRow(Base):
    __tablename__ = "price_recommendations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "action_hash", name="uq_price_recommendation_tenant_action_hash"),
        Index("ix_price_recommendation_queue", "tenant_id", "status", "risk_level", "calculated_at"),
        Index("ix_price_recommendation_sku", "tenant_id", "marketplace_id", "seller_sku"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    seller_sku: Mapped[str] = mapped_column(String(100), nullable=False)
    marketplace_id: Mapped[str] = mapped_column(String(50), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    profit_snapshot_id: Mapped[str] = mapped_column(String(36), nullable=False)
    inventory_snapshot_id: Mapped[str] = mapped_column(String(36), nullable=False)
    cost_profile_id: Mapped[str] = mapped_column(String(36), nullable=False)
    current_price: Mapped[Decimal] = mapped_column(Numeric(19, 6), nullable=False)
    recommended_price: Mapped[Decimal] = mapped_column(Numeric(19, 6), nullable=False)
    candidate_source: Mapped[str] = mapped_column(String(40), nullable=False)
    break_even_floor: Mapped[Decimal] = mapped_column(Numeric(19, 6), nullable=False)
    minimum_margin_floor: Mapped[Decimal] = mapped_column(Numeric(19, 6), nullable=False)
    minimum_margin_rate: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    projected_unit_profit: Mapped[Decimal] = mapped_column(Numeric(19, 6), nullable=False)
    projected_margin: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    change_rate: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    policy_decision: Mapped[str] = mapped_column(String(30), nullable=False)
    policy_reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    confidence: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    automation_eligible: Mapped[bool] = mapped_column(nullable=False, default=False)
    action_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    missing_data: Mapped[list[str]] = mapped_column(JSON, default=list)
    degraded_reason: Mapped[str | None] = mapped_column(String(255))
    approval_request_id: Mapped[str | None] = mapped_column(String(36), index=True)
    occurrence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ListingQualitySnapshotRow(Base):
    __tablename__ = "listing_quality_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "listing_id", "content_version", "calculated_at", name="uq_listing_quality_version_time"
        ),
        Index("ix_listing_quality_latest", "tenant_id", "marketplace_id", "seller_sku", "calculated_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    listing_id: Mapped[str] = mapped_column(String(36), nullable=False)
    seller_sku: Mapped[str] = mapped_column(String(100), nullable=False)
    marketplace_id: Mapped[str] = mapped_column(String(50), nullable=False)
    content_version: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    bullet_points: Mapped[list[str]] = mapped_column(JSON, default=list)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    image_count: Mapped[int] = mapped_column(Integer, nullable=False)
    search_terms: Mapped[list[str]] = mapped_column(JSON, default=list)
    listing_status: Mapped[str] = mapped_column(String(50), nullable=False)
    quality_score: Mapped[Decimal] = mapped_column(Numeric(9, 4), nullable=False)
    quality_status: Mapped[str] = mapped_column(String(20), nullable=False)
    section_scores: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    quality_issues: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    recommendations: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    evidence: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    confidence: Mapped[Decimal] = mapped_column(Numeric(9, 6), nullable=False)
    missing_data: Mapped[list[str]] = mapped_column(JSON, default=list)
    degraded_reason: Mapped[str | None] = mapped_column(String(255))
    automation_eligible: Mapped[bool] = mapped_column(nullable=False, default=False)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    source_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    calculation_version: Mapped[str] = mapped_column(String(50), nullable=False)
    baseline_metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class ListingOptimizationExperimentRow(Base):
    __tablename__ = "listing_optimization_experiments"
    __table_args__ = (
        UniqueConstraint("tenant_id", "action_hash", name="uq_listing_experiment_tenant_action_hash"),
        Index("ix_listing_experiment_status", "tenant_id", "status", "observation_end"),
        Index("ix_listing_experiment_sku", "tenant_id", "marketplace_id", "seller_sku"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    listing_id: Mapped[str] = mapped_column(String(36), nullable=False)
    seller_sku: Mapped[str] = mapped_column(String(100), nullable=False)
    marketplace_id: Mapped[str] = mapped_column(String(50), nullable=False)
    before_snapshot_id: Mapped[str] = mapped_column(String(36), nullable=False)
    after_snapshot_id: Mapped[str | None] = mapped_column(String(36))
    proposed_changes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    baseline_metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    observation_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    observation_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expected_outcome: Mapped[str] = mapped_column(Text, nullable=False)
    action_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    approval_request_id: Mapped[str | None] = mapped_column(String(36), index=True)
    effect_metrics: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)
    evaluation_reasons: Mapped[list[str]] = mapped_column(JSON, default=list)
    rollback_recommended: Mapped[bool] = mapped_column(nullable=False, default=False)
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    execution_status: Mapped[str] = mapped_column(String(30), nullable=False, default="not_executed")
    actual_changes: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    execution_evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    executed_by: Mapped[str | None] = mapped_column(String(255))
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    execution_notes: Mapped[str | None] = mapped_column(Text)
    decision: Mapped[str | None] = mapped_column(String(30))
    decision_by: Mapped[str | None] = mapped_column(String(255))
    decision_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AmazonOperationalSignalRow(Base):
    __tablename__ = "amazon_operational_signals"
    __table_args__ = (
        UniqueConstraint("tenant_id", "signal_type", "resource_id", "observed_at", name="uq_amazon_signal_observation"),
        Index("ix_amazon_signal_queue", "tenant_id", "severity", "signal_type", "observed_at"),
        Index("ix_amazon_signal_resource", "tenant_id", "marketplace_id", "resource_id"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    marketplace_id: Mapped[str] = mapped_column(String(50), nullable=False)
    signal_type: Mapped[str] = mapped_column(String(60), nullable=False)
    resource_id: Mapped[str] = mapped_column(String(200), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    freshness_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    verified_amazon_data: Mapped[bool] = mapped_column(Boolean, nullable=False)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    raw_status_code: Mapped[str] = mapped_column(String(100), nullable=False)
    evidence_manifest: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AdvertisingDailySnapshotRow(Base):
    __tablename__ = "advertising_daily_snapshots"
    __table_args__ = (
        UniqueConstraint("tenant_id", "entity_type", "entity_id", "period_start", name="uq_ad_daily_snapshot_period"),
        Index("ix_ad_daily_queue", "tenant_id", "marketplace_id", "seller_sku", "period_end"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    marketplace_id: Mapped[str] = mapped_column(String(50), nullable=False)
    seller_sku: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(30), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(200), nullable=False)
    campaign_id: Mapped[str | None] = mapped_column(String(200))
    ad_group_id: Mapped[str | None] = mapped_column(String(200))
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    spend: Mapped[Decimal] = mapped_column(Numeric(19, 6), nullable=False)
    attributed_sales: Mapped[Decimal | None] = mapped_column(Numeric(19, 6))
    total_sales: Mapped[Decimal | None] = mapped_column(Numeric(19, 6))
    budget: Mapped[Decimal | None] = mapped_column(Numeric(19, 6))
    impressions: Mapped[int | None] = mapped_column(Integer)
    clicks: Mapped[int | None] = mapped_column(Integer)
    orders: Mapped[int | None] = mapped_column(Integer)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    verified_amazon_data: Mapped[bool] = mapped_column(Boolean, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SearchTermPerformanceSnapshotRow(Base):
    __tablename__ = "search_term_performance_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "campaign_id", "search_term", "period_start", name="uq_search_term_snapshot_period"
        ),
        Index("ix_search_term_queue", "tenant_id", "marketplace_id", "seller_sku", "period_end"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    marketplace_id: Mapped[str] = mapped_column(String(50), nullable=False)
    seller_sku: Mapped[str] = mapped_column(String(100), nullable=False)
    campaign_id: Mapped[str] = mapped_column(String(200), nullable=False)
    ad_group_id: Mapped[str | None] = mapped_column(String(200))
    search_term: Mapped[str] = mapped_column(String(500), nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    spend: Mapped[Decimal] = mapped_column(Numeric(19, 6), nullable=False)
    attributed_sales: Mapped[Decimal | None] = mapped_column(Numeric(19, 6))
    impressions: Mapped[int | None] = mapped_column(Integer)
    clicks: Mapped[int | None] = mapped_column(Integer)
    orders: Mapped[int | None] = mapped_column(Integer)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    verified_amazon_data: Mapped[bool] = mapped_column(Boolean, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AdvertisingBudgetDiagnosticRow(Base):
    __tablename__ = "advertising_budget_diagnostics"
    __table_args__ = (
        UniqueConstraint("tenant_id", "snapshot_id", "code", name="uq_ad_budget_diagnostic"),
        Index("ix_ad_budget_diagnostic_queue", "tenant_id", "status", "severity", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("advertising_daily_snapshots.id", ondelete="CASCADE"), nullable=False
    )
    seller_sku: Mapped[str] = mapped_column(String(100), nullable=False)
    marketplace_id: Mapped[str] = mapped_column(String(50), nullable=False)
    campaign_id: Mapped[str] = mapped_column(String(200), nullable=False)
    code: Mapped[str] = mapped_column(String(80), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="open")
    work_item_id: Mapped[str | None] = mapped_column(String(36), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReturnEventRow(Base):
    __tablename__ = "return_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "external_return_id", name="uq_return_event_external"),
        Index("ix_return_event_sku", "tenant_id", "marketplace_id", "seller_sku", "observed_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    external_return_id: Mapped[str] = mapped_column(String(200), nullable=False)
    external_order_id: Mapped[str | None] = mapped_column(String(200))
    marketplace_id: Mapped[str] = mapped_column(String(50), nullable=False)
    seller_sku: Mapped[str] = mapped_column(String(100), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(80), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    return_amount: Mapped[Decimal | None] = mapped_column(Numeric(19, 6))
    currency: Mapped[str | None] = mapped_column(String(3))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    verified_amazon_data: Mapped[bool] = mapped_column(Boolean, nullable=False)
    evidence_manifest: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ReviewObservationRow(Base):
    __tablename__ = "review_observations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "external_review_id", name="uq_review_observation_external"),
        Index("ix_review_observation_sku", "tenant_id", "marketplace_id", "seller_sku", "observed_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    external_review_id: Mapped[str] = mapped_column(String(200), nullable=False)
    marketplace_id: Mapped[str] = mapped_column(String(50), nullable=False)
    seller_sku: Mapped[str] = mapped_column(String(100), nullable=False)
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    review_text: Mapped[str | None] = mapped_column(Text)
    reason_code: Mapped[str | None] = mapped_column(String(80))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    verified_amazon_data: Mapped[bool] = mapped_column(Boolean, nullable=False)
    evidence_manifest: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BuyerMessageTagRow(Base):
    __tablename__ = "buyer_message_tags"
    __table_args__ = (
        UniqueConstraint("tenant_id", "external_message_id", "tag", name="uq_buyer_message_tag"),
        Index("ix_buyer_message_tag_sku", "tenant_id", "marketplace_id", "seller_sku", "observed_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    external_message_id: Mapped[str] = mapped_column(String(200), nullable=False)
    marketplace_id: Mapped[str] = mapped_column(String(50), nullable=False)
    seller_sku: Mapped[str] = mapped_column(String(100), nullable=False)
    tag: Mapped[str] = mapped_column(String(80), nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    verified_amazon_data: Mapped[bool] = mapped_column(Boolean, nullable=False)
    evidence_manifest: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SKUVoCSummaryRow(Base):
    __tablename__ = "sku_voc_summaries"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "marketplace_id", "seller_sku", "period_start", "period_end", name="uq_sku_voc_period"
        ),
        Index("ix_sku_voc_queue", "tenant_id", "status", "top_reason_code", "period_end"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    marketplace_id: Mapped[str] = mapped_column(String(50), nullable=False)
    seller_sku: Mapped[str] = mapped_column(String(100), nullable=False)
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    return_count: Mapped[int] = mapped_column(Integer, nullable=False)
    review_count: Mapped[int] = mapped_column(Integer, nullable=False)
    message_count: Mapped[int] = mapped_column(Integer, nullable=False)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False)
    reason_counts: Mapped[dict[str, int]] = mapped_column(JSON, default=dict)
    top_reason_code: Mapped[str | None] = mapped_column(String(80))
    top_reason_count: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    recommended_action: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    work_item_id: Mapped[str | None] = mapped_column(String(36), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IssueFeedbackRow(Base):
    __tablename__ = "issue_feedback"
    __table_args__ = (
        UniqueConstraint("tenant_id", "operational_issue_id", "actor_id", name="uq_issue_feedback_actor"),
        Index("ix_issue_feedback_rule", "tenant_id", "rule_version", "created_at"),
        Index("ix_issue_feedback_reason", "tenant_id", "reason_code", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    operational_issue_id: Mapped[str] = mapped_column(
        ForeignKey("operational_issues.id", ondelete="CASCADE"), nullable=False
    )
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    is_correct: Mapped[bool] = mapped_column(Boolean, nullable=False)
    is_useful: Mapped[bool] = mapped_column(Boolean, nullable=False)
    reason_code: Mapped[str] = mapped_column(String(80), nullable=False)
    rule_version: Mapped[str] = mapped_column(String(100), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MissedIssueReportRow(Base):
    __tablename__ = "missed_issue_reports"
    __table_args__ = (
        Index("ix_missed_issue_queue", "tenant_id", "status", "severity", "created_at"),
        Index("ix_missed_issue_scope", "tenant_id", "marketplace_id", "seller_sku", "issue_type"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    marketplace_id: Mapped[str] = mapped_column(String(50), nullable=False)
    seller_sku: Mapped[str] = mapped_column(String(100), nullable=False)
    issue_type: Mapped[str] = mapped_column(String(100), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_manifest: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="open")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class OperatorActivityEventRow(Base):
    __tablename__ = "operator_activity_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_activity_event_idempotency"),
        Index("ix_activity_work_item", "tenant_id", "work_item_id", "actor_id", "occurred_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    work_item_id: Mapped[str] = mapped_column(
        ForeignKey("operational_work_items.id", ondelete="CASCADE"), nullable=False
    )
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    event_type: Mapped[str] = mapped_column(String(30), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class BusinessContextEventRow(Base):
    __tablename__ = "business_context_events"
    __table_args__ = (
        UniqueConstraint("tenant_id", "dedupe_key", name="uq_business_context_dedupe"),
        Index("ix_business_context_window", "tenant_id", "marketplace_id", "seller_sku", "starts_at", "ends_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    marketplace_id: Mapped[str] = mapped_column(String(50), nullable=False)
    seller_sku: Mapped[str | None] = mapped_column(String(100))
    context_type: Mapped[str] = mapped_column(String(60), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    source: Mapped[str] = mapped_column(String(40), nullable=False)
    verified_amazon_data: Mapped[bool] = mapped_column(Boolean, nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ActionExecutionAttemptRow(Base):
    __tablename__ = "action_execution_attempts"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_action_execution_idempotency"),
        Index("ix_action_execution_work_item", "tenant_id", "work_item_id", "executed_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    work_item_id: Mapped[str] = mapped_column(
        ForeignKey("operational_work_items.id", ondelete="CASCADE"), nullable=False
    )
    action_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    execution_mode: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    exact_parameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    execution_evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    actor_id: Mapped[str] = mapped_column(String(255), nullable=False)
    executed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    amazon_write_performed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ActionOutcomeRow(Base):
    __tablename__ = "action_outcomes"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "execution_id", "primary_metric", "observation_end", name="uq_action_outcome_window"
        ),
        Index("ix_action_outcome_work_item", "tenant_id", "work_item_id", "evaluated_at"),
        Index("ix_action_outcome_attribution", "tenant_id", "attribution_level", "outcome_status"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    work_item_id: Mapped[str] = mapped_column(
        ForeignKey("operational_work_items.id", ondelete="CASCADE"), nullable=False
    )
    execution_id: Mapped[str] = mapped_column(
        ForeignKey("action_execution_attempts.id", ondelete="CASCADE"), nullable=False
    )
    primary_metric: Mapped[str] = mapped_column(String(100), nullable=False)
    desired_direction: Mapped[str] = mapped_column(String(20), nullable=False)
    baseline_metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result_metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    guardrail_metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    observation_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    observation_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    confounders: Mapped[list[str]] = mapped_column(JSON, default=list)
    control_metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    randomized_experiment: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    minimum_sample_satisfied: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    attribution_level: Mapped[str] = mapped_column(String(40), nullable=False)
    outcome_status: Mapped[str] = mapped_column(String(40), nullable=False)
    incremental_value: Mapped[Decimal | None] = mapped_column(Numeric(19, 6))
    currency: Mapped[str | None] = mapped_column(String(3))
    causal_claim_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    recommended_decision: Mapped[str] = mapped_column(String(30), nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RuleVersionRow(Base):
    __tablename__ = "intelligence_rule_versions"
    __table_args__ = (
        UniqueConstraint("tenant_id", "rule_key", "version", name="uq_rule_version_scope"),
        Index("ix_rule_version_active", "tenant_id", "rule_key", "status"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    rule_key: Mapped[str] = mapped_column(String(160), nullable=False)
    version: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    evaluation_metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    source: Mapped[str] = mapped_column(String(40), nullable=False, default="manual")
    parent_version: Mapped[str | None] = mapped_column(String(100))
    created_by: Mapped[str] = mapped_column(String(255), nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(255))
    shadow_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    shadow_ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class CalibrationProposalRow(Base):
    __tablename__ = "intelligence_calibration_proposals"
    __table_args__ = (
        UniqueConstraint("tenant_id", "candidate_hash", name="uq_calibration_candidate_hash"),
        Index("ix_calibration_queue", "tenant_id", "status", "created_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    rule_key: Mapped[str] = mapped_column(String(160), nullable=False)
    base_version: Mapped[str] = mapped_column(String(100), nullable=False)
    candidate_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    current_parameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    candidate_parameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    replay_metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    label_count: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(40), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    proposed_by: Mapped[str] = mapped_column(String(255), nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(255))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ExecutionAuthorizationRow(Base):
    __tablename__ = "action_execution_authorizations"
    __table_args__ = (
        UniqueConstraint("tenant_id", "idempotency_key", name="uq_execution_authorization_idempotency"),
        Index("ix_execution_authorization_queue", "tenant_id", "status", "expires_at"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    work_item_id: Mapped[str] = mapped_column(
        ForeignKey("operational_work_items.id", ondelete="CASCADE"), nullable=False
    )
    action_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    action_type: Mapped[str] = mapped_column(String(100), nullable=False)
    risk_level: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    exact_parameters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    preflight_evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    requested_by: Mapped[str] = mapped_column(String(255), nullable=False)
    approved_by: Mapped[str | None] = mapped_column(String(255))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AttributionPlanRow(Base):
    __tablename__ = "action_attribution_plans"
    __table_args__ = (
        UniqueConstraint("tenant_id", "work_item_id", "action_hash", name="uq_attribution_plan_action"),
        Index("ix_attribution_plan_queue", "tenant_id", "status", "observation_end"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    work_item_id: Mapped[str] = mapped_column(
        ForeignKey("operational_work_items.id", ondelete="CASCADE"), nullable=False
    )
    action_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    primary_metric: Mapped[str] = mapped_column(String(100), nullable=False)
    desired_direction: Mapped[str] = mapped_column(String(20), nullable=False)
    baseline_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    baseline_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    observation_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    observation_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    method: Mapped[str] = mapped_column(String(50), nullable=False)
    minimum_sample: Mapped[int] = mapped_column(Integer, nullable=False)
    guardrail_metrics: Mapped[list[str]] = mapped_column(JSON, default=list)
    control_spec: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    confounders: Mapped[list[str]] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="planned")
    registered_by: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
