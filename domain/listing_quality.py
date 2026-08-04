from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from domain.models import new_id
from safety.policy_engine import canonical_action_hash

SCORE = Decimal("0.01")
RATE = Decimal("0.0001")


@dataclass(frozen=True)
class ListingQualityInput:
    tenant_id: str
    listing_id: str
    seller_sku: str
    marketplace_id: str
    content_version: int
    title: str
    bullet_points: tuple[str, ...]
    description: str
    image_count: int
    search_terms: tuple[str, ...]
    status: str
    source: str
    source_observed_at: datetime | None
    calculated_at: datetime
    sessions: int | None = None
    units_ordered: int | None = None
    conversion_rate: Decimal | None = None
    return_rate: Decimal | None = None
    ad_spend: Decimal | None = None
    ad_traffic_share: Decimal | None = None
    cpc: Decimal | None = None
    price: Decimal | None = None
    discount_rate: Decimal | None = None
    buy_box_share: Decimal | None = None
    sellable_units: int | None = None
    out_of_stock_hours: Decimal | None = None
    natural_rank: Decimal | None = None
    promotion_active: bool | None = None
    seasonality_key: str | None = None
    competitor_change: bool | None = None

    def __post_init__(self) -> None:
        if self.calculated_at.tzinfo is None or (self.source_observed_at and self.source_observed_at.tzinfo is None):
            raise ValueError("listing timestamps must be timezone-aware")
        if self.content_version <= 0 or self.image_count < 0:
            raise ValueError("content version must be positive and image count cannot be negative")
        if self.sessions is not None and self.sessions < 0:
            raise ValueError("sessions cannot be negative")
        if self.units_ordered is not None and self.units_ordered < 0:
            raise ValueError("units ordered cannot be negative")
        for value in (self.conversion_rate, self.return_rate):
            if value is not None and (Decimal(str(value)) < 0 or Decimal(str(value)) > 1):
                raise ValueError("rates must be between 0 and 1")


@dataclass
class ListingQualitySnapshot:
    tenant_id: str
    listing_id: str
    seller_sku: str
    marketplace_id: str
    content_version: int
    title: str
    bullet_points: tuple[str, ...]
    description: str
    image_count: int
    search_terms: tuple[str, ...]
    listing_status: str
    quality_score: Decimal
    quality_status: str
    section_scores: dict[str, str]
    quality_issues: tuple[dict[str, Any], ...]
    recommendations: tuple[dict[str, Any], ...]
    evidence: tuple[dict[str, Any], ...]
    confidence: Decimal
    missing_data: tuple[str, ...]
    degraded_reason: str | None
    automation_eligible: bool
    source: str
    source_observed_at: datetime | None
    calculated_at: datetime
    calculation_version: str
    baseline_metrics: dict[str, str | int | None]
    id: str = field(default_factory=new_id)


