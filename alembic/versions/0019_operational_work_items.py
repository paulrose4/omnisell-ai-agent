"""Add unified operational work items.

Revision ID: 0019
Revises: 0018
Create Date: 2026-07-20
"""

from __future__ import annotations

from datetime import timedelta

from alembic import op
from sqlalchemy import select

from infrastructure.database import models
from safety.policy_engine import canonical_action_hash


revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    table = models.Base.metadata.tables["operational_work_items"]
    table.create(bind=bind, checkfirst=True)
    _backfill(bind, table)
    if bind.dialect.name == "postgresql":
        op.execute('ALTER TABLE "operational_work_items" ENABLE ROW LEVEL SECURITY')
        op.execute('DROP POLICY IF EXISTS operational_work_items_tenant_isolation ON "operational_work_items"')
        op.execute(
            """CREATE POLICY operational_work_items_tenant_isolation ON "operational_work_items"
            USING (tenant_id = current_setting('app.current_tenant_id', true))
            WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true))"""
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute('DROP POLICY IF EXISTS operational_work_items_tenant_isolation ON "operational_work_items"')
    models.Base.metadata.tables["operational_work_items"].drop(bind=bind, checkfirst=True)


def _backfill(bind, work_items) -> None:
    actions = models.Base.metadata.tables["recommended_actions"]
    issues = models.Base.metadata.tables["operational_issues"]
    rows = bind.execute(select(
        actions.c.id.label("action_id"),
        actions.c.tenant_id,
        actions.c.issue_id,
        actions.c.action_type,
        actions.c.status.label("action_status"),
        actions.c.parameters,
        actions.c.title,
        actions.c.description,
        actions.c.expected_outcome,
        actions.c.currency,
        actions.c.expected_revenue_impact,
        actions.c.expected_profit_impact,
        actions.c.expected_cost_impact,
        actions.c.confidence,
        actions.c.evidence,
        actions.c.created_at,
        actions.c.updated_at,
        issues.c.issue_type,
        issues.c.status.label("issue_status"),
        issues.c.severity,
        issues.c.marketplace_id,
        issues.c.seller_sku,
        issues.c.assignee_id,
        issues.c.priority_score,
        issues.c.acknowledged_at,
        issues.c.resolved_at,
    ).join(
        issues,
        (issues.c.tenant_id == actions.c.tenant_id) & (issues.c.id == actions.c.issue_id),
    )).mappings()
    for row in rows:
        action_id = row["action_id"]
        issue_type = row["issue_type"]
        action_type = row["action_type"]
        domain, team = _scope(issue_type, action_type)
        status = _status(row["issue_status"], row["action_status"], row["assignee_id"])
        parameters = row["parameters"] or {}
        action_hash = canonical_action_hash(
            action=action_type,
            tenant_id=row["tenant_id"],
            resource_id=f"{row['marketplace_id']}:{row['seller_sku']}:{row['issue_id']}",
            arguments=parameters,
        )
        severity = row["severity"]
        due_at = row["created_at"] + {
            "critical": timedelta(hours=1), "high": timedelta(hours=4),
            "medium": timedelta(days=1), "low": timedelta(days=3),
        }[severity]
        bind.execute(work_items.insert().values(
            id=action_id, tenant_id=row["tenant_id"], source_type="recommended_action", source_id=action_id,
            operational_issue_id=row["issue_id"], recommended_action_id=action_id,
            domain=domain, team=team, seller_sku=row["seller_sku"], marketplace_id=row["marketplace_id"],
            brand=None, assignee_id=row["assignee_id"], severity=severity,
            priority_score=row["priority_score"] or 0, title=row["title"], description=row["description"],
            status=status, due_at=due_at, acknowledged_at=row["acknowledged_at"],
            sla_policy_version="ops-sla-v1", escalation_level=0, expected_outcome=row["expected_outcome"],
            baseline_metrics={
                "currency": row["currency"],
                "expected_revenue_impact": None if row["expected_revenue_impact"] is None else str(row["expected_revenue_impact"]),
                "expected_profit_impact": None if row["expected_profit_impact"] is None else str(row["expected_profit_impact"]),
                "expected_cost_impact": None if row["expected_cost_impact"] is None else str(row["expected_cost_impact"]),
                "confidence": str(row["confidence"]),
            },
            result_metrics={}, verification_status="pending", verification_attempts=0,
            evidence_manifest=row["evidence"] or [], action_hash=action_hash,
            handoff_note=None, blocked_reason=None, last_handoff_at=None,
            created_at=row["created_at"], updated_at=row["updated_at"],
            completed_at=row["resolved_at"] if status == "completed" else None,
        ))


def _scope(issue_type: str, action_type: str) -> tuple[str, str]:
    value = f"{issue_type} {action_type}".lower()
    if "customer" in value or "reply" in value:
        return "customer_service", "customer_service"
    if "listing" in value:
        return "listing", "listing"
    if "advert" in value or "acos" in value or "search_term" in value:
        return "advertising", "growth"
    if "profit" in value or "price" in value or "margin" in value:
        return "profitability", "finance"
    if "reconciliation" in value or "account_health" in value or "buy_box" in value:
        return "amazon_operations", "operations"
    if "inventory" in value or "stockout" in value or "replenish" in value:
        return "inventory", "supply_chain"
    return "operations", "operations"


def _status(issue_status: str, action_status: str, assignee_id: str | None) -> str:
    if action_status == "completed" or issue_status == "resolved":
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
