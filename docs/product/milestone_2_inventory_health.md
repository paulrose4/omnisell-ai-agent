# Milestone 2 — Inventory Health and Replenishment

## Scope delivered

- Effective-dated, versioned `InventoryPolicy` by tenant, marketplace and seller SKU.
- Deterministic 7/14/30-day weighted sales velocity with configurable weights.
- Manual seasonality and promotion multipliers.
- Inventory position, days of supply, expected stockout date, total lead time, safety stock, reorder point, MOQ/case-pack replenishment and excess units.
- Stockout/excess health classification, confidence, missing data, data warnings and automation eligibility.
- Optional revenue/profit-at-risk and extra-logistics-cost estimates when per-unit values are supplied.
- Persistent health snapshots, Alembic migration, PostgreSQL RLS registration, APIs, audit and HITL-gated policy updates.

## Deterministic formulas

```text
weighted_daily_sales
= normalized_weighted_average(sales_7d / 7, sales_14d / 14, sales_30d / 30)
× seasonality_factor
× promotion_factor

inventory_position
= available + inbound + in_transit - unusable - reserved_deduction

reserved_deduction
= 0 when available_is_net_of_reserved=true
= reserved otherwise

total_lead_time
= supplier + production + shipping + Amazon receiving

safety_stock_units
= weighted_daily_sales × safety_stock_days

reorder_point
= weighted_daily_sales × (total_lead_time + safety_stock_days)

recommended_reorder_quantity
= round_case_pack(max(MOQ, target_coverage_demand - inventory_position))
```

## Amazon inventory semantic safeguard

The existing Amazon adapter maps `fulfillableQuantity` to `available`. That quantity is treated as already net of reservations. Callers using this value must send `available_is_net_of_reserved=true`; otherwise reserved inventory is deducted according to the generic gross-on-hand formula. The selected interpretation is recorded in `input_manifest`.

## API

- `POST /api/inventory/policies`
- `GET /api/inventory/policies`
- `POST /api/inventory/health/calculate`

First policy creation requires Owner/Admin write access. A later policy version returns HTTP 202 with a HITL request. The update is committed only after a different user approves the exact action hash and the caller repeats the request with `approval_request_id`.

## Degraded behavior

The calculator still returns explainable read-only metrics when possible, but sets `automation_eligible=false` for:

- Missing 7/14/30-day windows.
- Zero sales velocity.
- Inventory older than six hours.
- Promotion anomaly, sales spike, stockout-distorted sales, recent price change or recent advertising-budget change.
- Mock/system-estimated inventory or confidence below the configured floor.

No replenishment order or Amazon inventory write is executed by this milestone.

## Data not yet automated

Sales windows, in-transit inventory, lead times, seasonality/promotion factors and per-unit impact values are API inputs or policy configuration. They are not yet automatically reconciled from Amazon reports, suppliers, freight providers or profit snapshots.

## Rollback

- Application rollback is safe because routes and tables are additive.
- Before production reliance, use `alembic downgrade 0002`.
- After policies are in use, create a corrected later-effective policy rather than deleting history.
- Health snapshots are derived records and may be recalculated using the recorded policy and input manifest.
