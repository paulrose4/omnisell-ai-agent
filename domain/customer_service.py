from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

CASE_STATUSES = {
    "open",
    "acknowledged",
    "in_progress",
    "awaiting_approval",
    "ready_to_send",
    "policy_blocked",
    "sent",
    "resolved",
    "dismissed",
    "reopened",
}


def utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass
class CustomerServiceCase:
    tenant_id: str
    external_case_id: str
    marketplace_id: str
    language: str
    intent: str
    customer_message: str
    severity: str
    risk_level: str
    priority_score: Decimal
    source: str
    verified_amazon_data: bool
    id: str = field(default_factory=lambda: str(uuid4()))
    external_order_id: str | None = None
    seller_sku: str | None = None
    asin: str | None = None
    sentiment: str = "neutral"
    urgency: str = "normal"
    status: str = "open"
    assignee_id: str | None = None
    due_at: datetime | None = None
    acknowledged_at: datetime | None = None
    started_at: datetime | None = None
    sent_at: datetime | None = None
    resolved_at: datetime | None = None
    draft_reply: str | None = None
    approved_reply: str | None = None
    actual_reply: str | None = None
    confidence: Decimal = Decimal("1")
    evidence: dict[str, Any] = field(default_factory=dict)
    missing_data: tuple[str, ...] = ()
    policy_checks: dict[str, Any] = field(default_factory=dict)
    playbook_key: str | None = None
    playbook_version: str | None = None
    approval_request_id: str | None = None
    resolution_code: str | None = None
    root_cause_code: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if self.status not in CASE_STATUSES:
            raise ValueError(f"unsupported customer case status: {self.status}")
        if self.severity not in {"low", "medium", "high", "critical"}:
            raise ValueError("unsupported customer case severity")
        if self.risk_level not in {"low", "medium", "high", "critical"}:
            raise ValueError("unsupported customer case risk level")
        if self.source == "mock_buyer" and self.verified_amazon_data:
            raise ValueError("Mock customer cases cannot be marked as verified Amazon data")
