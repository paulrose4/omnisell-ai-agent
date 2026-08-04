from __future__ import annotations

import json
import random
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from typing import Any

MARKETPLACE_ID = "ATVPDKIKX0DER"
SOURCE = "mock"
MONEY = Decimal("0.01")

SCENARIOS = (
    "healthy",
    "high_acos",
    "low_acos_contribution_loss",
    "waste_clicks",
    "budget_exhaustion",
    "inventory_guard",
    "packaging_damage",
    "wrong_or_missing_item",
    "listing_mismatch",
    "planned_promotion_suppression",
)

CUSTOMER_INTENTS = (
    "where_is_my_order",
    "delivery_delay",
    "cancel_request",
    "refund_demand",
    "damaged_item",
    "wrong_item",
    "missing_accessory",
    "usage_question",
    "invoice_request",
    "negative_sentiment",
    "off_platform_contact",
    "review_incentive",
)

MESSAGE_TEXT = {
    "where_is_my_order": "Where is my order? The carrier tracking has not changed.",
    "delivery_delay": "My order is late and I need a delivery update today.",
    "cancel_request": "Please cancel this order before it arrives.",
    "refund_demand": "I want a full refund immediately.",
    "damaged_item": "The item and retail package arrived damaged. Can you help me?",
    "wrong_item": "I received a different item from the one shown in my order.",
    "missing_accessory": "A required accessory is missing from the package.",
    "usage_question": "How should I use and clean this product safely?",
    "invoice_request": "Please provide the available invoice for this order.",
    "negative_sentiment": "This experience is frustrating and I still need a clear answer.",
    "off_platform_contact": "Can we continue this conversation on WhatsApp?",
    "review_incentive": "Will you refund me if I remove my negative review?",
}

VOC_REASON_BY_SCENARIO = {
    "packaging_damage": "packaging_problem",
    "wrong_or_missing_item": "wrong_or_missing_item",
    "listing_mismatch": "listing_mismatch",
    "inventory_guard": "shipping_damage",
    "planned_promotion_suppression": "subjective_dissatisfaction",
}


@dataclass(frozen=True)
class HighFidelityStoreConfig:
    seed: int = 20260722
    days: int = 30
    sku_count: int = 120
    order_count: int = 12000
    campaign_count: int = 60
    search_term_count: int = 3500
    return_count: int = 700
    message_count: int = 1000
    review_count: int = 400
    customer_case_count: int = 350
    business_context_count: int = 75

    def __post_init__(self) -> None:
        values = (
            self.days,
            self.sku_count,
            self.order_count,
            self.campaign_count,
            self.search_term_count,
            self.return_count,
            self.message_count,
            self.review_count,
            self.customer_case_count,
            self.business_context_count,
        )
        if any(value <= 0 for value in values):
            raise ValueError("high-fidelity dataset counts must be positive")
        if self.sku_count < len(SCENARIOS):
            raise ValueError("sku_count must cover every ground-truth scenario")
        if self.campaign_count > self.sku_count:
            raise ValueError("campaign_count cannot exceed sku_count")
        if self.customer_case_count > self.message_count:
            raise ValueError("customer_case_count cannot exceed message_count")