@dataclass
class ListingOptimizationExperiment:
    tenant_id: str
    listing_id: str
    seller_sku: str
    marketplace_id: str
    before_snapshot_id: str
    proposed_changes: dict[str, Any]
    baseline_metrics: dict[str, str | int | None]
    observation_start: datetime
    observation_end: datetime
    expected_outcome: str
    action_hash: str
    status: str
    created_at: datetime
    updated_at: datetime
    id: str = field(default_factory=new_id)
    after_snapshot_id: str | None = None
    approval_request_id: str | None = None
    effect_metrics: dict[str, str] = field(default_factory=dict)
    evaluation_reasons: tuple[str, ...] = ()
    rollback_recommended: bool = False
    evaluated_at: datetime | None = None
    execution_status: str = "not_executed"
    actual_changes: dict[str, Any] = field(default_factory=dict)
    execution_evidence: dict[str, Any] = field(default_factory=dict)
    executed_by: str | None = None
    executed_at: datetime | None = None
    execution_notes: str | None = None
    decision: str | None = None
    decision_by: str | None = None
    decision_at: datetime | None = None
    decision_reason: str | None = None

    @classmethod
    def create(
        cls,
        *,
        tenant_id: str,
        listing_id: str,
        seller_sku: str,
        marketplace_id: str,
        before_snapshot: ListingQualitySnapshot,
        proposed_changes: dict[str, Any],
        observation_start: datetime,
        observation_end: datetime,
        created_at: datetime,
    ) -> ListingOptimizationExperiment:
        if observation_start.tzinfo is None or observation_end.tzinfo is None or created_at.tzinfo is None:
            raise ValueError("experiment timestamps must be timezone-aware")
        if observation_end <= observation_start:
            raise ValueError("observation_end must be after observation_start")
        if before_snapshot.tenant_id != tenant_id or before_snapshot.listing_id != listing_id:
            raise ValueError("before snapshot scope does not match experiment")
        action_hash = canonical_action_hash(
            action="publish_listing",
            tenant_id=tenant_id,
            resource_id=f"{marketplace_id}:{seller_sku}",
            arguments={
                "listing_id": listing_id,
                "before_snapshot_id": before_snapshot.id,
                "proposed_changes": proposed_changes,
                "observation_start": observation_start.isoformat(),
                "observation_end": observation_end.isoformat(),
            },
        )
        return cls(
            tenant_id=tenant_id,
            listing_id=listing_id,
            seller_sku=seller_sku,
            marketplace_id=marketplace_id,
            before_snapshot_id=before_snapshot.id,
            proposed_changes=proposed_changes,
            baseline_metrics=before_snapshot.baseline_metrics,
            observation_start=observation_start,
            observation_end=observation_end,
            expected_outcome="Improve Listing quality without materially worsening conversion or return rate.",
            action_hash=action_hash,
            status="awaiting_approval",
            created_at=created_at,
            updated_at=created_at,
        )


