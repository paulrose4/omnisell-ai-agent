# Operator Decisions Required

These values are intentionally unresolved or defaulted. They must be approved per tenant before production business decisions use them.

| Decision | Current explicit default | Needed from operator |
|---|---|---|
| Cost profile stale threshold | 90 days | Finance review cadence |
| Daily order/refund freshness | 24 hours | Reporting cutoff and timezone |
| Fee/advertising freshness | 48 hours | Settlement and ads latency tolerance |
| Inventory freshness | 6 hours | Operational SLA |
| Required cost fields | Product, domestic shipping, international freight, duty, prep, packaging | Which fields may be legitimately zero by SKU |
| Return-loss rate | No universal default | Category/SKU historical rate or approved estimate |
| Referral fee rate | No universal default | Marketplace/category-specific rate source |
| Fulfillment/storage fee | No universal default | Actual fee source or approved estimate |
| Target/minimum margin | No universal default | Tenant policy and approval authority |
| Import approval | Single approval during initial rollout | Whether owner approval is required for first profile |
| CSV maximum size/rows | To be configured in Milestone 1 | Expected operational batch size |
| Profit attribution window | Requested window | Finance reporting standard |
| Refund attribution | Order-window convention pending | Refund-date versus order-date treatment |
| FX conversion | Blocked without explicit rate | Approved provider, timestamp and buffer |
| Advertising attribution | Not implemented in Milestone 1 | Amazon attribution window and reconciliation policy |

## Milestone 1 implemented configuration defaults

- CSV limit: 2 MiB and 5,000 data rows per job.
- Allowed source values: `amazon_api`, `advertising_api`, `csv_import`, `manual`, `system_estimate`, `mock`.
- Dry-run and commit use separate idempotency records so validation does not consume the commit key.
- Complete estimated profiles currently receive source-based confidence (`csv_import=0.85`, `manual=0.75`, `system_estimate=0.55`, `mock=0.20`). These are product defaults pending finance/operator calibration.

## Milestone 2 inventory decisions

| Decision | Current explicit default | Needed from operator |
|---|---|---|
| Sales weights | Per-policy; examples use 50%/30%/20% for 7/14/30 days | Category/SKU demand behavior |
| Inventory stale threshold | 6 hours | Operational inventory SLA |
| Critical stockout boundary | Days of supply <= total lead time | Tenant escalation policy |
| Low-stock boundary | Days of supply <= lead time + safety-stock days | Tenant escalation policy |
| Excess boundary | > 1.5 × target coverage | Cash/storage tolerance |
| Preferred shipping mode | Policy field; no automatic override | Supplier lane and urgency rules |
| Seasonality/promotion multiplier | 1.0 unless manually supplied | Approved campaign/season plan |
| Missing-window automation | Disabled | Whether any tenant allows restricted exceptions |
| Revenue/profit risk | Null without explicit per-unit values | Approved source and window |

## Milestone 3 operator decisions

- Confirm whether a stockout coverage threshold of seven days should be globally critical or tenant/category configurable.
- Confirm whether contribution margin at or below -10% should be critical and whether absolute loss thresholds should also apply.
- Define an ISO 4217 impact currency contract for inventory-health snapshots; current inventory impact estimates cannot be aggregated safely across marketplaces.
- Confirm whether dismissed issues should reopen automatically on recurrence; current implementation reopens them to avoid silent risk.
- Confirm default owners and response-time targets by issue severity before the daily operations brief is introduced.

## Milestone 4 operator decisions

- Confirm the default tenant timezone used by scheduled daily briefs.
- Confirm priority queue limits and whether absolute financial-impact thresholds should override severity.
- Define Listing freshness storage as a first-class synchronized timestamp rather than `platform_extensions.observed_at`.
- Confirm ownership and SLA by Critical/High/Medium/Low issue severity.
- Define approved FX sources before any cross-currency executive rollup is introduced.
- Decide when daily brief generation moves from explicit API invocation to the scheduler and Outbox event flow.

## Milestone 5 operator decisions

- Confirm the 24-hour profit-snapshot freshness threshold for each marketplace and reporting cadence.
- Confirm whether advertising cost should remain constant per unit in price projections or use a separate elasticity model later.
- Confirm the minimum confidence and maximum price-change band before any automatic Amazon write feature is enabled.
- Define approved competitor-price sources; the existing deterministic mock scraper is not production market evidence.
- Decide whether minimum-margin exceptions require one approver or two for selected brands/categories.
- Define post-price-change observation windows, stop-loss thresholds and rollback ownership before execution is introduced.

## Milestone 6 operator decisions

- Confirm Listing section weights and quality bands by category and marketplace; current values are product defaults.
- Confirm the minimum Listing source freshness required for an experiment proposal.
- Confirm the default observation window and minimum sessions/orders required before evaluation.
- Confirm conversion rollback at a 10% relative decline and return-rate rollback at a 2 percentage-point increase.
- Define traffic, advertising, price, inventory and seasonality controls required before outcomes can be treated as causal.
- Define who owns rollback review and the SLA for a `listing_experiment_regression` critical issue.
- Decide when approved experiments may call Amazon Listing writes; Milestone 6 intentionally performs no platform write.