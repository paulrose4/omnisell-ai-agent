from __future__ import annotations

import argparse
import csv
from datetime import UTC, datetime
import json
from pathlib import Path
from typing import Any

from application.high_fidelity_mock import HighFidelityStoreConfig, generate_high_fidelity_store


UAT_CONFIG = HighFidelityStoreConfig(
    seed=20260722,
    days=30,
    sku_count=100,
    order_count=10000,
    campaign_count=50,
    search_term_count=3000,
    return_count=500,
    message_count=800,
    review_count=300,
    customer_case_count=300,
    business_context_count=50,
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a deterministic, PII-free Amazon US operations UAT dataset.")
    parser.add_argument("--output-dir", default="outputs/high_fidelity_amazon_us_30d")
    parser.add_argument("--reference-time", default=datetime.now(UTC).replace(microsecond=0).isoformat())
    args = parser.parse_args()

    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    reference_time = datetime.fromisoformat(args.reference_time.replace("Z", "+00:00"))
    dataset = generate_high_fidelity_store(config=UAT_CONFIG, reference_time=reference_time)

    tables = {
        "catalog": dataset.catalog,
        "orders": [_flatten_order(item) for item in dataset.orders],
        "inventory_snapshots": dataset.inventory_snapshots,
        "advertising_daily": dataset.advertising_snapshots,
        "search_terms": dataset.search_terms,
        "return_events": dataset.return_events,
        "customer_messages": dataset.customer_messages,
        "review_observations": dataset.review_observations,
        "customer_cases_uat": dataset.customer_cases,
        "business_contexts": dataset.business_contexts,
        "data_quality_events": dataset.data_quality_events,
        "action_trials": dataset.action_trials,
        "ground_truth": dataset.ground_truth,
    }
    for name, rows in tables.items():
        _write_csv(output_dir / f"{name}.csv", rows)

    with (output_dir / "mock_spapi_orders.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for order in dataset.orders:
            handle.write(json.dumps(order, ensure_ascii=False, separators=(",", ":")) + "\n")
    for name, rows in {
        "data_quality_events": dataset.data_quality_events,
        "action_trials": dataset.action_trials,
    }.items():
        (output_dir / f"{name}.json").write_text(
            json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8",
        )
    (output_dir / "manifest.json").write_text(
        json.dumps(dataset.manifest(), ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(json.dumps(dataset.manifest(), ensure_ascii=False, indent=2))


def _flatten_order(order: dict[str, Any]) -> dict[str, Any]:
    line = order["OrderItems"][0]
    metadata = order["OmniSellMockMetadata"]
    return {
        "AmazonOrderId": order["AmazonOrderId"],
        "MarketplaceId": order["MarketplaceId"],
        "OrderStatus": order["OrderStatus"],
        "FulfillmentChannel": order["FulfillmentChannel"],
        "PurchaseDate": order["PurchaseDate"],
        "LastUpdateDate": order["LastUpdateDate"],
        "SellerSKU": line["SellerSKU"],
        "QuantityOrdered": line["QuantityOrdered"],
        "Title": line["Title"],
        "OrderItemId": line["OrderItemId"],
        "ItemAmount": line["ItemPrice"]["Amount"],
        "CurrencyCode": line["ItemPrice"]["CurrencyCode"],
        "scenario_id": metadata["scenario_id"],
        "source": order["source"],
        "verified_amazon_data": order["verified_amazon_data"],
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    keys: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                keys.append(key)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: _cell(row.get(key)) for key in keys})


def _cell(value: Any) -> Any:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return value


if __name__ == "__main__":
    main()
