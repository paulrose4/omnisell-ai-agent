from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Iterable

from domain.inventory import InventoryHealthCalculator, InventoryHealthInput
from domain.listing_quality import ListingQualityEvaluator, ListingQualityInput
from domain.models import InventoryPolicy, SKUCostProfile
from domain.operational_intelligence import AmazonOperationalSignal, diagnose_advertising, summarize_voc
from domain.operations import OperationalIssueDetector
from domain.profitability import ProfitCalculationInput, ProfitCalculator
from safety.policy_engine import PolicyDecision, PolicyEngine


MARKETPLACE_ID = "ATVPDKIKX0DER"
TENANT_ID = "synthetic-replay-tenant"
RATE = Decimal("0.0001")
HIGH_PRIORITY = {"critical", "high"}
EXTERNAL_SOURCES = {"amazon_api", "advertising_api", "amazon_sp_api"}
SYNTHETIC_SOURCES = {"mock", "manual", "csv_import", "system_estimate"}


def generate_synthetic_replay(*, days: int = 90, end_at: datetime | None = None) -> dict[str, Any]:
    if days < 30 or days > 90:
        raise ValueError("synthetic replay must cover 30 to 90 days")
    requested_end = end_at or datetime.now(UTC)
    if requested_end.tzinfo is None:
        raise ValueError("end_at must be timezone-aware")
    effective_end = requested_end.astimezone(UTC)
    start = (effective_end - timedelta(days=days - 1)).replace(hour=3, minute=0, second=0, microsecond=0)
    anomaly_start = start + timedelta(days=max(1, days - 14))
    records: list[dict[str, Any]] = []
    for offset in range(days):
        observed_at = start + timedelta(days=offset)
        anomaly_active = observed_at >= anomaly_start
        records.extend(_daily_inventory_records(observed_at, anomaly_active))
        records.extend(_daily_profit_records(observed_at, anomaly_active))
        records.extend(_daily_advertising_records(observed_at, anomaly_active))
        records.extend(_daily_listing_records(observed_at, anomaly_active))
        records.extend(_daily_voc_records(observed_at, anomaly_active))
        records.extend(_daily_amazon_signal_records(observed_at, anomaly_active))
    return {
        "metadata": {
            "dataset_version": "amazon-operations-synthetic-replay-v1",
            "classification": "synthetic_anonymized_replay",
            "tenant_id": TENANT_ID,
            "marketplace_id": MARKETPLACE_ID,
            "day_count": days,
            "period_start": start.isoformat(),
            "period_end": effective_end.isoformat(),
            "generated_at": effective_end.isoformat(),
            "contains_real_customer_data": False,
            "contains_real_amazon_data": False,
            "limitations": [
                "Gold labels are scenario-authored, not independent expert annotations.",
                "Modeled operator timings are not observed labor savings.",
                "Mock signals do not validate SP-API pagination, throttling, revocation, or Seller-specific mappings.",
                "Advisor quality requires real-provider output and named expert blind review.",
            ],
        },
        "records": records,
    }


def score_predictions(
    *, gold: set[str], predicted: set[str], high_priority_gold: set[str] | None = None,
) -> dict[str, Any]:
    true_positive = len(gold & predicted)
    false_positive = len(predicted - gold)
    false_negative = len(gold - predicted)
    precision = _ratio(true_positive, true_positive + false_positive)
    recall = _ratio(true_positive, true_positive + false_negative)
    if precision is None or recall is None or precision + recall == 0:
        f1 = None
    else:
        f1 = (Decimal("2") * precision * recall / (precision + recall)).quantize(RATE, rounding=ROUND_HALF_UP)
    high_gold = high_priority_gold or set()
    high_priority_recall = _ratio(len(high_gold & predicted), len(high_gold))
    dismissed_issue_rate = _ratio(false_positive, len(predicted))
    return {
        "true_positive": true_positive,
        "false_positive": false_positive,
        "false_negative": false_negative,
        "precision": _format_rate(precision),
        "recall": _format_rate(recall),
        "f1": _format_rate(f1),
        "high_priority_recall": _format_rate(high_priority_recall),
        "dismissed_issue_rate": _format_rate(dismissed_issue_rate),
    }


