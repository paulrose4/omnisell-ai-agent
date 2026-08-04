from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Iterable

from domain.operational_intelligence import calculate_advertising_metrics, diagnose_advertising


RATE = Decimal("0.0001")

VOC_REASON_ALIASES: dict[str, str] = {
    "defective": "product_defect",
    "product_defect": "product_defect",
    "quality_issue": "product_defect",
    "does_not_fit": "size_or_fit",
    "size_issue": "size_or_fit",
    "too_large": "size_or_fit",
    "too_small": "size_or_fit",
    "damaged_in_transit": "shipping_damage",
    "shipping_damage": "shipping_damage",
    "carrier_damage": "shipping_damage",
    "packaging_problem": "packaging_problem",
    "poor_packaging": "packaging_problem",
    "damaged_item": "packaging_problem",
    "packaging_damage": "packaging_problem",
    "missing_part": "wrong_or_missing_item",
    "wrong_item": "wrong_or_missing_item",
    "missing_item": "wrong_or_missing_item",
    "wrong_or_missing_item": "wrong_or_missing_item",
    "usage_question": "instructions_unclear",
    "customer_support_resolved": "instructions_unclear",
    "instructions_unclear": "instructions_unclear",
    "listing_mismatch": "listing_mismatch",
    "not_as_described": "listing_mismatch",
    "subjective_dissatisfaction": "subjective_dissatisfaction",
    "no_longer_needed": "subjective_dissatisfaction",
    "unknown": "unknown",
}

FEEDBACK_REASONS = {
    "actionable",
    "not_a_real_issue",
    "stale_or_missing_data",
    "threshold_not_applicable",
    "duplicate_issue",
    "planned_promotion_or_seasonality",
    "already_handled_externally",
    "impact_too_low",
    "recommendation_not_executable",
    "accepted_planned_risk",
}

CALIBRATION_MIN_LABELS = 30
CRITICAL_HIGH_RECALL_FLOOR = Decimal("0.95")
MAX_EXECUTION_DATA_AGE_SECONDS = 6 * 60 * 60


def generate_calibration_proposal(
    *,
    rule_key: str,
    current_version: str,
    current_parameters: dict[str, Any],
    candidate_parameters: dict[str, Any],
    label_count: int,
    replay_metrics: dict[str, Any],
) -> dict[str, Any]:
    """Return a reviewable rule proposal; never mutate a live rule version."""
    if not rule_key.strip() or not current_version.strip():
        raise ValueError("rule_key and current_version are required")
    if not candidate_parameters or candidate_parameters == current_parameters:
        raise ValueError("candidate parameters must differ from the current version")
    if label_count < CALIBRATION_MIN_LABELS:
        return {
            "eligible": False,
            "status": "insufficient_labels",
            "reason": f"at least {CALIBRATION_MIN_LABELS} real labels are required",
            "publish_mode": "shadow_then_canary",
        }
    critical_recall = _optional_decimal(replay_metrics.get("critical_high_recall"))
    profit_risk = _optional_decimal(replay_metrics.get("expected_profit_risk"))
    if critical_recall is None or critical_recall < CRITICAL_HIGH_RECALL_FLOOR:
        return {
            "eligible": False,
            "status": "replay_failed",
            "reason": "critical/high recall is below the production floor",
            "publish_mode": "shadow_then_canary",
        }
    if profit_risk is not None and profit_risk > 0:
        return {
            "eligible": False,
            "status": "profit_risk_detected",
            "reason": "offline replay predicts positive profit risk",
            "publish_mode": "shadow_then_canary",
        }
    return {
        "eligible": True,
        "status": "pending_approval",
        "rule_key": rule_key,
        "base_version": current_version,
        "current_parameters": current_parameters,
        "candidate_parameters": candidate_parameters,
        "label_count": label_count,
        "replay_metrics": replay_metrics,
        "publish_mode": "shadow_then_canary",
        "requires_owner_approval": True,
    }


