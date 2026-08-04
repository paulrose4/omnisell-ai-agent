from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from domain.models import new_id, utc_now

WORK_ITEM_ACTIVE_STATUSES = {
    "open",
    "assigned",
    "in_progress",
    "blocked",
    "awaiting_approval",
    "verifying",
}


@dataclass
class OperationalWorkItem:
    tenant_id: str
    source_type: str
    source_id: str
    operational_issue_id: str
    recommended_action_id: str | None
    domain: str
    team: str
    seller_sku: str
    marketplace_id: str
    severity: str
    priority_score: Decimal
    title: str
    description: str
    status: str
    due_at: datetime
    sla_policy_version: str
    expected_outcome: str
    baseline_metrics: dict[str, Any]
    evidence_manifest: list[dict[str, Any]]
    action_hash: str
    id: str = field(default_factory=new_id)
    brand: str | None = None
    assignee_id: str | None = None
    acknowledged_at: datetime | None = None
    escalation_level: int = 0
    result_metrics: dict[str, Any] = field(default_factory=dict)
    verification_status: str = "pending"
    verification_attempts: int = 0
    handoff_note: str | None = None
    blocked_reason: str | None = None
    last_handoff_at: datetime | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    completed_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.status not in WORK_ITEM_ACTIVE_STATUSES | {"completed", "canceled"}:
            raise ValueError("invalid work item status")
        if self.severity not in {"low", "medium", "high", "critical"}:
            raise ValueError("invalid work item severity")
        if self.due_at.tzinfo is None or self.created_at.tzinfo is None or self.updated_at.tzinfo is None:
            raise ValueError("work item timestamps must be timezone-aware")
        if len(self.action_hash) != 64:
            raise ValueError("action_hash must be a SHA-256 digest")
        if self.escalation_level < 0:
            raise ValueError("escalation_level cannot be negative")


def work_item_scope(issue_type: str, action_type: str) -> tuple[str, str]:
    normalized = f"{issue_type} {action_type}".lower()
    if "product_defect" in normalized or "product_quality" in normalized:
        return "product_quality", "quality"
    if "packaging_problem" in normalized or "shipping_damage" in normalized:
        return "inventory", "supply_chain"
    if "wrong_or_missing_item" in normalized:
        return "fulfillment", "operations"
    if "instructions_unclear" in normalized:
        return "customer_service", "customer_service"
    if "customer" in normalized or "reply" in normalized:
        return "customer_service", "customer_service"
    if "listing" in normalized:
        return "listing", "listing"
    if "advert" in normalized or "acos" in normalized or "search_term" in normalized:
        return "advertising", "growth"
    if "profit" in normalized or "price" in normalized or "margin" in normalized:
        return "profitability", "finance"
    if "reconciliation" in normalized or "account_health" in normalized or "buy_box" in normalized:
        return "amazon_operations", "operations"
    if "inventory" in normalized or "stockout" in normalized or "replenish" in normalized:
        return "inventory", "supply_chain"
    return "operations", "operations"


def sla_due_at(severity: str, created_at: datetime) -> datetime:
    durations = {
        "critical": timedelta(hours=1),
        "high": timedelta(hours=4),
        "medium": timedelta(days=1),
        "low": timedelta(days=3),
    }
    return created_at.astimezone(UTC) + durations[severity]


def work_item_status(issue_status: str, action_status: str, assignee_id: str | None) -> str:
    if action_status in {"completed"} or issue_status == "resolved":
        return "completed"
    if action_status in {"rejected", "canceled"} or issue_status == "dismissed":
        return "canceled"
    if issue_status == "awaiting_approval":
        return "awaiting_approval"
    if issue_status in {"verifying", "verification_failed"}:
        return "verifying"
    if issue_status == "in_progress":
        return "in_progress"
    return "assigned" if assignee_id else "open"