class ListingQualityEvaluator:
    calculation_version = "listing-quality-v1"

    def evaluate(self, input_data: ListingQualityInput) -> ListingQualitySnapshot:
        issues: list[dict[str, Any]] = []
        recommendations: list[dict[str, Any]] = []
        title_length = len(input_data.title.strip())
        if title_length < 40:
            title_score = Decimal("5") if title_length else Decimal("0")
            self._add(
                issues,
                recommendations,
                "title",
                "title_too_short",
                "Title is too short to communicate key product facts.",
            )
        elif title_length <= 200:
            title_score = Decimal("20")
        else:
            title_score = Decimal("10")
            self._add(
                issues,
                recommendations,
                "title",
                "title_too_long",
                "Title exceeds the controlled quality range and should be reviewed for readability.",
            )

        bullet_count = sum(1 for item in input_data.bullet_points if item.strip())
        if bullet_count >= 5:
            bullet_score = Decimal("25")
        elif bullet_count >= 3:
            bullet_score = Decimal("15")
            self._add(
                issues,
                recommendations,
                "bullets",
                "insufficient_bullets",
                "Add complete benefit, specification, care and usage bullets.",
            )
        else:
            bullet_score = Decimal("5") if bullet_count else Decimal("0")
            self._add(
                issues,
                recommendations,
                "bullets",
                "insufficient_bullets",
                "At least five substantive bullet points are recommended for review.",
            )

        description_length = len(input_data.description.strip())
        if description_length >= 500:
            description_score = Decimal("15")
        elif description_length >= 100:
            description_score = Decimal("8")
            self._add(
                issues,
                recommendations,
                "description",
                "description_too_short",
                "Expand materials, dimensions, intended use, care and package contents.",
            )
        else:
            description_score = Decimal("3") if description_length else Decimal("0")
            self._add(
                issues,
                recommendations,
                "description",
                "description_too_short",
                "Description lacks sufficient product and usage detail.",
            )

        if input_data.image_count >= 7:
            image_score = Decimal("15")
        elif input_data.image_count >= 3:
            image_score = Decimal("8")
            self._add(
                issues,
                recommendations,
                "images",
                "insufficient_images",
                "Review additional compliant product, dimension and usage images.",
            )
        else:
            image_score = Decimal("3") if input_data.image_count else Decimal("0")
            self._add(
                issues,
                recommendations,
                "images",
                "insufficient_images",
                "Image coverage is insufficient for product evaluation.",
            )

        term_count = len({item.strip().lower() for item in input_data.search_terms if item.strip()})
        if term_count >= 5:
            search_score = Decimal("10")
        elif term_count:
            search_score = Decimal("5")
            self._add(
                issues,
                recommendations,
                "search_terms",
                "insufficient_search_terms",
                "Review additional relevant search terms without keyword stuffing.",
            )
        else:
            search_score = Decimal("0")
            self._add(
                issues, recommendations, "search_terms", "missing_search_terms", "Search term evidence is missing."
            )

        if input_data.status.lower() == "active":
            status_score = Decimal("15")
        else:
            status_score = Decimal("0")
            self._add(
                issues,
                recommendations,
                "status",
                "listing_not_active",
                "Resolve platform validation or suppression issues before content experimentation.",
                severity="high",
            )

        section_scores = {
            "title": str(title_score),
            "bullets": str(bullet_score),
            "description": str(description_score),
            "images": str(image_score),
            "search_terms": str(search_score),
            "status": str(status_score),
        }
        total = sum((Decimal(value) for value in section_scores.values()), Decimal("0")).quantize(SCORE)
        quality_status = "critical" if total < 40 else "poor" if total < 60 else "fair" if total < 80 else "good"
        missing_data: list[str] = []
        if input_data.source_observed_at is None:
            missing_data.append("source_observed_at")
        confidence = self._confidence(input_data)
        degraded_reason = "listing_freshness_unknown" if input_data.source_observed_at is None else None
        evidence = (
            {
                "source": input_data.source,
                "source_observed_at": input_data.source_observed_at.isoformat()
                if input_data.source_observed_at
                else None,
                "content_version": input_data.content_version,
                "title_length": title_length,
                "bullet_count": bullet_count,
                "description_length": description_length,
                "image_count": input_data.image_count,
                "search_term_count": term_count,
                "listing_status": input_data.status,
            },
        )
        baseline_metrics = {
            "sessions": input_data.sessions,
            "units_ordered": input_data.units_ordered,
            "conversion_rate": str(input_data.conversion_rate) if input_data.conversion_rate is not None else None,
            "return_rate": str(input_data.return_rate) if input_data.return_rate is not None else None,
            "ad_spend": str(input_data.ad_spend) if input_data.ad_spend is not None else None,
            "ad_traffic_share": str(input_data.ad_traffic_share) if input_data.ad_traffic_share is not None else None,
            "cpc": str(input_data.cpc) if input_data.cpc is not None else None,
            "price": str(input_data.price) if input_data.price is not None else None,
            "discount_rate": str(input_data.discount_rate) if input_data.discount_rate is not None else None,
            "buy_box_share": str(input_data.buy_box_share) if input_data.buy_box_share is not None else None,
            "sellable_units": input_data.sellable_units,
            "out_of_stock_hours": str(input_data.out_of_stock_hours)
            if input_data.out_of_stock_hours is not None
            else None,
            "natural_rank": str(input_data.natural_rank) if input_data.natural_rank is not None else None,
            "promotion_active": input_data.promotion_active,
            "seasonality_key": input_data.seasonality_key,
            "competitor_change": input_data.competitor_change,
        }
        return ListingQualitySnapshot(
            tenant_id=input_data.tenant_id,
            listing_id=input_data.listing_id,
            seller_sku=input_data.seller_sku,
            marketplace_id=input_data.marketplace_id,
            content_version=input_data.content_version,
            title=input_data.title,
            bullet_points=input_data.bullet_points,
            description=input_data.description,
            image_count=input_data.image_count,
            search_terms=input_data.search_terms,
            listing_status=input_data.status,
            quality_score=total,
            quality_status=quality_status,
            section_scores=section_scores,
            quality_issues=tuple(issues),
            recommendations=tuple(recommendations),
            evidence=evidence,
            confidence=confidence,
            missing_data=tuple(missing_data),
            degraded_reason=degraded_reason,
            automation_eligible=False,
            source=input_data.source,
            source_observed_at=input_data.source_observed_at,
            calculated_at=input_data.calculated_at,
            calculation_version=self.calculation_version,
            baseline_metrics=baseline_metrics,
        )

    @staticmethod
    def _add(
        issues: list[dict[str, Any]],
        recommendations: list[dict[str, Any]],
        section: str,
        code: str,
        rationale: str,
        severity: str = "medium",
    ) -> None:
        issues.append({"section": section, "code": code, "severity": severity, "rationale": rationale})
        recommendations.append(
            {
                "section": section,
                "action": f"review_{section}",
                "rationale": rationale,
                "risk_level": "high" if section == "status" else "medium",
                "execution_mode": "recommendation_only",
            }
        )

    @staticmethod
    def _confidence(input_data: ListingQualityInput) -> Decimal:
        source_score = {
            "amazon_api": Decimal("0.95"),
            "csv_import": Decimal("0.80"),
            "manual": Decimal("0.70"),
            "system_estimate": Decimal("0.50"),
            "mock": Decimal("0.20"),
        }.get(input_data.source, Decimal("0.50"))
        if input_data.source_observed_at is None:
            return min(source_score, Decimal("0.40"))
        return source_score