def evaluate_replay(dataset: dict[str, Any]) -> dict[str, Any]:
    _validate_dataset(dataset)
    gold: set[str] = set()
    high_priority_gold: set[str] = set()
    predicted: set[str] = set()
    degraded_predictions = 0
    degraded_without_reason = 0
    high_priority_total = 0
    high_priority_within_sla = 0
    domain_counts: Counter[str] = Counter()
    source_counts: Counter[str] = Counter()
    verified_fact_count = 0
    detection_details: list[dict[str, Any]] = []

    for record in dataset["records"]:
        domain_counts[record["domain"]] += 1
        source_counts[record["source"]] += 1
        verified_fact_count += int(record["verified_amazon_data"] is True)
        for label in record["gold_labels"]:
            key = _finding_key(record["record_id"], label["code"])
            gold.add(key)
            if label["severity"] in HIGH_PRIORITY:
                high_priority_gold.add(key)
                high_priority_total += 1
                if int(record["simulated_ack_minutes"]) <= _sla_minutes(label["severity"]):
                    high_priority_within_sla += 1
        for finding in _evaluate_record(record):
            key = _finding_key(record["record_id"], finding["code"])
            predicted.add(key)
            if finding.get("degraded"):
                degraded_predictions += 1
                if not finding.get("degraded_reason"):
                    degraded_without_reason += 1
            detection_details.append({
                "record_id": record["record_id"],
                "domain": record["domain"],
                "seller_sku": record["seller_sku"],
                **finding,
            })

    metrics = score_predictions(gold=gold, predicted=predicted, high_priority_gold=high_priority_gold)
    metrics.update({
        "high_priority_sla_compliance": _format_rate(_ratio(high_priority_within_sla, high_priority_total)),
        "degraded_reason_visibility": _format_rate(_ratio(degraded_predictions - degraded_without_reason, degraded_predictions)),
        "degraded_detection_count": degraded_predictions,
        "degraded_without_reason": degraded_without_reason,
    })
    safety = _evaluate_safety_boundaries(dataset)
    metrics["safety_incident_count"] = safety["incident_count"]
    modeled_efficiency = _modeled_efficiency()
    synthetic_passed = (
        Decimal(metrics["high_priority_recall"] or "0") >= Decimal("0.95")
        and Decimal(metrics["dismissed_issue_rate"] or "1") < Decimal("0.15")
        and metrics["degraded_reason_visibility"] == "1.0000"
        and safety["incident_count"] == 0
    )
    gates = {
        "synthetic_rule_replay": _gate(
            "passed" if synthetic_passed else "failed", "verified_by_synthetic_replay",
            "Deterministic anomaly rules, provenance labels, degraded reasons, and policy probes.",
        ),
        "expert_blind_review": _gate(
            "not_verified", "requires_expert_real_model_validation",
            "Requires real Advisor and human candidates scored by at least three named Amazon operators.",
        ),
        "real_model_structured_output": _gate(
            "not_verified", "requires_expert_real_model_validation",
            "Requires provider JSON Schema, timeout, rejection, repair, token, cost, and Chinese-quality evidence.",
        ),
        "real_amazon_traceability": _gate(
            "not_verified", "requires_real_amazon_shadow_pilot",
            "The replay intentionally contains zero Amazon-verified facts.",
        ),
        "shadow_mode_business_effect": _gate(
            "not_verified", "requires_real_amazon_shadow_pilot",
            "Requires 2-4 weeks of observed operator work, missed incidents, alert dismissals, and SLA outcomes.",
        ),
        "observed_efficiency_improvement": _gate(
            "not_verified", "requires_real_amazon_shadow_pilot",
            "The replay includes an explicitly modeled estimate, not observed labor-time evidence.",
        ),
    }
    return {
        "report_version": "production-acceptance-replay-v1",
        "generated_at": dataset["metadata"]["generated_at"],
        "dataset": {
            **dataset["metadata"],
            "record_count": len(dataset["records"]),
            "domain_counts": dict(sorted(domain_counts.items())),
            "source_counts": dict(sorted(source_counts.items())),
            "verified_amazon_fact_count": verified_fact_count,
            "gold_finding_count": len(gold),
            "predicted_finding_count": len(predicted),
        },
        "synthetic_metrics": metrics,
        "modeled_efficiency": modeled_efficiency,
        "safety_probes": safety,
        "gates": gates,
        "decisions": {
            "internal_read_only_pilot": "conditional_go" if synthetic_passed else "no_go",
            "company_production": "no_go",
            "reason": (
                "Synthetic replay cannot satisfy real Amazon traceability, real-model expert quality, "
                "or observed Shadow Mode business-effect gates."
            ),
        },
        "detection_sample": detection_details[:50],
    }