@dataclass(frozen=True)
class HighFidelityStoreDataset:
    config: HighFidelityStoreConfig
    reference_time: datetime
    catalog: list[dict[str, Any]]
    orders: list[dict[str, Any]]
    inventory_snapshots: list[dict[str, Any]]
    advertising_snapshots: list[dict[str, Any]]
    search_terms: list[dict[str, Any]]
    return_events: list[dict[str, Any]]
    customer_messages: list[dict[str, Any]]
    review_observations: list[dict[str, Any]]
    customer_cases: list[dict[str, Any]]
    business_contexts: list[dict[str, Any]]
    data_quality_events: list[dict[str, Any]]
    action_trials: list[dict[str, Any]]
    ground_truth: list[dict[str, Any]]

    @property
    def counts(self) -> dict[str, int]:
        return {
            "skus": len(self.catalog),
            "orders": len(self.orders),
            "inventory_snapshots": len(self.inventory_snapshots),
            "campaign_daily_snapshots": len(self.advertising_snapshots),
            "search_terms": len(self.search_terms),
            "returns": len(self.return_events),
            "messages": len(self.customer_messages),
            "reviews": len(self.review_observations),
            "customer_cases": len(self.customer_cases),
            "business_contexts": len(self.business_contexts),
            "data_quality_events": len(self.data_quality_events),
            "action_trials": len(self.action_trials),
            "ground_truth": len(self.ground_truth),
        }

    @property
    def fingerprint(self) -> str:
        payload = {
            "config": self.config.__dict__,
            "reference_time": self.reference_time.isoformat(),
            "records": self.all_records(),
        }
        return sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()

    def all_records(self) -> list[dict[str, Any]]:
        return [
            *self.catalog,
            *self.orders,
            *self.inventory_snapshots,
            *self.advertising_snapshots,
            *self.search_terms,
            *self.return_events,
            *self.customer_messages,
            *self.review_observations,
            *self.customer_cases,
            *self.business_contexts,
            *self.data_quality_events,
            *self.action_trials,
            *self.ground_truth,
        ]

    def manifest(self) -> dict[str, Any]:
        return {
            "dataset": "omnisell-high-fidelity-amazon-us-30d",
            "seed": self.config.seed,
            "reference_time": self.reference_time.isoformat().replace("+00:00", "Z"),
            "marketplace_id": MARKETPLACE_ID,
            "source": SOURCE,
            "verified_amazon_data": False,
            "pii_policy": "fully_synthetic_no_customer_identifiers",
            "counts": self.counts,
            "fingerprint": self.fingerprint,
        }


def generate_high_fidelity_store(
    *,
    config: HighFidelityStoreConfig | None = None,
    reference_time: datetime | None = None,
) -> HighFidelityStoreDataset:
    config = config or HighFidelityStoreConfig()
    reference_time = reference_time or datetime.now(UTC).replace(microsecond=0)
    if reference_time.tzinfo is None:
        raise ValueError("reference_time must be timezone-aware")
    reference_time = reference_time.astimezone(UTC)
    rng = random.Random(config.seed)
    common = {"source": SOURCE, "verified_amazon_data": False, "marketplace_id": MARKETPLACE_ID}

    catalog = _catalog(config, rng, common)
    catalog_by_sku = {item["seller_sku"]: item for item in catalog}
    inventory_snapshots = _inventory(config, reference_time, catalog, rng, common)
    orders = _orders(config, reference_time, catalog, rng, common)
    advertising_snapshots = _advertising(config, reference_time, catalog, rng, common)
    search_terms = _search_terms(config, reference_time, advertising_snapshots, rng, common)
    return_events = _returns(config, reference_time, orders, catalog_by_sku, rng, common)
    customer_messages = _messages(config, reference_time, orders, catalog_by_sku, common)
    review_observations = _reviews(config, reference_time, catalog, rng, common)
    business_contexts = _contexts(config, reference_time, catalog, common)
    data_quality_events = _data_quality_events(reference_time, catalog, advertising_snapshots, common)
    action_trials = _action_trials(reference_time, catalog, common)
    ground_truth = [_ground_truth(item, common) for item in catalog]
    customer_cases = [
        {
            **common,
            "case_id": f"UAT-CASE-{index + 1:05d}",
            "external_message_id": message["external_message_id"],
            "external_order_id": message["external_order_id"],
            "seller_sku": message["seller_sku"],
            "scenario_id": message["scenario_id"],
            "expected_policy_decision": _customer_policy(message["scenario_id"]),
            "expected_route": _customer_route(message["scenario_id"]),
            "baseline_minutes": 8 + index % 13,
            "omnisell_target_minutes": 5 + index % 7,
            "status": "not_started",
        }
        for index, message in enumerate(customer_messages[: config.customer_case_count])
    ]
    return HighFidelityStoreDataset(
        config=config,
        reference_time=reference_time,
        catalog=catalog,
        orders=orders,
        inventory_snapshots=inventory_snapshots,
        advertising_snapshots=advertising_snapshots,
        search_terms=search_terms,
        return_events=return_events,
        customer_messages=customer_messages,
        review_observations=review_observations,
        customer_cases=customer_cases,
        business_contexts=business_contexts,
        data_quality_events=data_quality_events,
        action_trials=action_trials,
        ground_truth=ground_truth,
    )


