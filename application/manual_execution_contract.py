from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from domain.models import ChannelConnection, ChannelProvider, ConnectionStatus, OperationalIssue, RecommendedAction
from safety.policy_engine import canonical_action_hash

EXECUTION_CONTRACT_VERSION = "manual-execution-v1"
APPROVAL_ACTION = "manual_execute_recommended_action"


class ExecutionContractModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ReplenishmentExecutionChanges(ExecutionContractModel):
    inbound_quantity: int = Field(strict=True, gt=0, le=10_000_000)


class ApprovedManualExecutionPlan(ExecutionContractModel):
    execution_contract_version: Literal["manual-execution-v1"] = EXECUTION_CONTRACT_VERSION
    connection_id: str = Field(min_length=1, max_length=100)
    issue_id: str = Field(min_length=1, max_length=100)
    recommended_action_id: str = Field(min_length=1, max_length=100)
    recommended_action_type: Literal["review_replenishment"]
    action_type: Literal["create_replenishment_plan"]
    changes: ReplenishmentExecutionChanges


class OperationalApprovalContract(ExecutionContractModel):
    before_snapshot: dict[str, Any]
    proposed_changes: dict[str, Any]
    evidence: list[dict[str, Any]]
    expected_outcome: str
    decision_context: dict[str, Any]
    execution_plan: ApprovedManualExecutionPlan
    action_hash: str = Field(min_length=64, max_length=64)

    def approval_payload(self) -> dict[str, Any]:
        plan = self.execution_plan.model_dump(mode="json")
        return {
            "connection_id": plan["connection_id"],
            "execution_contract_version": plan["execution_contract_version"],
            "execution_plan": plan,
            "plan": {"action": APPROVAL_ACTION, "arguments": plan},
            "before_snapshot": self.before_snapshot,
            "proposed_changes": self.proposed_changes,
            "evidence": self.evidence,
            "expected_outcome": self.expected_outcome,
            "decision_context": self.decision_context,
            "action_hash": self.action_hash,
        }


def build_operational_approval_contract(
    *,
    issue: OperationalIssue,
    action: RecommendedAction,
    connection: ChannelConnection,
    snapshot_status: str | None = None,
) -> OperationalApprovalContract:
    execution_plan = build_manual_execution_plan(issue=issue, action=action, connection=connection)
    before_snapshot = _before_snapshot(issue, status=snapshot_status or issue.status)
    proposed_changes = json_safe(action.parameters)
    evidence = json_safe(list(action.evidence))
    expected_outcome = action.expected_outcome
    decision_context = _decision_context(issue=issue, action=action)
    hash_arguments = {
        "issue_id": issue.id,
        "recommended_action_id": action.id,
        "before_snapshot": before_snapshot,
        "proposed_changes": proposed_changes,
        "evidence": evidence,
        "expected_outcome": expected_outcome,
        "decision_context": decision_context,
        "execution_plan": execution_plan.model_dump(mode="json"),
    }
    action_hash = canonical_action_hash(
        action=APPROVAL_ACTION,
        tenant_id=issue.tenant_id,
        resource_id=action.id,
        arguments=hash_arguments,
    )
    return OperationalApprovalContract(
        before_snapshot=before_snapshot,
        proposed_changes=proposed_changes,
        evidence=evidence,
        expected_outcome=expected_outcome,
        decision_context=decision_context,
        execution_plan=execution_plan,
        action_hash=action_hash,
    )


def validate_operational_approval_payload(
    *,
    payload: dict[str, Any],
    issue: OperationalIssue,
    action: RecommendedAction,
    connection: ChannelConnection,
) -> OperationalApprovalContract:
    stored_before = payload.get("before_snapshot")
    if not isinstance(stored_before, dict):
        raise ValueError("approval before_snapshot is missing")
    snapshot_status = str(stored_before.get("status") or "")
    if not snapshot_status:
        raise ValueError("approval before_snapshot status is missing")
    expected = build_operational_approval_contract(
        issue=issue,
        action=action,
        connection=connection,
        snapshot_status=snapshot_status,
    )
    expected_payload = expected.approval_payload()
    for key in (
        "connection_id",
        "execution_contract_version",
        "execution_plan",
        "plan",
        "before_snapshot",
        "proposed_changes",
        "evidence",
        "expected_outcome",
        "decision_context",
        "action_hash",
    ):
        if payload.get(key) != expected_payload[key]:
            raise ValueError(f"approval {key} no longer matches the current execution contract")
    return expected