class ListingExperimentEvaluator:
    def evaluate(
        self,
        *,
        experiment: ListingOptimizationExperiment,
        before: ListingQualitySnapshot,
        after: ListingQualitySnapshot,
        evaluated_at: datetime,
    ) -> ListingOptimizationExperiment:
        if evaluated_at.tzinfo is None:
            raise ValueError("evaluated_at must be timezone-aware")
        if experiment.status not in {"approved", "observing"}:
            raise ValueError("experiment must be approved before evaluation")
        if evaluated_at < experiment.observation_end:
            raise ValueError("experiment observation window has not ended")
        if (
            before.id != experiment.before_snapshot_id
            or after.tenant_id != experiment.tenant_id
            or after.listing_id != experiment.listing_id
        ):
            raise ValueError("experiment snapshots do not match experiment scope")
        reasons: list[str] = []
        quality_delta = after.quality_score - before.quality_score
        effect_metrics = {"quality_score_delta": str(quality_delta.quantize(SCORE))}
        before_conversion = _metric(before, "conversion_rate")
        after_conversion = _metric(after, "conversion_rate")
        if before_conversion is not None and before_conversion > 0 and after_conversion is not None:
            conversion_delta = ((after_conversion - before_conversion) / before_conversion).quantize(
                RATE, rounding=ROUND_HALF_UP
            )
            effect_metrics["conversion_rate_relative_delta"] = str(conversion_delta)
            if conversion_delta <= Decimal("-0.10"):
                reasons.append("conversion_drop_exceeds_guardrail")
        before_return = _metric(before, "return_rate")
        after_return = _metric(after, "return_rate")
        if before_return is not None and after_return is not None:
            return_delta = (after_return - before_return).quantize(RATE, rounding=ROUND_HALF_UP)
            effect_metrics["return_rate_delta"] = str(return_delta)
            if return_delta >= Decimal("0.02"):
                reasons.append("return_rate_increase_exceeds_guardrail")
        missing_metrics = (
            before_conversion is None or after_conversion is None or before_return is None or after_return is None
        )
        if missing_metrics:
            reasons.append("business_outcome_metrics_incomplete")
        control_missing, control_drift = _control_variable_findings(before, after, effect_metrics)
        if control_missing:
            reasons.append("control_variables_incomplete")
        reasons.extend(control_drift)
        if (before.baseline_metrics.get("sessions") or 0) < 200 or (after.baseline_metrics.get("sessions") or 0) < 200:
            reasons.append("experiment_sample_below_minimum")
        if control_missing or control_drift:
            effect_metrics["attribution_status"] = "confounded" if control_drift else "inconclusive"
        elif "experiment_sample_below_minimum" in reasons or missing_metrics:
            effect_metrics["attribution_status"] = "inconclusive"
        else:
            effect_metrics["attribution_status"] = (
                "directional_improvement"
                if quality_delta > 0 or (after_conversion or Decimal("0")) > (before_conversion or Decimal("0"))
                else "inconclusive"
            )
        rollback = any(
            reason in reasons
            for reason in ("conversion_drop_exceeds_guardrail", "return_rate_increase_exceeds_guardrail")
        )
        experiment.after_snapshot_id = after.id
        experiment.effect_metrics = effect_metrics
        experiment.evaluation_reasons = tuple(reasons)
        experiment.rollback_recommended = rollback
        experiment.evaluated_at = evaluated_at
        experiment.updated_at = evaluated_at
        experiment.status = (
            "rollback_recommended"
            if rollback
            else "confounded"
            if control_drift
            else "inconclusive"
            if (missing_metrics or control_missing or "experiment_sample_below_minimum" in reasons)
            else "directional_improvement"
        )
        return experiment