def classify_ad_execution(
    *,
    action_type: str,
    verified_amazon_data: bool,
    data_fresh_seconds: int | None,
    currency: str | None,
    account_currency: str | None,
    inventory_status: str | None,
    contribution_profit: Decimal | str | None,
) -> dict[str, Any]:
    """Apply deterministic preflight policy before an L1 manual-confirm action."""
    normalized = str(action_type or "").strip().lower()
    risk_levels = {
        "negative_keyword_candidate": "low",
        "decrease_bid_candidate": "low",
        "increase_bid_candidate": "medium",
        "budget_reallocation_candidate": "medium",
        "pause_candidate": "high",
    }
    risk_level = risk_levels.get(normalized, "high")
    reasons: list[str] = []
    if not verified_amazon_data:
        reasons.append("amazon data is not verified")
    if data_fresh_seconds is None or data_fresh_seconds < 0 or data_fresh_seconds > MAX_EXECUTION_DATA_AGE_SECONDS:
        reasons.append("advertising evidence is stale or missing")
    if not currency or not account_currency or str(currency).upper() != str(account_currency).upper():
        reasons.append("currency is missing or does not match the account")
    profit = _optional_decimal(contribution_profit)
    growth_action = normalized in {"increase_bid_candidate", "budget_reallocation_candidate"}
    if growth_action and inventory_status in {"critical_stockout", "out_of_stock", "stale"}:
        reasons.append("inventory guard blocks growth")
    if growth_action and (profit is None or profit <= 0):
        reasons.append("contribution profit does not support growth")
    return {
        "eligible": not reasons,
        "risk_level": risk_level,
        "execution_mode": "manual_confirm",
        "automation_eligible": False,
        "amazon_write_performed": False,
        "reasons": reasons,
        "requires_approval": risk_level in {"medium", "high"},
        "cooldown_hours": 72 if risk_level == "high" else 24,
    }


def prepare_attribution_plan(
    *,
    primary_metric: str,
    desired_direction: str,
    baseline_start: datetime,
    baseline_end: datetime,
    observation_start: datetime,
    observation_end: datetime,
    currency: str,
    randomized_experiment: bool,
    has_control_group: bool,
    minimum_sample: int,
) -> dict[str, Any]:
    """Create a pre-registered, non-causal-by-default observation contract."""
    if not primary_metric.strip() or desired_direction not in {"increase", "decrease"}:
        raise ValueError("primary metric and desired direction are required")
    timestamps = (baseline_start, baseline_end, observation_start, observation_end)
    if any(value.tzinfo is None for value in timestamps):
        raise ValueError("attribution timestamps must be timezone-aware")
    if baseline_end <= baseline_start or observation_end <= observation_start or observation_start < baseline_end:
        raise ValueError("attribution windows must be ordered and non-overlapping")
    normalized_currency = str(currency or "").upper()
    if len(normalized_currency) != 3 or not normalized_currency.isalpha():
        raise ValueError("attribution currency must be an ISO 4217 code")
    if minimum_sample <= 0:
        raise ValueError("minimum_sample must be positive")
    method = "randomized_controlled" if randomized_experiment else "difference_in_differences" if has_control_group else "interrupted_time_series"
    return {
        "primary_metric": primary_metric,
        "desired_direction": desired_direction,
        "baseline_start": baseline_start.astimezone(UTC),
        "baseline_end": baseline_end.astimezone(UTC),
        "observation_start": observation_start.astimezone(UTC),
        "observation_end": observation_end.astimezone(UTC),
        "currency": normalized_currency,
        "method": method,
        "minimum_sample": minimum_sample,
        "causal_claim_allowed": False,
        "status": "planned",
    }


def calculate_incremental_contribution_profit(
    *, baseline: dict[str, Any], result: dict[str, Any],
) -> Decimal | None:
    baseline_value = _optional_decimal(baseline.get("contribution_profit"))
    result_value = _optional_decimal(result.get("contribution_profit"))
    if baseline_value is None or result_value is None:
        return None
    return (result_value - baseline_value).quantize(RATE, rounding=ROUND_HALF_UP)


@dataclass(frozen=True)
class ActivityEvent:
    work_item_id: str
    actor_id: str
    event_type: str
    occurred_at: datetime

    def __post_init__(self) -> None:
        if self.event_type not in {"started", "paused", "resumed", "completed"}:
            raise ValueError("unsupported activity event type")
        if self.occurred_at.tzinfo is None:
            raise ValueError("activity timestamps must be timezone-aware")


@dataclass(frozen=True)
class IssueFeedbackLabel:
    issue_id: str
    severity: str
    is_correct: bool
    is_useful: bool
    reason_code: str

    def __post_init__(self) -> None:
        if self.severity not in {"low", "medium", "high", "critical"}:
            raise ValueError("invalid issue feedback severity")
        if self.reason_code not in FEEDBACK_REASONS:
            raise ValueError("unsupported issue feedback reason")


def normalize_voc_reason(reason_code: str | None) -> str:
    normalized = str(reason_code or "unknown").strip().lower().replace("-", "_").replace(" ", "_")
    return VOC_REASON_ALIASES.get(normalized, "unknown")


