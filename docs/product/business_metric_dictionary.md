# Business Metric Dictionary

All money uses Decimal and ISO 4217. All windows are `[start_at, end_at)` in marketplace time but persisted as UTC. Missing required inputs never silently become zero.

| Metric | Formula | Required inputs | Window | Freshness | Degraded behavior |
|---|---|---|---|---|---|
| Gross revenue | item sales - discounts - coupons - refunds | item sales, discounts, coupons, refunds | SKU/day or requested window | order/financial sources <= 24h for daily use | mark missing values; no trusted margin |
| Landed unit cost | product + domestic shipping + international freight + duty + prep + packaging | active cost profile fields | effective cost version | profile effective at window end | incomplete; no automatic pricing |
| Landed cost | landed unit cost × units | complete landed unit cost, units | requested window | follows inputs | incomplete |
| Variable cost | landed cost + referral + fulfillment + storage + advertising + return loss + other variable | all applicable costs | requested window | fees/ads <= 48h unless configured | incomplete; estimated components flagged |
| Contribution profit | gross revenue - variable cost | gross revenue, variable cost | requested window | follows inputs | unavailable if required values missing |
| Contribution margin | contribution profit / gross revenue | contribution profit, positive gross revenue | requested window | follows inputs | null for zero/negative gross revenue |
| Break-even price | fixed per-unit costs / (1 - referral fee rate - return loss rate - exchange buffer rate) | landed unit cost, fulfillment fee, storage, rates | cost profile effective period | current profile | null if denominator <= 0 or missing |
| Advertising pre-spend profit | gross revenue - non-ad variable cost | revenue and all non-ad costs | requested window | follows inputs | incomplete |
| Break-even ACoS | advertising pre-spend profit / advertising-attributed sales | ad-attributed sales, non-ad costs | ad attribution window | ads <= 48h | null if attributed sales <= 0 |
| Data completeness | present required input weight / total required input weight | metric input manifest | calculation time | immediate | exposes missing inputs |
| Confidence | source confidence × completeness × freshness factor | source, completeness, age | calculation time | immediate | never upgraded by LLM |
| Days of supply | available / weighted daily sales | sellable stock, weighted sales | inventory snapshot + 7/14/30 sales | inventory <= 6h | null when sales unavailable |
| Expected stockout date | calculated_at + days of supply | days of supply | point-in-time | follows inputs | null when forecast unavailable |

## Milestone 1 formula conventions

- Refunds, discounts, coupons and promotions are positive deduction amounts.
- Referral fees may be actual totals. Estimated referral fee uses a versioned rate only when actual data is absent and is marked estimated.
- Break-even price excludes advertising cost because it is used to derive advertising capacity; it includes estimated return-loss and exchange-rate buffers.
- Break-even ACoS is not computed without positive advertising-attributed sales.
- Cross-currency calculation is blocked until an explicit exchange rate, source and effective timestamp are supplied.

## Default freshness configuration

The following are product defaults, not universal industry standards, and require operator confirmation:

- Cost profile: stale after 90 days.
- Order and refund data: stale after 24 hours for daily briefing.
- Amazon fee data: stale after 48 hours.
- Advertising data: stale after 48 hours.
- Inventory data: stale after 6 hours.

## Inventory calculation conventions

- Weighted sales defaults are tenant policy values and must sum to 1; no global “industry standard” weight is claimed.
- Missing windows are normalized across available weights, but the result is degraded and not automation-safe.
- `available_is_net_of_reserved` explicitly controls whether reserved inventory is deducted.
- Total lead time includes supplier, production, shipping and Amazon receiving days. Safety-stock days are added in the reorder point, not hidden inside lead time.
- MOQ and case-pack rounding only apply when raw replenishment demand is positive.
- Revenue/profit risk is null unless explicit per-unit revenue/contribution values are supplied.

## Operational issue conventions