def generate_blind_review_template(candidates: Iterable[dict[str, str]]) -> dict[str, Any]:
    dimensions = (
        "factual_grounding", "evidence_citation", "missing_data_honesty",
        "amazon_operational_usefulness", "next_step_clarity", "policy_safety", "chinese_quality",
    )
    return {
        "rubric_version": "advisor-blind-review-v1",
        "source_key_withheld": True,
        "minimum_named_reviewers": 3,
        "passing_rule": "Average >= 1.5, with no zero for factual_grounding or policy_safety.",
        "candidates": [
            {
                "case_id": item["case_id"],
                "candidate_id": item["candidate_id"],
                "content": item["content"],
                "source": "withheld",
                "reviewer_name": None,
                "scores": {dimension: None for dimension in dimensions},
                "edit_level": None,
                "adoption_decision": None,
                "review_notes": None,
            }
            for item in candidates
        ],
    }


def render_markdown_report(report: dict[str, Any]) -> str:
    metrics = report["synthetic_metrics"]
    lines = [
        "# OmniSell Production Acceptance Replay",
        "",
        f"Generated: {report['generated_at']}",
        "",
        "## Decision",
        "",
        f"- Internal read-only pilot: **{report['decisions']['internal_read_only_pilot']}**",
        f"- Company production: **{report['decisions']['company_production']}**",
        f"- Reason: {report['decisions']['reason']}",
        "",
        "## Synthetic Replay Metrics",
        "",
        f"- Records: {report['dataset']['record_count']}",
        f"- Precision: {metrics['precision']}",
        f"- Recall: {metrics['recall']}",
        f"- Critical/High recall: {metrics['high_priority_recall']}",
        f"- Dismissed issue rate: {metrics['dismissed_issue_rate']}",
        f"- Simulated Critical/High SLA compliance: {metrics['high_priority_sla_compliance']}",
        f"- Degraded reason visibility: {metrics['degraded_reason_visibility']}",
        f"- Deterministic safety/provenance incidents: {metrics['safety_incident_count']}",
        f"- Amazon-verified facts in replay: {report['dataset']['verified_amazon_fact_count']}",
        "",
        "## Gate Classification",
        "",
        "| Gate | Status | Evidence class |",
        "| --- | --- | --- |",
    ]
    for name, gate in report["gates"].items():
        lines.append(f"| {name} | {gate['status']} | {gate['evidence_class']} |")
    lines.extend([
        "",
        "## Interpretation",
        "",
        "This replay validates deterministic business-rule coverage and evaluation plumbing only. It does not prove real Seller mappings, real-model recommendation quality, actual labor savings, or 2-4 week Shadow Mode performance.",
        "",
        "The correct current release statement is: conditional go for an internal read-only pilot; no-go for a company production claim until all external gates are completed and signed.",
        "",
    ])
    return "\n".join(lines)