def _metric(snapshot: ListingQualitySnapshot, name: str) -> Decimal | None:
    value = snapshot.baseline_metrics.get(name)
    return Decimal(str(value)) if value is not None else None


_CONTROL_THRESHOLDS = {
    "ad_spend": Decimal("0.20"),
    "ad_traffic_share": Decimal("0.10"),
    "cpc": Decimal("0.20"),
    "price": Decimal("0.05"),
    "discount_rate": Decimal("0.10"),
    "buy_box_share": Decimal("0.10"),
    "sellable_units": Decimal("0.20"),
    "out_of_stock_hours": Decimal("1"),
    "natural_rank": Decimal("5"),
}
_CONTROL_NUMERIC_FIELDS = tuple(_CONTROL_THRESHOLDS)
_CONTROL_CATEGORICAL_FIELDS = ("promotion_active", "seasonality_key", "competitor_change")


def _control_variable_findings(
    before: ListingQualitySnapshot,
    after: ListingQualitySnapshot,
    effect_metrics: dict[str, str],
) -> tuple[list[str], list[str]]:
    missing: list[str] = []
    drift: list[str] = []
    for name in _CONTROL_NUMERIC_FIELDS:
        before_value = _metric(before, name)
        after_value = _metric(after, name)
        if before_value is None or after_value is None:
            missing.append(name)
            continue
        if name == "out_of_stock_hours" or name == "natural_rank":
            delta = abs(after_value - before_value)
            effect_metrics[f"control_{name}_delta"] = str(delta.quantize(RATE, rounding=ROUND_HALF_UP))
            if delta > _CONTROL_THRESHOLDS[name]:
                drift.append(f"confounded_{name}_drift")
            continue
        denominator = abs(before_value) if before_value else Decimal("1")
        relative_delta = abs((after_value - before_value) / denominator)
        effect_metrics[f"control_{name}_relative_delta"] = str(relative_delta.quantize(RATE, rounding=ROUND_HALF_UP))
        if relative_delta > _CONTROL_THRESHOLDS[name]:
            drift.append(f"confounded_{name}_drift")
    for name in _CONTROL_CATEGORICAL_FIELDS:
        before_value = before.baseline_metrics.get(name)
        after_value = after.baseline_metrics.get(name)
        if before_value is None or after_value is None:
            missing.append(name)
        elif before_value != after_value:
            drift.append(f"confounded_{name}_change")
    return missing, drift