def _catalog(config: HighFidelityStoreConfig, rng: random.Random, common: dict[str, Any]) -> list[dict[str, Any]]:
    categories = ("Home", "Kitchen", "Outdoors", "Pet Supplies", "Beauty", "Office")
    adjectives = ("Everyday", "Compact", "Premium", "Classic", "Durable", "Essential")
    nouns = ("Organizer", "Bottle", "Travel Kit", "Storage Set", "Care Tool", "Desk Accessory")
    records = []
    for index in range(config.sku_count):
        scenario = SCENARIOS[index % len(SCENARIOS)]
        price = (Decimal("14.99") + Decimal(index % 18) * Decimal("2.50")).quantize(MONEY)
        records.append(
            {
                **common,
                "seller_sku": f"HF-{index + 1:04d}",
                "asin": f"B0HF{index + 1:06d}",
                "fnsku": f"X00HF{index + 1:05d}",
                "title": f"{adjectives[index % len(adjectives)]} {nouns[index % len(nouns)]} {index + 1}",
                "category": categories[index % len(categories)],
                "scenario_id": scenario,
                "currency": "USD",
                "price": str(price),
                "product_cost": str((price * Decimal("0.28") + Decimal(rng.randrange(0, 150)) / 100).quantize(MONEY)),
                "fulfillment_channel": "AFN" if index % 8 else "MFN",
                "lifecycle": ("launch" if index % 11 == 0 else "mature" if index % 5 else "seasonal"),
            }
        )
    return records


def _inventory(
    config: HighFidelityStoreConfig,
    reference_time: datetime,
    catalog: list[dict[str, Any]],
    rng: random.Random,
    common: dict[str, Any],
) -> list[dict[str, Any]]:
    records = []
    for item in catalog:
        base = 80 + rng.randrange(0, 180)
        daily_sales = 2 + rng.randrange(0, 8)
        for day in range(config.days):
            observed = reference_time - timedelta(days=config.days - 1 - day, minutes=5)
            available = max(0, base - daily_sales * day + (120 if day and day % 14 == 0 else 0))
            if item["scenario_id"] == "inventory_guard" and day >= config.days - 3:
                available = max(0, 2 - (day - (config.days - 3)))
            records.append(
                {
                    **common,
                    "seller_sku": item["seller_sku"],
                    "asin": item["asin"],
                    "observed_at": observed.isoformat(),
                    "available": available,
                    "reserved": 1 + day % 4,
                    "inbound": 0 if item["scenario_id"] == "inventory_guard" else (40 if day % 14 == 0 else 0),
                    "unsellable": 1 if item["scenario_id"] == "packaging_damage" else 0,
                    "daily_sales": daily_sales,
                    "inventory_status": "critical_stockout" if available <= 3 else "healthy",
                }
            )
    return records


def _orders(
    config: HighFidelityStoreConfig,
    reference_time: datetime,
    catalog: list[dict[str, Any]],
    rng: random.Random,
    common: dict[str, Any],
) -> list[dict[str, Any]]:
    statuses = ("Shipped",) * 92 + ("Pending",) * 3 + ("Canceled",) * 3 + ("Unshipped",) * 2
    records = []
    for index in range(config.order_count):
        item = catalog[rng.randrange(len(catalog))]
        quantity = 1 if rng.random() < 0.86 else 2 if rng.random() < 0.9 else 3
        purchased = reference_time - timedelta(days=index % config.days, minutes=(index * 17) % 1440)
        status = statuses[index % len(statuses)]
        if item["scenario_id"] == "listing_mismatch" and index % 97 == 0:
            status = "UnexpectedPlatformState"
        amount = (Decimal(item["price"]) * quantity).quantize(MONEY)
        records.append(
            {
                **common,
                "AmazonOrderId": f"HF-ORDER-{index + 1:08d}",
                "MarketplaceId": MARKETPLACE_ID,
                "OrderStatus": status,
                "FulfillmentChannel": item["fulfillment_channel"],
                "PurchaseDate": purchased.isoformat(),
                "LastUpdateDate": (purchased + timedelta(hours=2)).isoformat(),
                "OrderItems": [
                    {
                        "SellerSKU": item["seller_sku"],
                        "QuantityOrdered": quantity,
                        "Title": item["title"],
                        "OrderItemId": f"HF-ITEM-{index + 1:08d}",
                        "ItemPrice": {"Amount": str(amount), "CurrencyCode": "USD"},
                    }
                ],
                "OmniSellMockMetadata": {
                    "data_source": "mock_amazon",
                    "verified_amazon_data": False,
                    "scenario_id": item["scenario_id"],
                    "seller_sku": item["seller_sku"],
                },
            }
        )
    return records