def _daily_inventory_records(observed_at: datetime, anomaly_active: bool) -> list[dict[str, Any]]:
    scenarios = (
        ("SYN-INV-HEALTHY", "healthy", 600, 10, None),
        ("SYN-INV-STOCKOUT", "stockout", 20 if anomaly_active else 600, 10, "inventory_stockout_risk" if anomaly_active else None),
        ("SYN-INV-EXCESS", "excess", 1200 if anomaly_active else 120, 2, "inventory_excess" if anomaly_active else None),
        ("SYN-INV-STALE", "stale", 600, 10, "inventory_data_quality" if anomaly_active else None),
    )
    records = []
    for sku, scenario, available, daily_sales, label in scenarios:
        labels = []
        if label:
            labels.append({"code": label, "severity": "critical" if label == "inventory_stockout_risk" else "high" if label == "inventory_excess" else "medium"})
        records.append(_record(
            observed_at, "inventory", sku, scenario,
            {
                "available": available, "reserved": 0, "inbound": 0, "in_transit": 0, "unsellable": 0,
                "sales_7_days": daily_sales * 7, "sales_14_days": daily_sales * 14,
                "sales_30_days": daily_sales * 30, "collection_age_hours": 12 if scenario == "stale" and anomaly_active else 1,
                "unit_revenue": "29.99", "unit_contribution_profit": "8.00",
            }, labels,
        ))
    return records


def _daily_profit_records(observed_at: datetime, anomaly_active: bool) -> list[dict[str, Any]]:
    scenarios = (
        ("SYN-PROFIT-HEALTHY", "healthy", "299.90", False, None),
        ("SYN-PROFIT-LOSS", "loss", "140.00" if anomaly_active else "299.90", False, "sku_contribution_loss" if anomaly_active else None),
        ("SYN-PROFIT-MISSING", "missing_cost", "299.90", anomaly_active, "profit_data_quality" if anomaly_active else None),
    )
    records = []
    for sku, scenario, sales, missing_cost, label in scenarios:
        labels = [{"code": label, "severity": "critical" if label == "sku_contribution_loss" else "high"}] if label else []
        records.append(_record(
            observed_at, "profitability", sku, scenario,
            {
                "currency": "USD", "units": 10, "item_sales": sales, "advertising_cost": "10.00",
                "advertising_attributed_sales": sales, "missing_packaging_cost": missing_cost,
            }, labels,
        ))
    return records


def _daily_advertising_records(observed_at: datetime, anomaly_active: bool) -> list[dict[str, Any]]:
    scenarios = (
        ("SYN-ADS-HEALTHY", "healthy", "50", "500", 100, 10, "1000", None),
        ("SYN-ADS-ACOS", "high_acos", "100" if anomaly_active else "50", "200" if anomaly_active else "500", 100, 10, "1000", "acos_above_break_even" if anomaly_active else None),
        ("SYN-ADS-WASTE", "click_waste", "100" if anomaly_active else "20", "0" if anomaly_active else "300", 25, 0 if anomaly_active else 5, "1000", "click_waste_without_orders" if anomaly_active else None),
        ("SYN-ADS-BUDGET", "budget", "180" if anomaly_active else "50", "1000", 100, 10, "300" if anomaly_active else "1000", "budget_projected_to_exhaust_early" if anomaly_active else None),
    )
    records = []
    for sku, scenario, spend, attributed_sales, clicks, orders, budget, label in scenarios:
        severity = "high" if label in {"acos_above_break_even", "click_waste_without_orders"} else "medium"
        records.append(_record(
            observed_at, "advertising", sku, scenario,
            {
                "campaign_id": f"campaign-{sku.lower()}", "spend": spend,
                "attributed_sales": attributed_sales, "total_sales": "1200", "impressions": 10000,
                "clicks": clicks, "orders": orders, "budget": budget, "elapsed_days": "3",
                "period_days": "7", "break_even_acos": "0.30",
            }, [{"code": label, "severity": severity}] if label else [],
        ))
    return records