def route_voc_reason(reason_code: str) -> dict[str, str]:
    normalized = normalize_voc_reason(reason_code)
    if normalized == "listing_mismatch":
        return {"domain": "listing", "team": "listing"}
    if normalized in {"packaging_problem", "shipping_damage"}:
        return {"domain": "inventory", "team": "supply_chain"}
    if normalized == "product_defect":
        return {"domain": "product_quality", "team": "quality"}
    if normalized == "wrong_or_missing_item":
        return {"domain": "fulfillment", "team": "operations"}
    if normalized == "instructions_unclear":
        return {"domain": "customer_service", "team": "customer_service"}
    return {"domain": "operations", "team": "operations"}


def evaluate_sponsored_products(
    *,
    seller_sku: str,
    marketplace_id: str,
    campaign_id: str,
    spend: Decimal,
    attributed_sales: Decimal | None,
    total_sales: Decimal | None,
    impressions: int | None,
    clicks: int | None,
    orders: int | None,
    budget: Decimal | None,
    elapsed_days: Decimal,
    period_days: Decimal,
    break_even_acos: Decimal | None,
    contribution_profit: Decimal | None,
    inventory_status: str | None,
    min_clicks_without_order: int = 20,
) -> dict[str, Any]:
    metrics = calculate_advertising_metrics(
        spend=spend,
        attributed_sales=attributed_sales,
        total_sales=total_sales,
        impressions=impressions,
        clicks=clicks,
        orders=orders,
    )
    metrics["roas"] = _rate(attributed_sales, spend)
    findings = diagnose_advertising(
        seller_sku=seller_sku,
        marketplace_id=marketplace_id,
        campaign_id=campaign_id,
        spend=spend,
        attributed_sales=attributed_sales,
        total_sales=total_sales,
        impressions=impressions,
        clicks=clicks,
        orders=orders,
        budget=budget,
        elapsed_days=elapsed_days,
        period_days=period_days,
        break_even_acos=break_even_acos,
        min_clicks_without_order=min_clicks_without_order,
    )
    growth_blocked = inventory_status in {"critical_stockout", "out_of_stock", "stale"} or (
        contribution_profit is not None and contribution_profit <= 0
    )
    if metrics["acos"] is not None and break_even_acos is not None and metrics["acos"] <= break_even_acos and contribution_profit is not None and contribution_profit <= 0:
        findings.append({
            "code": "low_acos_but_contribution_loss",
            "severity": "critical",
            "title": "ACoS看似健康但SKU仍在亏损",
            "seller_sku": seller_sku,
            "marketplace_id": marketplace_id,
            "campaign_id": campaign_id,
            "contribution_profit": str(contribution_profit),
        })
    if inventory_status in {"critical_stockout", "out_of_stock", "stale"}:
        findings.append({
            "code": "inventory_guard_blocks_growth",
            "severity": "high",
            "title": "库存护栏阻止广告放量",
            "seller_sku": seller_sku,
            "marketplace_id": marketplace_id,
            "campaign_id": campaign_id,
            "inventory_status": inventory_status,
        })

    candidates: list[dict[str, Any]] = []
    codes = {item["code"] for item in findings}
    if "click_waste_without_orders" in codes:
        candidates.append(_candidate("negative_keyword_candidate", "复核无转化搜索词并生成否定关键词候选"))
    if "acos_above_break_even" in codes:
        candidates.append(_candidate("decrease_bid_candidate", "降低竞价至利润护栏允许的区间"))
    if "low_acos_but_contribution_loss" in codes:
        candidates.append(_candidate("pause_candidate", "暂停放量并复核价格、成本和广告归因"))
    if "budget_projected_to_exhaust_early" in codes and not growth_blocked:
        candidates.append(_candidate("budget_reallocation_candidate", "从低边际利润活动转移预算"))
    if not findings and not growth_blocked and contribution_profit is not None and contribution_profit > 0:
        candidates.append(_candidate("increase_bid_candidate", "在利润和库存护栏内测试性提高竞价"))
    return {
        "metrics": metrics,
        "findings": findings,
        "candidates": candidates,
        "guards": {
            "growth_blocked": growth_blocked,
            "inventory_status": inventory_status,
            "contribution_profit": contribution_profit,
        },
    }