| Metric | Formula | Required inputs | Freshness | Degraded behavior |
|---|---|---|---|---|
| Issue dedupe key | SHA-256 of tenant, issue type, marketplace and seller SKU | Stable tenant/resource identifiers | Immediate | Detection is rejected when source tenant mismatches |
| Occurrence count | Prior count + 1 for each repeated detection | Canonical dedupe match | Detection time | Reopened terminal issue retains history count |
| Business impact score | Absolute revenue impact + absolute profit impact + absolute cost impact | Source snapshot impact estimates | Follows source snapshots | Null components contribute nothing and remain explicitly null |
| Issue confidence | Deterministic source snapshot confidence | Snapshot completeness/source/freshness | Follows source snapshots | Never upgraded by LLM |
| Issue freshness | Oldest relevant source collection/calculation timestamp | Source evidence timestamp | Domain-specific thresholds | Stale inputs create a separate data-quality issue |
| Priority order | Severity descending, then quantified impact descending | Severity and available impact | Detection time | Missing impact is ranked below quantified impact at equal severity |

## Daily operations brief metrics

| Metric | Formula | Required inputs | Freshness | Degraded behavior |
|---|---|---|---|---|
| Open issue count | Count of non-resolved/non-dismissed operational issues | Persisted tenant issues | Brief generation time | No brief is generated from missing issue storage |
| Critical/High count | Critical count + High count | Issue severity | Brief generation time | Severity is never inferred by the UI |
| Currency impact | Sum absolute revenue/profit/cost impact within one ISO currency | Issue impact and currency | Follows source issue evidence | Unknown-currency issues remain unallocated |
| SKU health | Highest active severity for marketplace + seller SKU | Active SKU issues | Latest issue detection | Data-quality flag remains visible independently |
| Daily priority | Severity rank, impact score, latest detection | Active issues | Brief generation time | Missing impact ranks below quantified impact at equal severity |

## Profit-constrained pricing metrics

| Metric | Formula | Required inputs | Freshness | Degraded behavior |
|---|---|---|---|---|
| Pricing fixed unit cost | Landed + fulfillment + storage + advertising + other variable cost, divided by units | Positive units and complete profit snapshot | Profit snapshot <= 24h | Recommendation requires approval |
| Pricing variable rate | Referral/revenue + return-loss/revenue + exchange buffer | Positive revenue, fees, cost profile | Profit/cost freshness | Invalid rate >= 1 blocks calculation |
| Pricing break-even floor | Fixed unit cost / (1 - variable rate), max with snapshot break-even | Profit snapshot and cost profile | Follows evidence | Price below floor is permanently blocked |
| Minimum-margin floor | Fixed unit cost / (1 - variable rate - minimum margin) | Tenant minimum margin | Effective cost profile | Infeasible denominator blocks calculation |
| Projected unit profit | Price * (1 - variable rate) - fixed unit cost | Proposed price and pricing economics | Calculation time | Advisory only; no guarantee |
| Projected margin | Projected unit profit / proposed price | Positive proposed price | Calculation time | Below configured minimum requires approval |

## Listing quality and experiment metrics

| Metric | Formula | Required inputs | Freshness | Degraded behavior |
|---|---|---|---|---|
| Listing quality score | Sum of deterministic section scores, maximum 100 | Title, bullets, description, images, search terms, status | Source observation timestamp | Missing/weak sections lower score; unknown freshness lowers confidence |
| Listing quality status | `<40 critical`, `<60 poor`, `<80 fair`, otherwise good | Quality score | Calculation time | Product default only, not Amazon certification |
| Quality score delta | After score - before score | Scope-matched snapshots | After observation window | Advisory outcome only |
| Conversion relative delta | `(after - before) / before` | Positive before conversion and after conversion | Same experiment observation windows | Missing values produce inconclusive result |
| Return-rate delta | After return rate - before return rate | Before and after return rate | Same experiment observation windows | Missing values produce inconclusive result |
| Experiment result | Rollback if conversion delta <= -10% or return delta >= 0.02 | Approved experiment and complete metrics | Observation end reached | Incomplete metrics produce `inconclusive` |