def _daily_listing_records(observed_at: datetime, anomaly_active: bool) -> list[dict[str, Any]]:
    good = {
        "title": "Stainless Steel Insulated Travel Bottle for Office and Outdoor Use",
        "bullet_points": ["Durable steel", "Leak resistant", "Easy clean", "Fits cup holders", "Reusable design"],
        "description": "Detailed verified product information. " * 20,
        "image_count": 7, "search_terms": ["travel bottle", "steel bottle", "office bottle", "reusable bottle", "outdoor bottle"],
        "status": "active",
    }
    poor = {
        "title": "Bottle", "bullet_points": ["Steel"], "description": "Short description.",
        "image_count": 1, "search_terms": [], "status": "active",
    }
    return [
        _record(observed_at, "listing", "SYN-LISTING-GOOD", "healthy", good, []),
        _record(
            observed_at, "listing", "SYN-LISTING-POOR", "poor" if anomaly_active else "healthy",
            poor if anomaly_active else good,
            [{"code": "listing_quality_risk", "severity": "critical"}] if anomaly_active else [],
        ),
    ]


def _daily_voc_records(observed_at: datetime, anomaly_active: bool) -> list[dict[str, Any]]:
    actionable_returns = [{"reason_code": "damaged", "quantity": 1} for _ in range(6)] if anomaly_active else []
    return [
        _record(
            observed_at, "voc", "SYN-VOC-LOW-SAMPLE", "insufficient_sample",
            {"returns": [{"reason_code": "damaged", "quantity": 1} for _ in range(3)], "reviews": [], "messages": []}, [],
        ),
        _record(
            observed_at, "voc", "SYN-VOC-DAMAGE", "damaged_cluster" if anomaly_active else "healthy",
            {"returns": actionable_returns, "reviews": [], "messages": []},
            [{"code": "voc_actionable", "severity": "high"}] if anomaly_active else [],
        ),
    ]


def _daily_amazon_signal_records(observed_at: datetime, anomaly_active: bool) -> list[dict[str, Any]]:
    scenarios = (
        ("SYN-SIGNAL-BUYBOX", "buy_box_loss", "high"),
        ("SYN-SIGNAL-STRANDED", "stranded_inventory", "high"),
        ("SYN-SIGNAL-ACCOUNT", "account_health_violation", "critical"),
        ("SYN-SIGNAL-INBOUND", "inbound_receiving_delay", "high"),
    )
    records = []
    for sku, signal_type, severity in scenarios:
        records.append(_record(
            observed_at, "amazon_signal", sku, signal_type,
            {"signal_type": signal_type, "severity": severity, "raw_status_code": signal_type.upper(), "active": anomaly_active},
            [{"code": signal_type, "severity": severity}] if anomaly_active else [],
        ))
    return records


def _record(
    observed_at: datetime, domain: str, seller_sku: str, scenario: str,
    payload: dict[str, Any], gold_labels: list[dict[str, str]],
) -> dict[str, Any]:
    return {
        "record_id": f"{observed_at.date().isoformat()}:{domain}:{seller_sku}",
        "date": observed_at.date().isoformat(),
        "observed_at": observed_at.isoformat(),
        "tenant_id": TENANT_ID,
        "marketplace_id": MARKETPLACE_ID,
        "seller_sku": seller_sku,
        "domain": domain,
        "scenario": scenario,
        "source": "mock",
        "verified_amazon_data": False,
        "payload": payload,
        "gold_labels": gold_labels,
        "simulated_ack_minutes": 30 if any(item["severity"] == "critical" for item in gold_labels) else 120,
    }


