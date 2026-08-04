from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from hashlib import sha256

from domain.models import InventoryHealthSnapshot, OperationalIssue, RecommendedAction, SKUProfitSnapshot, new_id

SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}


@dataclass(frozen=True)
class IssueDetectionResult:
    issues: tuple[OperationalIssue, ...]
    actions: tuple[RecommendedAction, ...]


class OperationalIssueDetector:
    def detect(
        self,
        *,
        tenant_id: str,
        inventory_snapshots: Iterable[InventoryHealthSnapshot],
        profit_snapshots: Iterable[SKUProfitSnapshot],
        detected_at: datetime,
    ) -> IssueDetectionResult:
        if detected_at.tzinfo is None:
            raise ValueError("detected_at must be timezone-aware")
        issues: list[OperationalIssue] = []
        actions: list[RecommendedAction] = []
        for snapshot in inventory_snapshots:
            self._require_tenant(tenant_id, snapshot.tenant_id)
            inventory_issues = self._inventory_issues(snapshot=snapshot, detected_at=detected_at)
            issues.extend(inventory_issues)
            actions.extend(self._action_for_issue(issue, snapshot, detected_at) for issue in inventory_issues)
        for snapshot in profit_snapshots:
            self._require_tenant(tenant_id, snapshot.tenant_id)
            profit_issues = self._profit_issues(snapshot=snapshot, detected_at=detected_at)
            issues.extend(profit_issues)
            actions.extend(self._action_for_issue(issue, snapshot, detected_at) for issue in profit_issues)
        issues.sort(
            key=lambda item: (
                SEVERITY_ORDER[item.severity],
                -self._impact_score(item),
                item.issue_type,
                item.seller_sku,
            )
        )
        actions_by_issue = {action.issue_id: action for action in actions}
        sorted_actions = tuple(actions_by_issue[issue.id] for issue in issues)
        return IssueDetectionResult(issues=tuple(issues), actions=sorted_actions)

    def _inventory_issues(self, *, snapshot: InventoryHealthSnapshot, detected_at: datetime) -> list[OperationalIssue]:
        issues: list[OperationalIssue] = []
        evidence_items = [
            {
                "snapshot_type": "inventory_health",
                "snapshot_id": snapshot.id,
                "calculation_version": snapshot.calculation_version,
                "inventory_source": snapshot.inventory_source,
                "inventory_collected_at": snapshot.inventory_collected_at.isoformat(),
                "days_of_supply": str(snapshot.days_of_supply) if snapshot.days_of_supply is not None else None,
                "total_lead_time_days": snapshot.total_lead_time_days,
                "recommended_reorder_quantity": snapshot.recommended_reorder_quantity,
                "forecast_confidence": str(snapshot.forecast_confidence),
                "available": snapshot.available,
                "reserved": snapshot.reserved,
                "inbound": snapshot.inbound,
                "in_transit": snapshot.in_transit,
                "unsellable": snapshot.unsellable,
                "inventory_position": snapshot.inventory_position,
                "weighted_daily_sales": str(snapshot.weighted_daily_sales)
                if snapshot.weighted_daily_sales is not None
                else None,
            }
        ]
        platform_metadata = snapshot.input_manifest.get("platform_metadata")
        if isinstance(platform_metadata, dict):
            evidence_items.append(platform_metadata)
        evidence = tuple(evidence_items)
        has_stockout_signal = (
            snapshot.stockout_risk in {"critical", "high", "medium"} or snapshot.inventory_health == "stockout_risk"
        )
        has_excess_signal = snapshot.inventory_health == "excess_inventory" or snapshot.excess_risk in {
            "critical",
            "high",
            "medium",
        }
        if has_stockout_signal and has_excess_signal:
            return [
                self._issue(
                    tenant_id=snapshot.tenant_id,
                    seller_sku=snapshot.seller_sku,
                    marketplace_id=snapshot.marketplace_id,
                    issue_type="inventory_signal_conflict",
                    severity="high",
                    title=f"{snapshot.seller_sku} has conflicting inventory signals",
                    summary="The latest deterministic snapshot reports both stockout and excess conditions; commercial recommendations are blocked pending reconciliation.",
                    confidence=Decimal("0"),
                    data_freshness_at=snapshot.inventory_collected_at,
                    evidence=evidence,
                    missing_data=("inventory_signal_reconciliation",),
                    degraded_reason="conflicting_inventory_signals",
                    detected_at=detected_at,
                )
            ]
        if has_stockout_signal:
            severity = self._stockout_severity(snapshot)
            issues.append(
                self._issue(
                    tenant_id=snapshot.tenant_id,
                    seller_sku=snapshot.seller_sku,
                    marketplace_id=snapshot.marketplace_id,
                    issue_type="inventory_stockout_risk",
                    severity=severity,
                    title=f"{snapshot.seller_sku} has stockout risk",
                    summary="Inventory coverage is below the replenishment lead-time requirement.",
                    confidence=snapshot.forecast_confidence,
                    data_freshness_at=snapshot.inventory_collected_at,
                    evidence=evidence,
                    missing_data=snapshot.missing_data,
                    degraded_reason=snapshot.degraded_reason,
                    detected_at=detected_at,
                    currency=None,
                    estimated_revenue_impact=snapshot.estimated_revenue_at_risk,
                    estimated_profit_impact=snapshot.estimated_profit_at_risk,
                    estimated_cost_impact=snapshot.estimated_extra_logistics_cost,
                )
            )
        if has_excess_signal:
            severity = "high" if snapshot.excess_risk in {"critical", "high"} else "medium"
            issues.append(
                self._issue(
                    tenant_id=snapshot.tenant_id,
                    seller_sku=snapshot.seller_sku,
                    marketplace_id=snapshot.marketplace_id,
                    issue_type="inventory_excess",
                    severity=severity,
                    title=f"{snapshot.seller_sku} has excess inventory",
                    summary="Inventory exceeds the configured target coverage and may increase storage or aging cost.",
                    confidence=snapshot.forecast_confidence,
                    data_freshness_at=snapshot.inventory_collected_at,
                    evidence=evidence,
                    missing_data=snapshot.missing_data,
                    degraded_reason=snapshot.degraded_reason,
                    detected_at=detected_at,
                    currency=None,
                    estimated_cost_impact=snapshot.estimated_extra_logistics_cost,
                )
            )
        quality_degraded = snapshot.degraded_reason in {
            "inventory_inputs_not_automation_safe",
            "low_confidence_inventory_source",
            "inventory_stale",
        }
        if quality_degraded or snapshot.missing_data or snapshot.data_warnings:
            severity = (
                "high"
                if snapshot.degraded_reason == "inventory_stale" or "inventory_stale" in snapshot.data_warnings
                else "medium"
            )
            issues.append(
                self._issue(
                    tenant_id=snapshot.tenant_id,
                    seller_sku=snapshot.seller_sku,
                    marketplace_id=snapshot.marketplace_id,
                    issue_type="inventory_data_quality",
                    severity=severity,
                    title=f"{snapshot.seller_sku} inventory evidence is degraded",
                    summary="Inventory recommendations must remain manual until missing or stale evidence is refreshed.",
                    confidence=snapshot.forecast_confidence,
                    data_freshness_at=snapshot.inventory_collected_at,
                    evidence=evidence,
                    missing_data=snapshot.missing_data,
                    degraded_reason=snapshot.degraded_reason,
                    detected_at=detected_at,
                )
            )
        return issues

    def _profit_issues(self, *, snapshot: SKUProfitSnapshot, detected_at: datetime) -> list[OperationalIssue]:
        issues: list[OperationalIssue] = []
        evidence_items = [
            {
                "snapshot_type": "sku_profit",
                "snapshot_id": snapshot.id,
                "calculation_version": snapshot.calculation_version,
                "window_start": snapshot.window_start.isoformat(),
                "window_end": snapshot.window_end.isoformat(),
                "revenue": str(snapshot.revenue),
                "contribution_profit": str(snapshot.contribution_profit)
                if snapshot.contribution_profit is not None
                else None,
                "contribution_margin": str(snapshot.contribution_margin)
                if snapshot.contribution_margin is not None
                else None,
                "confidence": str(snapshot.confidence),
                "estimated": snapshot.estimated,
            }
        ]
        platform_metadata = snapshot.input_manifest.get("platform_metadata")
        if isinstance(platform_metadata, dict):
            evidence_items.append(platform_metadata)
        evidence = tuple(evidence_items)
        if snapshot.contribution_profit is not None and snapshot.contribution_profit < 0:
            severity = (
                "critical"
                if snapshot.contribution_margin is not None and snapshot.contribution_margin <= Decimal("-0.10")
                else "high"
            )
            issues.append(
                self._issue(
                    tenant_id=snapshot.tenant_id,
                    seller_sku=snapshot.seller_sku,
                    marketplace_id=snapshot.marketplace_id,
                    issue_type="sku_contribution_loss",
                    severity=severity,
                    title=f"{snapshot.seller_sku} is contribution-loss making",
                    summary="Deterministic contribution profit is below zero for the measured window.",
                    confidence=snapshot.confidence,
                    data_freshness_at=snapshot.calculated_at,
                    evidence=evidence,
                    missing_data=snapshot.missing_inputs,
                    degraded_reason=snapshot.degraded_reason,
                    detected_at=detected_at,
                    currency=snapshot.currency,
                    estimated_profit_impact=abs(snapshot.contribution_profit),
                )
            )
        if snapshot.degraded_reason or snapshot.missing_inputs:
            issues.append(
                self._issue(
                    tenant_id=snapshot.tenant_id,
                    seller_sku=snapshot.seller_sku,
                    marketplace_id=snapshot.marketplace_id,
                    issue_type="profit_data_quality",
                    severity="high" if snapshot.contribution_profit is None else "medium",
                    title=f"{snapshot.seller_sku} profit evidence is incomplete",
                    summary="Profit conclusions are degraded because required cost or fee evidence is missing or stale.",
                    confidence=snapshot.confidence,
                    data_freshness_at=snapshot.calculated_at,
                    evidence=evidence,
                    missing_data=snapshot.missing_inputs,
                    degraded_reason=snapshot.degraded_reason,
                    detected_at=detected_at,
                    currency=snapshot.currency,
                )
            )
        return issues

    def _action_for_issue(
        self, issue: OperationalIssue, snapshot: InventoryHealthSnapshot | SKUProfitSnapshot, created_at: datetime
    ) -> RecommendedAction:
        action_id = new_id()
        if issue.issue_type == "inventory_stockout_risk":
            negative_unit_profit = snapshot.degraded_reason == "negative_unit_contribution_profit"
            action_type = "review_loss_making_stockout" if negative_unit_profit else "review_replenishment"
            title = (
                "Resolve contribution loss before replenishment"
                if negative_unit_profit
                else "Review replenishment plan"
            )
            description = (
                "Do not replenish automatically. Validate the loss drivers and decide whether price, cost, advertising or assortment action should precede any purchase order."
                if negative_unit_profit
                else "Validate demand distortions, inbound stock and supplier lead time before creating a purchase or transfer task."
            )
            parameters = {
                "seller_sku": issue.seller_sku,
                "marketplace_id": issue.marketplace_id,
                "recommended_reorder_quantity": snapshot.recommended_reorder_quantity,
                "recommended_shipping_mode": snapshot.recommended_shipping_mode,
                "replenishment_blocked": negative_unit_profit,
            }
            expected_outcome = (
                "Prevent additional profit loss and loss-making inventory while preserving an evidence-based availability decision."
                if negative_unit_profit
                else "Restore safe inventory coverage without breaching profit, cash or evidence guardrails."
            )
            risk_level = "high"
        elif issue.issue_type == "inventory_excess":
            action_type = "review_excess_inventory"
            title = "Review excess inventory mitigation"
            description = (
                "Pause replenishment and evaluate compliant sell-through options without breaching the profit floor."
            )
            parameters = {
                "seller_sku": issue.seller_sku,
                "marketplace_id": issue.marketplace_id,
                "excess_units": snapshot.excess_units,
            }
            expected_outcome = "Reduce excess stock exposure without breaching the profit floor."
            risk_level = "medium"
        elif issue.issue_type == "inventory_signal_conflict":
            action_type = "reconcile_inventory_signals"
            title = "Reconcile contradictory inventory evidence"
            description = "Refresh and compare sellable, reserved, inbound and demand evidence before making any replenishment or excess-stock decision."
            parameters = {
                "seller_sku": issue.seller_sku,
                "marketplace_id": issue.marketplace_id,
                "commercial_actions_blocked": True,
            }
            expected_outcome = (
                "Restore one internally consistent inventory state before any commercial action is proposed."
            )
            risk_level = "high"
        elif issue.issue_type == "inventory_data_quality":
            action_type = "refresh_inventory_data"
            title = "Refresh inventory evidence"
            description = "Reconcile Amazon inventory, sales windows and inbound quantities before acting on replenishment advice."
            parameters = {
                "seller_sku": issue.seller_sku,
                "marketplace_id": issue.marketplace_id,
                "missing_data": list(issue.missing_data),
            }
            expected_outcome = "Restore complete, fresh inventory evidence before a replenishment decision."
            risk_level = "low"
        elif issue.issue_type == "sku_contribution_loss":
            action_type = "review_profit_levers"
            title = "Review price, advertising and cost levers"
            description = "Validate fees and costs, then prepare a profit-constrained remediation proposal; do not change price automatically."
            parameters = {"seller_sku": issue.seller_sku, "marketplace_id": issue.marketplace_id}
            expected_outcome = (
                "Stop contribution loss through a human-reviewed price, cost, advertising or assortment plan."
            )
            risk_level = "high"
        else:
            action_type = "refresh_profit_data"
            title = "Refresh profit evidence"
            description = (
                "Complete missing cost and fee inputs before generating pricing or advertising recommendations."
            )
            parameters = {
                "seller_sku": issue.seller_sku,
                "marketplace_id": issue.marketplace_id,
                "missing_data": list(issue.missing_data),
            }
            expected_outcome = "Restore complete profit evidence before making a commercial decision."
            risk_level = "low"
        return RecommendedAction(
            id=action_id,
            tenant_id=issue.tenant_id,
            issue_id=issue.id,
            action_type=action_type,
            title=title,
            description=description,
            execution_mode="recommendation_only",
            risk_level=risk_level,
            status="proposed",
            parameters=parameters,
            evidence=issue.evidence,
            expected_outcome=expected_outcome,
            confidence=issue.confidence,
            automation_eligible=False,
            dedupe_key=self._dedupe(issue.tenant_id, issue.dedupe_key, action_type),
            created_at=created_at,
            updated_at=created_at,
            currency=issue.currency,
            expected_revenue_impact=issue.estimated_revenue_impact,
            expected_profit_impact=issue.estimated_profit_impact,
            expected_cost_impact=issue.estimated_cost_impact,
        )

    def _issue(self, **values) -> OperationalIssue:
        values["dedupe_key"] = self._dedupe(
            values["tenant_id"], values["issue_type"], values["marketplace_id"], values["seller_sku"]
        )
        values["status"] = "open"
        values["last_detected_at"] = values["detected_at"]
        return OperationalIssue(**values)

    @staticmethod
    def _stockout_severity(snapshot: InventoryHealthSnapshot) -> str:
        if snapshot.stockout_risk == "critical" or (
            snapshot.days_of_supply is not None and snapshot.days_of_supply <= Decimal("7")
        ):
            return "critical"
        if snapshot.stockout_risk == "high" or (
            snapshot.days_of_supply is not None and snapshot.days_of_supply <= snapshot.total_lead_time_days
        ):
            return "high"
        return "medium"

    @staticmethod
    def _impact_score(issue: OperationalIssue) -> Decimal:
        return sum(
            (
                abs(value)
                for value in (
                    issue.estimated_revenue_impact,
                    issue.estimated_profit_impact,
                    issue.estimated_cost_impact,
                )
                if value is not None
            ),
            Decimal("0"),
        )

    @staticmethod
    def _dedupe(*parts: str) -> str:
        return sha256("|".join(parts).encode("utf-8")).hexdigest()

    @staticmethod
    def _require_tenant(expected: str, actual: str) -> None:
        if expected != actual:
            raise ValueError("snapshot tenant does not match detection tenant")
