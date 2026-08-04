from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from hashlib import sha256

from domain.models import DailyOperationsBrief, OperationalIssue, RecommendedAction, SKUHealthSummary, new_id
from domain.operations import SEVERITY_ORDER


@dataclass(frozen=True)
class ListingBasicSignal:
    tenant_id: str
    listing_id: str
    seller_sku: str
    marketplace_id: str
    status: str
    title: str | None
    price_amount: Decimal | None
    price_currency: str | None
    external_product_id: str | None
    observed_at: datetime | None


@dataclass(frozen=True)
class BriefBuildResult:
    brief: DailyOperationsBrief
    sku_health: tuple[SKUHealthSummary, ...]


class ListingIssueEvaluator:
    def evaluate(
        self, signal: ListingBasicSignal, *, assessed_at: datetime
    ) -> tuple[OperationalIssue, RecommendedAction] | None:
        if assessed_at.tzinfo is None:
            raise ValueError("assessed_at must be timezone-aware")
        missing: list[str] = []
        if not signal.title or not signal.title.strip():
            missing.append("title")
        if signal.price_amount is None or not signal.price_currency:
            missing.append("price")
        if not signal.external_product_id:
            missing.append("external_product_id")
        if signal.observed_at is None:
            missing.append("listing_observed_at")
        unhealthy_status = signal.status.lower() in {"suppressed", "inactive", "error", "invalid", "deleted"}
        if not missing and not unhealthy_status:
            return None
        severity = "high" if unhealthy_status else "medium"
        confidence = Decimal("0.40") if signal.observed_at is None else Decimal("0.80")
        degraded_reason = "listing_freshness_unknown" if signal.observed_at is None else None
        issue_id = new_id()
        dedupe_key = _dedupe(signal.tenant_id, "listing_basic_quality", signal.marketplace_id, signal.seller_sku)
        evidence = (
            {
                "source_type": "listing_record",
                "listing_id": signal.listing_id,
                "status": signal.status,
                "has_title": bool(signal.title and signal.title.strip()),
                "has_price": signal.price_amount is not None and bool(signal.price_currency),
                "has_external_product_id": bool(signal.external_product_id),
                "observed_at": signal.observed_at.isoformat() if signal.observed_at else None,
            },
        )
        issue = OperationalIssue(
            id=issue_id,
            tenant_id=signal.tenant_id,
            seller_sku=signal.seller_sku,
            marketplace_id=signal.marketplace_id,
            issue_type="listing_basic_quality",
            severity=severity,
            title=f"{signal.seller_sku} listing requires review",
            summary="Listing status or required commercial fields are incomplete.",
            status="open",
            dedupe_key=dedupe_key,
            confidence=confidence,
            data_freshness_at=signal.observed_at or assessed_at,
            evidence=evidence,
            missing_data=tuple(missing),
            degraded_reason=degraded_reason,
            detected_at=assessed_at,
            last_detected_at=assessed_at,
            currency=signal.price_currency if signal.price_currency and len(signal.price_currency) == 3 else None,
        )
        action = RecommendedAction(
            tenant_id=signal.tenant_id,
            issue_id=issue.id,
            action_type="review_listing_quality",
            title="Review listing quality and status",
            description="Verify listing status, title, price and Amazon identifier before preparing any content update.",
            execution_mode="recommendation_only",
            risk_level=severity,
            status="proposed",
            parameters={"listing_id": signal.listing_id, "seller_sku": signal.seller_sku, "missing_fields": missing},
            evidence=evidence,
            expected_outcome="Restore a complete, reviewable listing record without automatic publication.",
            confidence=confidence,
            automation_eligible=False,
            dedupe_key=_dedupe(signal.tenant_id, dedupe_key, "review_listing_quality"),
            created_at=assessed_at,
            updated_at=assessed_at,
        )
        return issue, action