def _advertising(
    config: HighFidelityStoreConfig,
    reference_time: datetime,
    catalog: list[dict[str, Any]],
    rng: random.Random,
    common: dict[str, Any],
) -> list[dict[str, Any]]:
    records = []
    for campaign_index, item in enumerate(catalog[: config.campaign_count]):
        campaign_id = f"HF-SP-{campaign_index + 1:04d}"
        for day in range(config.days):
            start = (reference_time - timedelta(days=config.days - day)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            end = start + timedelta(days=1) - timedelta(seconds=1)
            scenario = item["scenario_id"]
            impressions = 2500 + rng.randrange(0, 5000)
            clicks = max(5, int(impressions * (0.009 + (campaign_index % 5) * 0.002)))
            orders = max(0, int(clicks * (0.08 + (campaign_index % 4) * 0.025)))
            spend = (Decimal(clicks) * (Decimal("0.70") + Decimal(campaign_index % 7) * Decimal("0.11"))).quantize(
                MONEY
            )
            sales = (Decimal(orders) * Decimal(item["price"])).quantize(MONEY)
            budget = Decimal("100.00")
            contribution = (sales * Decimal("0.32") - spend).quantize(MONEY)
            inventory_status = "healthy"
            if scenario == "high_acos":
                spend, sales, contribution = Decimal("100"), Decimal("125"), Decimal("-38")
            elif scenario == "low_acos_contribution_loss":
                spend, sales, contribution = Decimal("40"), Decimal("400"), Decimal("-12.50")
            elif scenario == "waste_clicks":
                spend, sales, orders, clicks, contribution = Decimal("55"), Decimal("0"), 0, 32, Decimal("-55")
            elif scenario == "budget_exhaustion":
                spend, budget = Decimal("70"), Decimal("60")
            elif scenario == "inventory_guard":
                spend, sales, contribution, inventory_status = (
                    Decimal("40"),
                    Decimal("400"),
                    Decimal("50"),
                    "critical_stockout",
                )
            records.append(
                {
                    **common,
                    "seller_sku": item["seller_sku"],
                    "entity_type": "campaign",
                    "entity_id": campaign_id,
                    "campaign_id": campaign_id,
                    "period_start": start.isoformat(),
                    "period_end": end.isoformat(),
                    "observed_at": end.isoformat(),
                    "collected_at": (end + timedelta(hours=4)).isoformat(),
                    "currency": "USD",
                    "spend": str(spend.quantize(MONEY)),
                    "attributed_sales": str(sales.quantize(MONEY)),
                    "total_sales": str((sales * Decimal("1.35")).quantize(MONEY)),
                    "budget": str(budget.quantize(MONEY)),
                    "impressions": impressions,
                    "clicks": clicks,
                    "orders": orders,
                    "elapsed_days": "1",
                    "period_days": "1",
                    "break_even_acos": "0.30",
                    "contribution_profit": str(contribution.quantize(MONEY)),
                    "inventory_status": inventory_status,
                    "scenario_id": scenario,
                    "evidence_manifest": [{"source": SOURCE, "verified_amazon_data": False, "scenario_id": scenario}],
                }
            )
    return records


def _search_terms(
    config: HighFidelityStoreConfig,
    reference_time: datetime,
    snapshots: list[dict[str, Any]],
    rng: random.Random,
    common: dict[str, Any],
) -> list[dict[str, Any]]:
    latest_by_campaign = {item["campaign_id"]: item for item in snapshots}
    campaigns = list(latest_by_campaign.values())
    records = []
    for index in range(config.search_term_count):
        campaign = campaigns[index % len(campaigns)]
        waste = index % 17 == 0 or campaign["scenario_id"] == "waste_clicks"
        impressions = 350 + rng.randrange(0, 2200)
        clicks = 20 + index % 35 if waste else 2 + index % 18
        orders = 0 if waste else max(1, clicks // 8)
        spend = (Decimal(clicks) * Decimal("0.92")).quantize(MONEY)
        sales = Decimal("0") if waste else (Decimal(orders) * Decimal("24.99")).quantize(MONEY)
        records.append(
            {
                **common,
                "seller_sku": campaign["seller_sku"],
                "campaign_id": campaign["campaign_id"],
                "ad_group_id": f"HF-AG-{index % max(1, len(campaigns)) + 1:04d}",
                "search_term": f"{'irrelevant replacement' if waste else 'relevant product'} term {index + 1}",
                "period_start": (reference_time - timedelta(days=config.days)).isoformat(),
                "period_end": reference_time.isoformat(),
                "observed_at": reference_time.isoformat(),
                "collected_at": (reference_time + timedelta(hours=4)).isoformat(),
                "currency": "USD",
                "spend": str(spend),
                "attributed_sales": str(sales),
                "impressions": impressions,
                "clicks": clicks,
                "orders": orders,
                "scenario_id": "waste_clicks" if waste else campaign["scenario_id"],
                "evidence_manifest": [{"source": SOURCE, "verified_amazon_data": False}],
            }
        )
    return records


def _returns(
    config: HighFidelityStoreConfig,
    reference_time: datetime,
    orders: list[dict[str, Any]],
    catalog: dict[str, dict[str, Any]],
    rng: random.Random,
    common: dict[str, Any],
) -> list[dict[str, Any]]:
    shipped = [item for item in orders if item["OrderStatus"] == "Shipped"]
    records = []
    reasons = (
        "product_defect",
        "size_or_fit",
        "shipping_damage",
        "packaging_problem",
        "wrong_or_missing_item",
        "instructions_unclear",
        "listing_mismatch",
        "subjective_dissatisfaction",
    )
    for index in range(config.return_count):
        order = shipped[(index * 13) % len(shipped)]
        sku = order["OrderItems"][0]["SellerSKU"]
        scenario = catalog[sku]["scenario_id"]
        reason = VOC_REASON_BY_SCENARIO.get(scenario, reasons[index % len(reasons)])
        records.append(
            {
                **common,
                "external_return_id": f"HF-RETURN-{index + 1:06d}",
                "external_order_id": order["AmazonOrderId"],
                "seller_sku": sku,
                "reason_code": reason,
                "quantity": 1,
                "return_amount": order["OrderItems"][0]["ItemPrice"]["Amount"],
                "currency": "USD",
                "observed_at": (reference_time - timedelta(days=index % config.days, hours=3)).isoformat(),
                "scenario_id": scenario,
                "evidence_manifest": [
                    {"source": SOURCE, "verified_amazon_data": False, "external_order_id": order["AmazonOrderId"]}
                ],
            }
        )
    return records


def _messages(
    config: HighFidelityStoreConfig,
    reference_time: datetime,
    orders: list[dict[str, Any]],
    catalog: dict[str, dict[str, Any]],
    common: dict[str, Any],
) -> list[dict[str, Any]]:
    records = []
    for index in range(config.message_count):
        order = orders[(index * 19) % len(orders)]
        sku = order["OrderItems"][0]["SellerSKU"]
        scenario_id = CUSTOMER_INTENTS[index % len(CUSTOMER_INTENTS)]
        scenario = catalog[sku]["scenario_id"]
        tag = VOC_REASON_BY_SCENARIO.get(
            scenario, "instructions_unclear" if scenario_id == "usage_question" else "subjective_dissatisfaction"
        )
        records.append(
            {
                **common,
                "external_message_id": f"HF-MSG-{index + 1:06d}",
                "external_order_id": order["AmazonOrderId"],
                "seller_sku": sku,
                "scenario_id": scenario_id,
                "language": "en-US",
                "message": MESSAGE_TEXT[scenario_id],
                "tag": tag,
                "confidence": "0.96",
                "observed_at": (reference_time - timedelta(days=index % config.days, minutes=index % 720)).isoformat(),
                "evidence_manifest": [
                    {"source": SOURCE, "verified_amazon_data": False, "external_order_id": order["AmazonOrderId"]}
                ],
            }
        )
    return records


def _reviews(
    config: HighFidelityStoreConfig,
    reference_time: datetime,
    catalog: list[dict[str, Any]],
    rng: random.Random,
    common: dict[str, Any],
) -> list[dict[str, Any]]:
    records = []
    for index in range(config.review_count):
        item = catalog[(index * 7) % len(catalog)]
        reason = VOC_REASON_BY_SCENARIO.get(item["scenario_id"], "subjective_dissatisfaction")
        rating = 1 + index % 3 if reason != "subjective_dissatisfaction" else 3 + index % 3
        records.append(
            {
                **common,
                "external_review_id": f"HF-REVIEW-{index + 1:06d}",
                "seller_sku": item["seller_sku"],
                "rating": rating,
                "review_text": f"Synthetic product feedback for {reason.replace('_', ' ')} scenario.",
                "reason_code": reason,
                "observed_at": (
                    reference_time - timedelta(days=index % config.days, minutes=rng.randrange(0, 600))
                ).isoformat(),
                "evidence_manifest": [{"source": SOURCE, "verified_amazon_data": False}],
            }
        )
    return records


def _contexts(
    config: HighFidelityStoreConfig, reference_time: datetime, catalog: list[dict[str, Any]], common: dict[str, Any]
) -> list[dict[str, Any]]:
    types = (
        "planned_promotion",
        "seasonality",
        "planned_stockout",
        "price_change",
        "listing_change",
        "advertising_change",
    )
    records = []
    for index in range(config.business_context_count):
        item = catalog[index % len(catalog)]
        context_type = (
            "planned_promotion" if item["scenario_id"] == "planned_promotion_suppression" else types[index % len(types)]
        )
        start = reference_time - timedelta(days=(index % config.days) + 1)
        records.append(
            {
                **common,
                "seller_sku": item["seller_sku"],
                "context_type": context_type,
                "starts_at": start.isoformat(),
                "ends_at": (start + timedelta(days=3)).isoformat(),
                "details": {
                    "scenario_id": item["scenario_id"],
                    "expected_suppression": context_type in {"planned_promotion", "planned_stockout"},
                },
            }
        )
    return records


def _data_quality_events(
    reference_time: datetime,
    catalog: list[dict[str, Any]],
    snapshots: list[dict[str, Any]],
    common: dict[str, Any],
) -> list[dict[str, Any]]:
    """Amazon-shaped delivery defects kept separate from business performance facts."""
    latest_by_campaign = {item["campaign_id"]: item for item in snapshots}
    campaigns = list(latest_by_campaign.values())
    base = reference_time.replace(hour=0, minute=0, second=0, microsecond=0)
    definitions = (
        {
            "event_type": "late_report",
            "seller_sku": catalog[0]["seller_sku"],
            "campaign_id": campaigns[0 % len(campaigns)]["campaign_id"],
            "report_date": (base - timedelta(days=2)).date().isoformat(),
            "observed_at": (base - timedelta(days=1)).isoformat(),
            "collected_at": (base + timedelta(hours=18)).isoformat(),
            "freshness_seconds": 151200,
            "expected_behavior": "degrade_freshness_no_business_alert",
        },
        {
            "event_type": "missing_report_partition",
            "seller_sku": catalog[1]["seller_sku"],
            "campaign_id": campaigns[1 % len(campaigns)]["campaign_id"],
            "report_date": (base - timedelta(days=3)).date().isoformat(),
            "observed_at": (base - timedelta(days=2)).isoformat(),
            "collected_at": base.isoformat(),
            "missing_fields": ["attributed_sales", "orders"],
            "expected_behavior": "mark_incomplete_no_business_alert",
        },
        {
            "event_type": "report_restatement",
            "seller_sku": catalog[2]["seller_sku"],
            "campaign_id": campaigns[2 % len(campaigns)]["campaign_id"],
            "report_date": (base - timedelta(days=7)).date().isoformat(),
            "observed_at": (base - timedelta(days=7)).isoformat(),
            "collected_at": base.isoformat(),
            "report_version": 2,
            "supersedes_version": 1,
            "previous_values": {"spend": "84.25", "attributed_sales": "211.80"},
            "restated_values": {"spend": "86.10", "attributed_sales": "228.40"},
            "expected_behavior": "replace_prior_version_and_recompute",
        },
        {
            "event_type": "cross_currency_quarantine",
            "seller_sku": catalog[3]["seller_sku"],
            "campaign_id": campaigns[3 % len(campaigns)]["campaign_id"],
            "report_date": (base - timedelta(days=1)).date().isoformat(),
            "observed_at": (base - timedelta(days=1)).isoformat(),
            "collected_at": base.isoformat(),
            "currency": "EUR",
            "account_currency": "USD",
            "expected_behavior": "quarantine_currency_mismatch",
        },
        {
            "event_type": "duplicate_notification",
            "seller_sku": catalog[4]["seller_sku"],
            "campaign_id": campaigns[4 % len(campaigns)]["campaign_id"],
            "report_date": base.date().isoformat(),
            "observed_at": base.isoformat(),
            "collected_at": (base + timedelta(minutes=3)).isoformat(),
            "external_event_id": "HF-NOTIFICATION-0001",
            "delivery_attempt": 2,
            "expected_behavior": "deduplicate_without_second_side_effect",
        },
    )
    records = []
    for index, definition in enumerate(definitions, start=1):
        idempotency_material = (
            definition.get("external_event_id") or f"{definition['event_type']}:{definition['report_date']}"
        )
        records.append(
            {
                **common,
                "event_id": f"HF-DQ-{index:04d}",
                "idempotency_key": sha256(str(idempotency_material).encode()).hexdigest(),
                "report_type": "SPONSORED_PRODUCTS_DAILY",
                "report_version": definition.get("report_version", 1),
                "currency": definition.get("currency", "USD"),
                **definition,
            }
        )
    return records


def _action_trials(
    reference_time: datetime,
    catalog: list[dict[str, Any]],
    common: dict[str, Any],
) -> list[dict[str, Any]]:
    baseline_start = reference_time - timedelta(days=28)
    baseline_end = reference_time - timedelta(days=15)
    observation_start = reference_time - timedelta(days=14)
    observation_end = reference_time
    definitions = (
        {
            "action_type": "decrease_bid",
            "primary_metric": "incremental_contribution_profit",
            "desired_direction": "increase",
            "baseline_metrics": {"incremental_contribution_profit": "1000.00", "acos": "0.42"},
            "result_metrics": {"incremental_contribution_profit": "880.00", "acos": "0.35"},
            "guardrail_metrics": {"orders_before": 82, "orders_after": 54},
            "confounders": ["competitor_price_change"],
            "control_metrics": {},
            "randomized_experiment": False,
            "minimum_sample_satisfied": True,
            "execution_verified": True,
            "expected_attribution_level": "observed_association",
            "expected_outcome_status": "guardrail_regression",
            "causal_claim_allowed": False,
            "recommended_decision": "rollback",
        },
        {
            "action_type": "add_negative_keyword",
            "primary_metric": "incremental_contribution_profit",
            "desired_direction": "increase",
            "baseline_metrics": {"incremental_contribution_profit": "1000.00", "wasted_spend": "240.00"},
            "result_metrics": {"incremental_contribution_profit": "1120.00", "wasted_spend": "96.00"},
            "guardrail_metrics": {"orders_before": 76, "orders_after": 78},
            "confounders": [],
            "control_metrics": {"baseline": "975.00", "result": "982.00"},
            "randomized_experiment": False,
            "minimum_sample_satisfied": True,
            "execution_verified": True,
            "expected_attribution_level": "quasi_experimental",
            "expected_outcome_status": "directional_improvement",
            "causal_claim_allowed": False,
            "recommended_decision": "keep",
        },
        {
            "action_type": "listing_content_experiment",
            "primary_metric": "incremental_contribution_profit",
            "desired_direction": "increase",
            "baseline_metrics": {"incremental_contribution_profit": "1000.00", "conversion_rate": "0.102"},
            "result_metrics": {"incremental_contribution_profit": "1250.00", "conversion_rate": "0.119"},
            "guardrail_metrics": {"return_rate_before": "0.061", "return_rate_after": "0.058"},
            "confounders": [],
            "control_metrics": {"baseline": "1005.00", "result": "1010.00"},
            "randomized_experiment": True,
            "minimum_sample_satisfied": True,
            "execution_verified": True,
            "expected_attribution_level": "causal_experiment",
            "expected_outcome_status": "directional_improvement",
            "causal_claim_allowed": True,
            "recommended_decision": "keep",
        },
        {
            "action_type": "increase_campaign_budget",
            "primary_metric": "incremental_contribution_profit",
            "desired_direction": "increase",
            "baseline_metrics": {"incremental_contribution_profit": "1000.00"},
            "result_metrics": {"incremental_contribution_profit": None},
            "guardrail_metrics": {"inventory_status": "critical_stockout"},
            "confounders": ["planned_promotion", "inventory_stockout"],
            "control_metrics": {},
            "randomized_experiment": False,
            "minimum_sample_satisfied": False,
            "execution_verified": True,
            "expected_attribution_level": "inconclusive",
            "expected_outcome_status": "metrics_incomplete",
            "causal_claim_allowed": False,
            "recommended_decision": "hold",
        },
    )
    records = []
    for index, definition in enumerate(definitions, start=1):
        seller_sku = catalog[index + 4]["seller_sku"]
        action_material = {
            "seller_sku": seller_sku,
            "action_type": definition["action_type"],
            "approved_change": definition["result_metrics"],
            "observation_end": observation_end.isoformat(),
        }
        records.append(
            {
                **common,
                "trial_id": f"HF-TRIAL-{index:04d}",
                "seller_sku": seller_sku,
                "action_hash": sha256(
                    json.dumps(action_material, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest(),
                "execution_mode": "mock_dry_run",
                "amazon_write_performed": False,
                "execution_evidence": {
                    "source": "mock_seller_central",
                    "verified": True,
                    "receipt_id": f"HF-RECEIPT-{index:04d}",
                },
                "baseline_start": baseline_start.isoformat(),
                "baseline_end": baseline_end.isoformat(),
                "observation_start": observation_start.isoformat(),
                "observation_end": observation_end.isoformat(),
                "currency": "USD",
                **definition,
            }
        )
    return records


def _ground_truth(item: dict[str, Any], common: dict[str, Any]) -> dict[str, Any]:
    mapping = {
        "healthy": (False, "low", None, "operations"),
        "high_acos": (True, "high", "acos_above_break_even", "growth"),
        "low_acos_contribution_loss": (True, "critical", "low_acos_but_contribution_loss", "finance"),
        "waste_clicks": (True, "medium", "click_waste_without_orders", "growth"),
        "budget_exhaustion": (True, "medium", "budget_projected_to_exhaust_early", "growth"),
        "inventory_guard": (True, "high", "inventory_guard_blocks_growth", "supply_chain"),
        "packaging_damage": (True, "high", "voc_packaging_problem", "supply_chain"),
        "wrong_or_missing_item": (True, "high", "voc_wrong_or_missing_item", "operations"),
        "listing_mismatch": (True, "high", "voc_listing_mismatch", "listing"),
        "planned_promotion_suppression": (False, "low", None, "growth"),
    }
    alert, severity, detector, route = mapping[item["scenario_id"]]
    return {
        **common,
        "seller_sku": item["seller_sku"],
        "scenario_id": item["scenario_id"],
        "expected_alert": alert,
        "expected_severity": severity,
        "expected_detector": detector,
        "expected_route": route,
        "expected_suppression": item["scenario_id"] == "planned_promotion_suppression",
    }


def _customer_policy(intent: str) -> str:
    if intent in {"refund_demand", "cancel_request", "off_platform_contact", "review_incentive"}:
        return "block"
    if intent in {"damaged_item", "wrong_item", "negative_sentiment"}:
        return "require_approval"
    return "allow"


def _customer_route(intent: str) -> str:
    if intent in {"damaged_item", "missing_accessory"}:
        return "supply_chain"
    if intent == "wrong_item":
        return "fulfillment"
    if intent == "usage_question":
        return "customer_service_content"
    if intent in {"refund_demand", "cancel_request", "review_incentive", "off_platform_contact"}:
        return "policy_review"
    return "customer_service"
