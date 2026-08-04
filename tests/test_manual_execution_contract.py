from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from application.manual_execution_contract import build_operational_approval_contract
from domain.models import (
    ChannelConnection,
    ChannelProvider,
    ConnectionStatus,
    OperationalIssue,
    RecommendedAction,
)

MARKETPLACE_ID = "ATVPDKIKX0DER"


def _resources(*, confidence: Decimal = Decimal("1")):
    now = datetime(2026, 7, 15, 8, 0, tzinfo=UTC)
    issue = OperationalIssue(
        id="issue-1",
        tenant_id="tenant-1",
        seller_sku="SKU-1",
        marketplace_id=MARKETPLACE_ID,
        issue_type="inventory_stockout_risk",
        severity="critical",
        title="Stockout risk",
        summary="Needs replenishment",
        status="in_progress",
        dedupe_key="issue-dedupe",
        confidence=confidence,
        data_freshness_at=now,
        evidence=({"data_source": "mock_amazon", "verified_amazon_data": False},),
        missing_data=(),
        detected_at=now,
        last_detected_at=now,
    )
    action = RecommendedAction(
        id="action-1",
        tenant_id="tenant-1",
        issue_id=issue.id,
        action_type="review_replenishment",
        title="Review replenishment",
        description="Review the deterministic reorder quantity.",
        execution_mode="recommendation_only",
        risk_level="critical",
        status="proposed",
        parameters={
            "seller_sku": issue.seller_sku,
            "marketplace_id": issue.marketplace_id,
            "recommended_reorder_quantity": 600,
            "replenishment_blocked": False,
        },
        evidence=issue.evidence,
        expected_outcome="Restore inventory coverage.",
        confidence=confidence,
        automation_eligible=False,
        dedupe_key="action-dedupe",
        created_at=now,
        updated_at=now,
    )
    connection = ChannelConnection(
        id="connection-1",
        tenant_id="tenant-1",
        provider=ChannelProvider.AMAZON,
        selling_partner_id="seller-1",
        region="na",
        marketplace_ids=(MARKETPLACE_ID,),
        secret_ref="memory://mock",
        status=ConnectionStatus.ACTIVE,
    )
    return issue, action, connection


def test_approval_hash_is_stable_across_decimal_database_scale() -> None:
    compact = _resources(confidence=Decimal("1"))
    persisted = _resources(confidence=Decimal("1.000000"))

    compact_contract = build_operational_approval_contract(
        issue=compact[0],
        action=compact[1],
        connection=compact[2],
    )
    persisted_contract = build_operational_approval_contract(
        issue=persisted[0],
        action=persisted[1],
        connection=persisted[2],
    )

    assert compact_contract.before_snapshot["confidence"] == "1"
    assert compact_contract.decision_context["inventory"]["forecast_confidence"] == "1"
    assert compact_contract.action_hash == persisted_contract.action_hash


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("unsupported_action", "recommended action is not executable"),
        ("profitability_block", "blocked by deterministic profitability policy"),
        ("inactive_connection", "active Amazon connection"),
        ("wrong_marketplace", "does not serve the issue marketplace"),
        ("non_integer_quantity", "deterministic integer"),
    ],
)
def test_execution_contract_fails_closed_for_unsupported_context(mutation: str, message: str) -> None:
    issue, action, connection = _resources()
    if mutation == "unsupported_action":
        action = replace(action, action_type="update_listing_title")
    elif mutation == "profitability_block":
        action = replace(action, parameters={**action.parameters, "replenishment_blocked": True})
    elif mutation == "inactive_connection":
        connection = replace(connection, status=ConnectionStatus.REVOKED)
    elif mutation == "wrong_marketplace":
        connection = replace(connection, marketplace_ids=("A1F83G8C2ARO7P",))
    else:
        action = replace(
            action,
            parameters={
                **action.parameters,
                "recommended_reorder_quantity": "600",
            },
        )

    with pytest.raises(ValueError, match=message):
        build_operational_approval_contract(issue=issue, action=action, connection=connection)
