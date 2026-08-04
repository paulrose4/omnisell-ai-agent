from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any


AMAZON_SIGNAL_TYPES = {
    "account_health_violation",
    "buy_box_loss",
    "listing_suppressed",
    "stranded_inventory",
    "inbound_receiving_delay",
    "shipment_quantity_mismatch",
    "inventory_aged",
    "restock_limit_risk",
}


@dataclass(frozen=True)
class AmazonOperationalSignal:
    tenant_id: str
    marketplace_id: str
    signal_type: str
    resource_id: str
    severity: str
    title: str
    summary: str
    observed_at: datetime
    collected_at: datetime
    freshness_seconds: int | None
    verified_amazon_data: bool
    source: str
    raw_status_code: str
    evidence_manifest: list[dict[str, Any]]
    id: str | None = None

    def __post_init__(self) -> None:
        if self.signal_type not in AMAZON_SIGNAL_TYPES:
            raise ValueError("unsupported Amazon operational signal type")
        if self.severity not in {"low", "medium", "high", "critical"}:
            raise ValueError("invalid signal severity")
        if self.observed_at.tzinfo is None or self.collected_at.tzinfo is None:
            raise ValueError("signal timestamps must be timezone-aware")
        if self.collected_at < self.observed_at:
            raise ValueError("collected_at cannot precede observed_at")
        if self.freshness_seconds is None or self.freshness_seconds < 0:
            raise ValueError("freshness_seconds must be a non-negative value")
        if self.verified_amazon_data and not self.source.startswith("amazon_"):
            raise ValueError("only Amazon API sources may be marked verified_amazon_data")
        if not self.resource_id or not self.evidence_manifest:
            raise ValueError("Amazon signal requires a resource id and evidence manifest")


def calculate_advertising_metrics(
    *, spend: Decimal, attributed_sales: Decimal | None, total_sales: Decimal | None,
    impressions: int | None, clicks: int | None, orders: int | None,
) -> dict[str, Any]:
    missing_data: list[str] = []
    acos = _rate(spend, attributed_sales, "attributed_sales", missing_data)
    tacos = _rate(spend, total_sales, "total_sales", missing_data)
    ctr = _rate(Decimal(clicks or 0), Decimal(impressions or 0), "impressions", missing_data)
    cvr = _rate(Decimal(orders or 0), Decimal(clicks or 0), "clicks", missing_data)
    cpc = _rate(spend, Decimal(clicks or 0), "clicks", missing_data)
    return {
        "spend": spend, "attributed_sales": attributed_sales, "total_sales": total_sales,
        "impressions": impressions, "clicks": clicks, "orders": orders,
        "acos": acos, "tacos": tacos, "ctr": ctr, "cvr": cvr, "cpc": cpc,
        "missing_data": sorted(set(missing_data)),
    }


def diagnose_advertising(
    *, seller_sku: str, marketplace_id: str, campaign_id: str, spend: Decimal,
    attributed_sales: Decimal | None, total_sales: Decimal | None, impressions: int | None,
    clicks: int | None, orders: int | None, budget: Decimal | None, elapsed_days: Decimal,
    period_days: Decimal, break_even_acos: Decimal | None, min_clicks_without_order: int = 20,
) -> list[dict[str, Any]]:
    metrics = calculate_advertising_metrics(
        spend=spend, attributed_sales=attributed_sales, total_sales=total_sales,
        impressions=impressions, clicks=clicks, orders=orders,
    )
    result: list[dict[str, Any]] = []
    acos = metrics["acos"]
    if acos is not None and break_even_acos is not None and acos > break_even_acos:
        result.append({
            "code": "acos_above_break_even", "severity": "high",
            "title": "ACoS 高于盈亏平衡线", "seller_sku": seller_sku,
            "marketplace_id": marketplace_id, "campaign_id": campaign_id,
            "observed": str(acos), "threshold": str(break_even_acos),
        })
    if int(clicks or 0) >= min_clicks_without_order and int(orders or 0) == 0:
        result.append({
            "code": "click_waste_without_orders", "severity": "high",
            "title": "点击达到阈值但没有订单", "seller_sku": seller_sku,
            "marketplace_id": marketplace_id, "campaign_id": campaign_id,
            "clicks": int(clicks or 0), "threshold": min_clicks_without_order,
        })
    if budget is not None and elapsed_days > 0 and period_days > elapsed_days:
        projected_spend = spend / elapsed_days * period_days
        if projected_spend > budget:
            result.append({
                "code": "budget_projected_to_exhaust_early", "severity": "medium",
                "title": "预算预计提前耗尽", "seller_sku": seller_sku,
                "marketplace_id": marketplace_id, "campaign_id": campaign_id,
                "projected_spend": str(projected_spend), "budget": str(budget),
            })
    return result


def summarize_voc(
    *, tenant_id: str, seller_sku: str, marketplace_id: str,
    returns: list[dict[str, Any]], reviews: list[dict[str, Any]], messages: list[dict[str, Any]],
    min_sample_size: int = 5,
) -> dict[str, Any]:
    reason_counts: dict[str, int] = {}
    for event in returns:
        code = str(event.get("reason_code") or "unknown")
        reason_counts[code] = reason_counts.get(code, 0) + max(1, int(event.get("quantity") or 1))
    for event in messages:
        code = str(event.get("tag") or "unknown")
        reason_counts[code] = reason_counts.get(code, 0) + 1
    for review in reviews:
        code = str(review.get("reason_code") or ("low_rating" if int(review.get("rating") or 0) <= 3 else "review"))
        reason_counts[code] = reason_counts.get(code, 0) + 1
    sample_size = sum(reason_counts.values())
    top_reason_code = max(reason_counts, key=reason_counts.get) if reason_counts else None
    top_reason_count = reason_counts.get(top_reason_code, 0) if top_reason_code else 0
    status = "actionable" if sample_size >= min_sample_size and top_reason_code else "insufficient_sample"
    recommended_action = None
    if status == "actionable":
        recommended_action = {
            "title": f"调查 SKU {seller_sku} 的 {top_reason_code} 原因",
            "reason_code": top_reason_code,
            "expected_outcome": "完成产品、包装、Listing 或质检改进，并在后续周期验证退货率趋势。",
        }
    return {
        "tenant_id": tenant_id, "seller_sku": seller_sku, "marketplace_id": marketplace_id,
        "sample_size": sample_size, "reason_counts": reason_counts,
        "top_reason_code": top_reason_code, "top_reason_count": top_reason_count,
        "status": status, "recommended_action": recommended_action,
    }


def _rate(numerator: Decimal, denominator: Decimal | None, name: str, missing_data: list[str]) -> Decimal | None:
    if denominator is None or denominator <= 0:
        missing_data.append(name)
        return None
    try:
        return (numerator / denominator).quantize(Decimal("0.0001"))
    except (InvalidOperation, ZeroDivisionError):
        missing_data.append(name)
        return None