class DailyOperationsBriefBuilder:
    def build(
        self,
        *,
        tenant_id: str,
        brief_date: date,
        timezone: str,
        generated_at: datetime,
        issues: Iterable[OperationalIssue],
        actions: Iterable[RecommendedAction],
    ) -> BriefBuildResult:
        if generated_at.tzinfo is None:
            raise ValueError("generated_at must be timezone-aware")
        active_issues = [
            issue for issue in issues if issue.tenant_id == tenant_id and issue.status not in {"resolved", "dismissed"}
        ]
        active_actions = [
            action for action in actions if action.tenant_id == tenant_id and action.status in {"proposed", "accepted"}
        ]
        active_issues.sort(
            key=lambda issue: (
                SEVERITY_ORDER[issue.severity],
                -_impact_score(issue),
                -issue.last_detected_at.timestamp(),
            )
        )
        action_by_issue = {action.issue_id: action for action in active_actions}
        issue_counts = {severity: 0 for severity in ("critical", "high", "medium", "low")}
        domain_counts = {domain: 0 for domain in ("profit", "inventory", "listing", "data_quality", "other")}
        impact_by_currency: dict[str, dict[str, Decimal]] = {}
        unallocated = 0
        for issue in active_issues:
            issue_counts[issue.severity] += 1
            domain_counts[_domain(issue.issue_type)] += 1
            if issue.currency:
                bucket = impact_by_currency.setdefault(
                    issue.currency, {"revenue": Decimal("0"), "profit": Decimal("0"), "cost": Decimal("0")}
                )
                bucket["revenue"] += abs(issue.estimated_revenue_impact or Decimal("0"))
                bucket["profit"] += abs(issue.estimated_profit_impact or Decimal("0"))
                bucket["cost"] += abs(issue.estimated_cost_impact or Decimal("0"))
            else:
                unallocated += 1
        sku_health = self._sku_health(tenant_id=tenant_id, issues=active_issues, action_by_issue=action_by_issue)
        priority_issues = active_issues[:20]
        priority_actions = tuple(
            action_by_issue[issue.id].id for issue in priority_issues if issue.id in action_by_issue
        )
        serialized_impacts = {
            currency: {name: str(value) for name, value in values.items()}
            for currency, values in sorted(impact_by_currency.items())
        }
        brief = DailyOperationsBrief(
            tenant_id=tenant_id,
            brief_date=brief_date,
            timezone=timezone,
            generated_at=generated_at,
            issue_counts=issue_counts,
            domain_counts=domain_counts,
            total_open_issues=len(active_issues),
            data_quality_issue_count=sum(
                1 for issue in active_issues if "data_quality" in issue.issue_type or issue.degraded_reason
            ),
            impact_by_currency=serialized_impacts,
            unallocated_impact_issue_count=unallocated,
            priority_issue_ids=tuple(issue.id for issue in priority_issues),
            priority_action_ids=priority_actions,
            sku_health=sku_health,
            source_manifest={
                "operational_issue_count": len(active_issues),
                "recommended_action_count": len(active_actions),
                "generated_from_persisted_records": True,
            },
        )
        return BriefBuildResult(brief=brief, sku_health=sku_health)

    @staticmethod
    def _sku_health(
        *,
        tenant_id: str,
        issues: list[OperationalIssue],
        action_by_issue: dict[str, RecommendedAction],
    ) -> tuple[SKUHealthSummary, ...]:
        grouped: dict[tuple[str, str], list[OperationalIssue]] = {}
        for issue in issues:
            grouped.setdefault((issue.marketplace_id, issue.seller_sku), []).append(issue)
        results: list[SKUHealthSummary] = []
        for (marketplace_id, seller_sku), sku_issues in grouped.items():
            sku_issues.sort(
                key=lambda issue: (
                    SEVERITY_ORDER[issue.severity],
                    -_impact_score(issue),
                    -issue.last_detected_at.timestamp(),
                )
            )
            impact: dict[str, dict[str, Decimal]] = {}
            for issue in sku_issues:
                if not issue.currency:
                    continue
                bucket = impact.setdefault(
                    issue.currency, {"revenue": Decimal("0"), "profit": Decimal("0"), "cost": Decimal("0")}
                )
                bucket["revenue"] += abs(issue.estimated_revenue_impact or Decimal("0"))
                bucket["profit"] += abs(issue.estimated_profit_impact or Decimal("0"))
                bucket["cost"] += abs(issue.estimated_cost_impact or Decimal("0"))
            highest = sku_issues[0].severity
            results.append(
                SKUHealthSummary(
                    tenant_id=tenant_id,
                    seller_sku=seller_sku,
                    marketplace_id=marketplace_id,
                    health_status=highest,
                    highest_severity=highest,
                    open_issue_count=len(sku_issues),
                    issue_types=tuple(dict.fromkeys(issue.issue_type for issue in sku_issues)),
                    recommended_action_ids=tuple(
                        action_by_issue[issue.id].id for issue in sku_issues if issue.id in action_by_issue
                    ),
                    impact_by_currency={
                        currency: {name: str(value) for name, value in values.items()}
                        for currency, values in sorted(impact.items())
                    },
                    has_data_quality_risk=any(
                        "data_quality" in issue.issue_type or issue.degraded_reason for issue in sku_issues
                    ),
                    last_detected_at=max(issue.last_detected_at for issue in sku_issues),
                )
            )
        results.sort(key=lambda item: (SEVERITY_ORDER[item.highest_severity], -item.open_issue_count, item.seller_sku))
        return tuple(results)


def _domain(issue_type: str) -> str:
    if issue_type.startswith("inventory_"):
        return "inventory"
    if issue_type.startswith("sku_contribution") or issue_type.startswith("profit_"):
        return "profit"
    if issue_type.startswith("listing_"):
        return "listing"
    if "data_quality" in issue_type:
        return "data_quality"
    return "other"


def _impact_score(issue: OperationalIssue) -> Decimal:
    return sum(
        (
            abs(value)
            for value in (issue.estimated_revenue_impact, issue.estimated_profit_impact, issue.estimated_cost_impact)
            if value is not None
        ),
        Decimal("0"),
    )


def _dedupe(*parts: str) -> str:
    return sha256("|".join(parts).encode("utf-8")).hexdigest()