def _evaluate_record(record: dict[str, Any]) -> list[dict[str, Any]]:
    domain = record["domain"]
    if domain == "inventory":
        return _evaluate_inventory(record)
    if domain == "profitability":
        return _evaluate_profit(record)
    if domain == "advertising":
        return _evaluate_advertising(record)
    if domain == "listing":
        return _evaluate_listing(record)
    if domain == "voc":
        return _evaluate_voc(record)
    if domain == "amazon_signal":
        return _evaluate_amazon_signal(record)
    raise ValueError(f"unsupported replay domain: {domain}")


def _evaluate_inventory(record: dict[str, Any]) -> list[dict[str, Any]]:
    observed_at = datetime.fromisoformat(record["observed_at"])
    payload = record["payload"]
    policy = InventoryPolicy(
        tenant_id=TENANT_ID, seller_sku=record["seller_sku"], marketplace_id=MARKETPLACE_ID,
        supplier_lead_time_days=5, production_lead_time_days=10, shipping_lead_time_days=25,
        amazon_receiving_days=7, safety_stock_days=7, target_coverage_days=60,
        minimum_order_quantity=100, case_pack_quantity=20, preferred_shipping_mode="sea",
        sales_weight_7d=Decimal("0.50"), sales_weight_14d=Decimal("0.30"), sales_weight_30d=Decimal("0.20"),
        source="mock", effective_from=observed_at - timedelta(days=365), collected_at=observed_at, created_at=observed_at,
    )
    snapshot = InventoryHealthCalculator().calculate(
        policy=policy,
        input_data=InventoryHealthInput(
            tenant_id=TENANT_ID, seller_sku=record["seller_sku"], marketplace_id=MARKETPLACE_ID,
            available=int(payload["available"]), reserved=int(payload["reserved"]), inbound=int(payload["inbound"]),
            in_transit=int(payload["in_transit"]), unsellable=int(payload["unsellable"]),
            sales_7_days=int(payload["sales_7_days"]), sales_14_days=int(payload["sales_14_days"]),
            sales_30_days=int(payload["sales_30_days"]), inventory_source=record["source"],
            inventory_collected_at=observed_at - timedelta(hours=int(payload["collection_age_hours"])),
            calculated_at=observed_at, unit_revenue=Decimal(payload["unit_revenue"]),
            unit_contribution_profit=Decimal(payload["unit_contribution_profit"]),
        ),
    )
    result = OperationalIssueDetector().detect(
        tenant_id=TENANT_ID, inventory_snapshots=[snapshot], profit_snapshots=[], detected_at=observed_at,
    )
    return [
        {
            "code": issue.issue_type, "severity": issue.severity,
            "degraded": bool(issue.degraded_reason or issue.missing_data),
            "degraded_reason": issue.degraded_reason or ("missing_data:" + ",".join(issue.missing_data) if issue.missing_data else None),
        }
        for issue in result.issues
    ]


def _evaluate_profit(record: dict[str, Any]) -> list[dict[str, Any]]:
    observed_at = datetime.fromisoformat(record["observed_at"])
    payload = record["payload"]
    profile = SKUCostProfile(
        tenant_id=TENANT_ID, seller_sku=record["seller_sku"], marketplace_id=MARKETPLACE_ID, currency="USD",
        product_cost=Decimal("6.00"), domestic_shipping_cost=Decimal("0.60"), international_freight_cost=Decimal("1.50"),
        duty_cost=Decimal("0.50"), prep_cost=Decimal("0.40"), packaging_cost=None if payload["missing_packaging_cost"] else Decimal("0.35"),
        estimated_referral_fee_rate=Decimal("0.15"), estimated_fulfillment_fee=Decimal("4.25"),
        estimated_storage_cost=Decimal("0.20"), estimated_return_loss_rate=Decimal("0.02"),
        exchange_rate_buffer_rate=Decimal("0.01"), target_margin_rate=Decimal("0.20"), minimum_margin_rate=Decimal("0.10"),
        effective_from=observed_at - timedelta(days=365), source="mock", collected_at=observed_at, created_at=observed_at,
    )
    snapshot = ProfitCalculator().calculate(
        profile=profile,
        input_data=ProfitCalculationInput(
            tenant_id=TENANT_ID, seller_sku=record["seller_sku"], marketplace_id=MARKETPLACE_ID,
            currency=payload["currency"], window_start=observed_at - timedelta(days=1), window_end=observed_at,
            item_sales=Decimal(payload["item_sales"]), units=int(payload["units"]),
            advertising_cost=Decimal(payload["advertising_cost"]),
            advertising_attributed_sales=Decimal(payload["advertising_attributed_sales"]), calculated_at=observed_at,
        ),
    )
    result = OperationalIssueDetector().detect(
        tenant_id=TENANT_ID, inventory_snapshots=[], profit_snapshots=[snapshot], detected_at=observed_at,
    )
    return [
        {
            "code": issue.issue_type, "severity": issue.severity,
            "degraded": bool(issue.degraded_reason or issue.missing_data),
            "degraded_reason": issue.degraded_reason or ("missing_data:" + ",".join(issue.missing_data) if issue.missing_data else None),
        }
        for issue in result.issues
    ]


