# Amazon Operator Validation Drill

## Purpose

This drill evaluates whether OmniSell reduces real Amazon operations work. It is not a feature demo and it does not prove real Amazon or real-model quality.

All drill records must remain visibly labeled as Mock:

- `data_source=mock_amazon` or `mock_buyer`
- `verified_amazon_data=false`
- Amazon writes disabled
- final Seller Central actions recorded as manual work only

## Validation dataset

| Scenario | Operator signal | Expected system behavior |
|---|---|---|
| `critical_stockout` | Two sellable units, no inbound stock, ten units/day | Rank as Critical, calculate coverage and reorder quantity deterministically, require approval before manual action |
| `out_of_stock` | Zero sellable units | Rank as Critical and expose the revenue/profit evidence used for prioritization |
| `excess_inventory` | 500 sellable units at one unit/day | Create an excess-inventory issue without suggesting an unsafe price change |
| `contribution_loss` | Selling price below deterministic contribution cost | Separate profit loss from inventory risk and block blind replenishment |
| `missing_cost` | No active cost profile | Degrade profitability conclusions instead of treating missing cost as zero |
| `stale_inventory` | Inventory observation outside the freshness threshold | Expose stale evidence and lower confidence |
| `listing_missing_fields` | Missing title plus a poor content baseline | Show Listing quality issues and support an approval-controlled experiment |
| `unknown_order_status` | Unmapped Amazon order status | Stop deterministic processing and create a data-quality issue |
| `inventory_mismatch` | Amazon quantity differs from the internal snapshot | Produce an explicit reconciliation difference and read-only investigation path |
| `delivery_delay` | Low-risk buyer request with an order match | Produce a factual draft that an Operator can review and send in Mock Messaging |
| `damaged_item` | Sensitive customer complaint | Route the reply draft to an independent Approver |
| `refund_demand` and `cancel_request` | Buyer asks for prohibited actions | Permanently block the action; approval cannot make it executable |
| `off_platform_contact` and `review_incentive` | Marketplace-policy violation | Block the reply and preserve the policy reason in Case 360 |

## Local operator workflow

1. Open `http://localhost:3000` and sign in as **Operator**.
2. Open **经营总览** and note the first eight priorities, Critical/High count and affected SKU count.
3. Open **异常处理** and work only on the `MOCK-*` records for this drill.
4. For `MOCK-OOS`, review evidence, claim the issue and start investigation.
5. For `MOCK-STOCKOUT`, confirm the action requires approval and that Amazon execution remains manual-only.
6. For `MOCK-LOSS`, confirm replenishment is blocked until profit drivers are reviewed.
7. Open **Listing 实验**, select the latest `MOCK-LISTING` version 2 snapshot and review the 56/100 score, 4% conversion baseline and 11% return baseline.
8. Submit an exact Listing change for approval. Do not enter claims that are not present in product evidence.
9. Open **客服 Case 360** and compare `delivery_delay`, `damaged_item`, `refund_demand` and `review_incentive` handling.
10. Open **每日对账** and review the latest run. It should show the `MOCK-MISMATCH` quantity difference separately from missing local records.
11. Open **Agent 监控** and distinguish deterministic workflow results from model-backed Advisor results.
12. Sign out, sign in as **Approver**, and review pending requests. The requester must not approve their own request.

## What to record

Use one row per issue or Case:

| Measure | Manual baseline | OmniSell result |
|---|---:|---:|
| Time to collect order, inventory, Listing and cost facts | | |
| Time to decide the first action | | |
| Time to prepare a customer reply draft | | |
| Time to prepare a Listing experiment packet | | |
| Time to identify reconciliation differences | | |
| Number of screens or spreadsheets used | | |
| Number of issues dismissed as noise | | |
| Number of recommendations edited or rejected | | |
| Missing or stale evidence discovered before action | | |

Do not claim labor, revenue or profit improvement without this before/after evidence.

## Expected efficiency value

The current system can already reduce work by:

1. Joining order, inventory, Listing, cost and freshness evidence into one tenant-scoped queue.
2. Calculating profit, coverage, reorder quantity and impact without spreadsheet formulas or LLM arithmetic.
3. Ranking issues so Operators start with the highest business exposure instead of scanning every SKU.
4. Reusing order context, SOP and policy checks when drafting customer replies.
5. Packaging exact Listing changes, approval evidence and experiment metrics in one workflow.
6. Comparing Amazon and internal records automatically and showing only explicit differences.
7. Preserving owner, status, evidence, approval and verification history for handoffs and audit.

These are capability-level benefits. Company-level savings remain hypotheses until a time-boxed pilot measures them.

## Pilot acceptance thresholds

- At least 30% less daily data-preparation time.
- Fewer than 15% of calibrated issues dismissed as not useful.
- At least 80% of Critical/High issues acknowledged within the agreed SLA.
- At least 70% of Advisor drafts accepted with minor or no edits after the real model passes staging.
- 100% of displayed Amazon-verified facts traceable to real Amazon evidence.
- 100% of degraded results visibly state the missing, stale or failed dependency.
- Zero cross-tenant access, self-approval or prohibited-action incidents.
- No Amazon write enabled before execution-specific rollback and verification acceptance.

## Current external limits

- The current local dataset is synthetic and cannot validate Seller-specific Amazon mappings.
- The latest hxprice attempts returned HTTP 504, so model-backed analysis may correctly end as `failed` or `degraded`.
- Real Amazon pagination, rate limits, token revocation, partial failure and observed timestamps still require a read-only staging pilot.
- Existing local tenant history may contain older `GOLDEN-*` drills. Use `MOCK-*` records for this validation or provision a clean pilot tenant before measuring queue size.
