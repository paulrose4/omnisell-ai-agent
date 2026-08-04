from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from statistics import mean
from typing import Any

DEFAULT_COMPETITORS = ["Shopee Mall", "Lazada Choice", "TikTok Shop", "Amazon Global"]


def _stable_ratio(seed: str) -> float:
    digest = hashlib.sha256(seed.encode("utf-8")).hexdigest()
    value = int(digest[:8], 16)
    return value / 0xFFFFFFFF


def fetch_competitor_prices(
    product_id: str,
    current_price: float,
    currency: str = "USD",
    market: str = "SG",
    competitors: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Return deterministic mock competitor prices for a product and market."""
    if current_price <= 0:
        raise ValueError("current_price must be greater than 0.")

    resolved_competitors = competitors or DEFAULT_COMPETITORS
    fetched_at = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    prices: list[dict[str, Any]] = []

    for competitor in resolved_competitors:
        ratio = _stable_ratio(f"{product_id}:{market}:{competitor}")
        multiplier = 0.82 + ratio * 0.36
        competitor_price = round(current_price * multiplier, 2)
        prices.append(
            {
                "product_id": product_id,
                "competitor": competitor,
                "market": market,
                "price": competitor_price,
                "currency": currency,
                "url": f"https://mock-prices.example.com/{market.lower()}/{competitor.lower().replace(' ', '-')}/{product_id}",
                "fetched_at": fetched_at,
            }
        )

    return prices


def generate_price_recommendation(
    product_id: str,
    current_price: float,
    competitor_prices: list[dict[str, Any]] | None = None,
    currency: str = "USD",
    market: str = "SG",
    min_margin_rate: float = 0.12,
) -> dict[str, Any]:
    """Analyze competitor prices and return an action recommendation with HITL approval requirements."""
    if current_price <= 0:
        raise ValueError("current_price must be greater than 0.")
    if min_margin_rate < 0 or min_margin_rate >= 1:
        raise ValueError("min_margin_rate must be between 0 and 1.")

    prices = competitor_prices or fetch_competitor_prices(
        product_id=product_id, current_price=current_price, currency=currency, market=market
    )
    numeric_prices = [float(item["price"]) for item in prices if float(item["price"]) > 0]
    if not numeric_prices:
        raise ValueError("competitor_prices must contain at least one positive price.")

    min_competitor_price = min(numeric_prices)
    avg_competitor_price = mean(numeric_prices)
    target_price = round(min_competitor_price * 0.99, 2)
    floor_price = round(current_price * min_margin_rate, 2)
    recommended_price = max(target_price, floor_price)
    change_rate = (recommended_price - current_price) / current_price
    abs_change_rate = abs(change_rate)

    if abs_change_rate < 0.05:
        action = "auto_update"
        approval_required = False
        notification_required = False
        approval_reason = "Price change is below 5%; automatic update is allowed."
    elif abs_change_rate <= 0.10:
        action = "notify_operator"
        approval_required = False
        notification_required = True
        approval_reason = "Price change is between 5% and 10%; notify operator before execution."
    else:
        action = "require_human_approval"
        approval_required = True
        notification_required = True
        approval_reason = "Price change exceeds 10%; HITL approval is required before any update."

    if recommended_price <= 0:
        action = "require_human_approval"
        approval_required = True
        notification_required = True
        approval_reason = "Recommended price is invalid; human approval is required."

    return {
        "product_id": product_id,
        "current_price": round(current_price, 2),
        "recommended_price": round(recommended_price, 2),
        "currency": currency,
        "market": market,
        "change_rate": round(change_rate, 4),
        "change_percent": round(change_rate * 100, 2),
        "min_competitor_price": round(min_competitor_price, 2),
        "avg_competitor_price": round(avg_competitor_price, 2),
        "competitor_prices": prices,
        "action": action,
        "approval_required": approval_required,
        "notification_required": notification_required,
        "approval_reason": approval_reason,
    }


if __name__ == "__main__":
    sample_prices = fetch_competitor_prices("P1001", 49.99)
    print(sample_prices)
    print(generate_price_recommendation("P1001", 49.99, sample_prices))