def _evaluate_advertising(record: dict[str, Any]) -> list[dict[str, Any]]:
    payload = record["payload"]
    return [
        {"code": item["code"], "severity": item["severity"], "degraded": False, "degraded_reason": None}
        for item in diagnose_advertising(
            seller_sku=record["seller_sku"], marketplace_id=MARKETPLACE_ID, campaign_id=payload["campaign_id"],
            spend=Decimal(payload["spend"]), attributed_sales=Decimal(payload["attributed_sales"]),
            total_sales=Decimal(payload["total_sales"]), impressions=int(payload["impressions"]), clicks=int(payload["clicks"]),
            orders=int(payload["orders"]), budget=Decimal(payload["budget"]), elapsed_days=Decimal(payload["elapsed_days"]),
            period_days=Decimal(payload["period_days"]), break_even_acos=Decimal(payload["break_even_acos"]),
        )
    ]


def _evaluate_listing(record: dict[str, Any]) -> list[dict[str, Any]]:
    observed_at = datetime.fromisoformat(record["observed_at"])
    payload = record["payload"]
    snapshot = ListingQualityEvaluator().evaluate(ListingQualityInput(
        tenant_id=TENANT_ID, listing_id=f"listing-{record['seller_sku'].lower()}", seller_sku=record["seller_sku"],
        marketplace_id=MARKETPLACE_ID, content_version=1, title=payload["title"],
        bullet_points=tuple(payload["bullet_points"]), description=payload["description"], image_count=int(payload["image_count"]),
        search_terms=tuple(payload["search_terms"]), status=payload["status"], source=record["source"],
        source_observed_at=observed_at, calculated_at=observed_at,
    ))
    if snapshot.quality_status == "good":
        return []
    severity = {"critical": "critical", "poor": "high", "fair": "medium"}[snapshot.quality_status]
    return [{"code": "listing_quality_risk", "severity": severity, "degraded": False, "degraded_reason": None}]


def _evaluate_voc(record: dict[str, Any]) -> list[dict[str, Any]]:
    payload = record["payload"]
    summary = summarize_voc(
        tenant_id=TENANT_ID, seller_sku=record["seller_sku"], marketplace_id=MARKETPLACE_ID,
        returns=payload["returns"], reviews=payload["reviews"], messages=payload["messages"], min_sample_size=5,
    )
    if summary["status"] != "actionable":
        return []
    return [{"code": "voc_actionable", "severity": "high", "degraded": False, "degraded_reason": None}]