def calculate_labor_metrics(events: Iterable[ActivityEvent]) -> dict[str, int | None]:
    ordered = sorted(events, key=lambda item: item.occurred_at)
    if not ordered:
        return {"active_touch_seconds": 0, "cycle_seconds": None, "waiting_seconds": None}
    active_started: datetime | None = None
    active_seconds = 0
    cycle_started: datetime | None = None
    cycle_completed: datetime | None = None
    identity = {(item.work_item_id, item.actor_id) for item in ordered}
    if len(identity) != 1:
        raise ValueError("labor metrics require one work item and actor")
    for event in ordered:
        if event.event_type in {"started", "resumed"}:
            if cycle_started is None:
                cycle_started = event.occurred_at
            if active_started is not None:
                raise ValueError("activity cannot start while already active")
            active_started = event.occurred_at
        elif event.event_type in {"paused", "completed"}:
            if active_started is None:
                raise ValueError("activity cannot pause or complete while inactive")
            active_seconds += int((event.occurred_at - active_started).total_seconds())
            active_started = None
            if event.event_type == "completed":
                cycle_completed = event.occurred_at
    if active_started is not None:
        raise ValueError("activity interval is still open")
    cycle_seconds = int((cycle_completed - cycle_started).total_seconds()) if cycle_started and cycle_completed else None
    waiting_seconds = cycle_seconds - active_seconds if cycle_seconds is not None else None
    return {
        "active_touch_seconds": active_seconds,
        "cycle_seconds": cycle_seconds,
        "waiting_seconds": waiting_seconds,
    }


def evaluate_issue_feedback(
    *, labels: Iterable[IssueFeedbackLabel], missed_issue_severities: Iterable[str] = (),
) -> dict[str, Decimal | int | None]:
    materialized = list(labels)
    missed = list(missed_issue_severities)
    true_positive = sum(1 for item in materialized if item.is_correct)
    false_positive = len(materialized) - true_positive
    false_negative = len(missed)
    high_detected = sum(1 for item in materialized if item.is_correct and item.severity in {"critical", "high"})
    high_missed = sum(1 for severity in missed if severity in {"critical", "high"})
    return {
        "label_count": len(materialized),
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "precision": _rate(Decimal(true_positive), Decimal(true_positive + false_positive)),
        "recall": _rate(Decimal(true_positive), Decimal(true_positive + false_negative)),
        "dismissed_as_false_positive_rate": _rate(Decimal(false_positive), Decimal(len(materialized))),
        "critical_high_recall": _rate(Decimal(high_detected), Decimal(high_detected + high_missed)),
        "calibration_eligible": len(materialized) >= 30,
    }


def assess_action_outcome(
    *,
    baseline_metrics: dict[str, Any],
    result_metrics: dict[str, Any],
    primary_metric: str,
    desired_direction: str,
    execution_verified: bool,
    confounders: Iterable[str] = (),
    control_metrics: dict[str, Any] | None = None,
    randomized_experiment: bool = False,
    minimum_sample_satisfied: bool = False,
) -> dict[str, Any]:
    if desired_direction not in {"increase", "decrease"}:
        raise ValueError("desired_direction must be increase or decrease")
    baseline = _optional_decimal(baseline_metrics.get(primary_metric))
    result = _optional_decimal(result_metrics.get(primary_metric))
    confounder_list = sorted(set(str(item) for item in confounders if item))
    if not execution_verified:
        return _outcome("inconclusive", "execution_not_verified", None, confounder_list, False)
    if baseline is None or result is None:
        return _outcome("inconclusive", "metrics_incomplete", None, confounder_list, False)
    delta = (result - baseline).quantize(RATE)
    improved = delta > 0 if desired_direction == "increase" else delta < 0
    status = "directional_improvement" if improved else "guardrail_regression" if delta else "no_material_change"
    if randomized_experiment and minimum_sample_satisfied:
        level = "causal_experiment"
    elif control_metrics and minimum_sample_satisfied:
        level = "quasi_experimental"
    else:
        level = "observed_association"
    return _outcome(level, status, delta, confounder_list, level == "causal_experiment")


def _candidate(action_type: str, title: str) -> dict[str, Any]:
    return {
        "action_type": action_type,
        "title": title,
        "automation_eligible": False,
        "execution_mode": "manual_only",
    }


def _outcome(level: str, status: str, delta: Decimal | None, confounders: list[str], causal: bool) -> dict[str, Any]:
    return {
        "attribution_level": level,
        "outcome_status": status,
        "incremental_value": delta,
        "confounders": confounders,
        "causal_claim_allowed": causal,
    }


def _rate(numerator: Decimal | None, denominator: Decimal | None) -> Decimal | None:
    if numerator is None or denominator is None or denominator <= 0:
        return None
    try:
        return (numerator / denominator).quantize(RATE, rounding=ROUND_HALF_UP)
    except (InvalidOperation, ZeroDivisionError):
        return None


def _optional_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    return Decimal(str(value))