def build_manual_execution_plan(
    *,
    issue: OperationalIssue,
    action: RecommendedAction,
    connection: ChannelConnection,
) -> ApprovedManualExecutionPlan:
    if issue.tenant_id != action.tenant_id or issue.tenant_id != connection.tenant_id:
        raise ValueError("execution plan resources must belong to one tenant")
    if action.issue_id != issue.id:
        raise ValueError("recommended action does not belong to the issue")
    if connection.provider != ChannelProvider.AMAZON or connection.status != ConnectionStatus.ACTIVE:
        raise ValueError("execution plan requires an active Amazon connection")
    if issue.marketplace_id not in connection.marketplace_ids:
        raise ValueError("execution connection does not serve the issue marketplace")
    if action.action_type != "review_replenishment":
        raise ValueError(f"recommended action is not executable: {action.action_type}")
    if action.parameters.get("replenishment_blocked") is True:
        raise ValueError("replenishment is blocked by deterministic profitability policy")
    if action.parameters.get("seller_sku") != issue.seller_sku:
        raise ValueError("recommended action seller_sku does not match the issue")
    if action.parameters.get("marketplace_id") != issue.marketplace_id:
        raise ValueError("recommended action marketplace does not match the issue")
    quantity = action.parameters.get("recommended_reorder_quantity")
    if isinstance(quantity, bool) or not isinstance(quantity, int):
        raise ValueError("recommended reorder quantity must be a deterministic integer")
    return ApprovedManualExecutionPlan(
        connection_id=connection.id,
        issue_id=issue.id,
        recommended_action_id=action.id,
        recommended_action_type="review_replenishment",
        action_type="create_replenishment_plan",
        changes=ReplenishmentExecutionChanges(inbound_quantity=quantity),
    )


def _before_snapshot(issue: OperationalIssue, *, status: str) -> dict[str, Any]:
    return json_safe(
        {
            "issue_id": issue.id,
            "seller_sku": issue.seller_sku,
            "marketplace_id": issue.marketplace_id,
            "status": status,
            "severity": issue.severity,
            "confidence": issue.confidence,
            "data_freshness_at": issue.data_freshness_at.isoformat(),
            "evidence": list(issue.evidence),
            "missing_data": list(issue.missing_data),
        }
    )


def _decision_context(*, issue: OperationalIssue, action: RecommendedAction) -> dict[str, Any]:
    evidence = [item for item in issue.evidence if isinstance(item, dict)]
    inventory = next((item for item in evidence if item.get("snapshot_type") == "inventory_health"), {})
    provenance = next((item for item in evidence if "verified_amazon_data" in item or "data_source" in item), {})
    return json_safe(
        {
            "inventory": {
                "sellable": inventory.get("available"),
                "reserved": inventory.get("reserved"),
                "inbound": inventory.get("inbound"),
                "in_transit": inventory.get("in_transit"),
                "unsellable": inventory.get("unsellable"),
                "inventory_position": inventory.get("inventory_position"),
                "days_of_supply": inventory.get("days_of_supply"),
                "total_lead_time_days": inventory.get("total_lead_time_days"),
                "weighted_daily_sales": inventory.get("weighted_daily_sales"),
                "forecast_confidence": inventory.get("forecast_confidence") or issue.confidence,
            },
            "recommendation": {
                "action_type": action.action_type,
                "recommended_reorder_quantity": action.parameters.get("recommended_reorder_quantity"),
                "recommended_shipping_mode": action.parameters.get("recommended_shipping_mode"),
                "reason": action.description,
                "expected_outcome": action.expected_outcome,
                "alternatives": ["defer_and_refresh_evidence", "manual_review_without_execution"],
            },
            "economics": {
                "currency": action.currency or issue.currency,
                "estimated_revenue_impact": action.expected_revenue_impact or issue.estimated_revenue_impact,
                "estimated_profit_impact": action.expected_profit_impact or issue.estimated_profit_impact,
                "estimated_cost_impact": action.expected_cost_impact or issue.estimated_cost_impact,
            },
            "provenance": {
                "source": provenance.get("data_source") or inventory.get("inventory_source") or "unknown",
                "verified_amazon_data": bool(provenance.get("verified_amazon_data", False)),
                "observed_at": provenance.get("observed_at")
                or inventory.get("inventory_collected_at")
                or issue.data_freshness_at,
                "freshness_at": issue.data_freshness_at,
            },
            "missing_data": list(issue.missing_data),
        }
    )


def json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Decimal):
        normalized = value.normalize()
        return "0" if normalized == 0 else format(normalized, "f")
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, tuple | list):
        return [json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): json_safe(item) for key, item in value.items()}
    raise TypeError(f"Unsupported execution contract value: {type(value).__name__}")