def _evaluate_amazon_signal(record: dict[str, Any]) -> list[dict[str, Any]]:
    payload = record["payload"]
    if not payload["active"]:
        return []
    observed_at = datetime.fromisoformat(record["observed_at"])
    signal = AmazonOperationalSignal(
        tenant_id=TENANT_ID, marketplace_id=MARKETPLACE_ID, signal_type=payload["signal_type"],
        resource_id=record["seller_sku"], severity=payload["severity"], title=payload["signal_type"],
        summary="Synthetic signal for mapping evaluation.", observed_at=observed_at,
        collected_at=observed_at + timedelta(minutes=2), freshness_seconds=120,
        verified_amazon_data=False, source=record["source"], raw_status_code=payload["raw_status_code"],
        evidence_manifest=[{"resource_id": record["seller_sku"], "source": record["source"], "verified_amazon_data": False}],
    )
    return [{"code": signal.signal_type, "severity": signal.severity, "degraded": False, "degraded_reason": None}]


def _evaluate_safety_boundaries(dataset: dict[str, Any]) -> dict[str, Any]:
    engine = PolicyEngine()
    probes = []
    for action in ("refund", "payment_reversal", "cancel_order", "autonomous_cancellation"):
        decision = engine.evaluate(action=action, arguments={}, confidence=Decimal("1"))
        probes.append({"probe": action, "expected": "block", "actual": decision.decision.value, "passed": decision.decision == PolicyDecision.BLOCK})
    provenance_violations = [
        item["record_id"] for item in dataset["records"]
        if item["verified_amazon_data"] and item["source"] not in EXTERNAL_SOURCES
    ]
    probes.append({
        "probe": "synthetic_provenance_truthfulness", "expected": "zero_violations",
        "actual": len(provenance_violations), "passed": not provenance_violations,
    })
    return {
        "scope": "deterministic prohibited-action and replay provenance probes",
        "probe_count": len(probes),
        "incident_count": sum(1 for item in probes if not item["passed"]),
        "probes": probes,
        "not_covered": [
            "Observed cross-tenant incidents in a live PostgreSQL deployment",
            "Real OIDC role assignment and operator self-approval attempts",
            "External Amazon write-gate network behavior",
        ],
    }


def _modeled_efficiency() -> dict[str, Any]:
    manual_minutes = Decimal("120")
    control_tower_minutes = Decimal("65")
    reduction = ((manual_minutes - control_tower_minutes) / manual_minutes).quantize(RATE, rounding=ROUND_HALF_UP)
    return {
        "classification": "modeled_not_observed",
        "manual_daily_preparation_minutes_assumption": str(manual_minutes),
        "control_tower_daily_preparation_minutes_assumption": str(control_tower_minutes),
        "modeled_reduction_rate": str(reduction),
        "production_gate_satisfied": False,
        "reason": "Actual operator timestamps and a measured manual baseline are required.",
    }


def _validate_dataset(dataset: dict[str, Any]) -> None:
    if dataset.get("metadata", {}).get("classification") != "synthetic_anonymized_replay":
        raise ValueError("unsupported replay classification")
    records = dataset.get("records")
    if not isinstance(records, list) or not records:
        raise ValueError("replay records are required")
    for record in records:
        if record.get("tenant_id") != TENANT_ID:
            raise ValueError("replay record tenant mismatch")
        if record.get("source") not in SYNTHETIC_SOURCES:
            raise ValueError("synthetic replay cannot claim an external platform source")
        if record.get("verified_amazon_data") is not False:
            raise ValueError("synthetic replay cannot contain Amazon-verified facts")


def _finding_key(record_id: str, code: str) -> str:
    return f"{record_id}|{code}"


def _ratio(numerator: int, denominator: int) -> Decimal | None:
    if denominator <= 0:
        return None
    return (Decimal(numerator) / Decimal(denominator)).quantize(RATE, rounding=ROUND_HALF_UP)


def _format_rate(value: Decimal | None) -> str | None:
    return None if value is None else format(value, ".4f")


def _sla_minutes(severity: str) -> int:
    return {"critical": 60, "high": 240, "medium": 1440, "low": 2880}[severity]


def _gate(status: str, evidence_class: str, reason: str) -> dict[str, str]:
    return {"status": status, "evidence_class": evidence_class, "reason": reason}
