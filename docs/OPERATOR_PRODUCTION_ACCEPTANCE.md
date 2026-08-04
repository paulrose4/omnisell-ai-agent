# Operator Production Acceptance Report — 2026-07-17

## Purpose

This report evaluates whether the current OmniSell system provides real operational value, not merely whether its code passes tests. The acceptance flow covers data ingestion, deterministic calculation, daily triage, bounded-advisor analysis, approval, manual execution and post-action verification.

## Current decision

**Conditional Go for a controlled internal read-only operations pilot.**

The deterministic operations engine already removes meaningful manual aggregation work and enforces safer decisions. The bounded-advisor architecture is evidence constrained and production shaped. However, the current machine does not have a working real model endpoint, and no real Amazon seller evidence has been validated in this acceptance run. Broad production claims therefore remain blocked.

## Evidence executed

- 101 focused operational, advisor, safety and business tests passed before business-value corrections.
- Golden Mock processed 300 orders, 10 inventory positions and 10 Listings into profit, inventory, issue and brief records.
- The full issue lifecycle exercised acknowledgement, investigation, approval, manual execution, re-sync and deterministic verification.
- Customer Case, Listing Advisor, reconciliation and operations Advisor safety/evidence tests passed.
- The configured local model endpoint 'http://localhost:11434/v1' returned HTTP 502 on 2026-07-17; no real model output was accepted as evidence.

## Measured workflow result

After correcting alert-noise and replenishment conflicts, the representative 10-SKU drill produced:

- 13 active issues across 9 genuinely affected SKU/account scopes;
- 7 Critical, 4 High and 2 Medium issues;
- 2 real data-quality issues instead of one synthetic warning for nearly every Mock SKU;
- zero active issues for the configured healthy SKU;
- a contribution-loss SKU ranked separately for profit loss and stockout exposure;
- replenishment explicitly blocked for the loss-making SKU until profit drivers are reviewed;
- successful post-execution verification only after newly observed inventory evidence arrived.

Before the corrections, the same drill produced 24 active issues, including repeated per-SKU Mock-source data-quality noise and a false issue on the healthy SKU. The corrected flow reduces the active queue by roughly 46% while preserving the intended business risks.

## Acceptance matrix

| Production need | Result | Evidence |
|---|---|---|
| Consolidate Amazon operational facts | Pass in Mock | Orders, FBA inventory and Listings normalize into tenant-scoped records |
| Calculate profit/inventory deterministically | Pass | Decimal/currency/freshness/missing-data contracts and focused tests |
| Show operators what to work first | Pass with calibration | Ranked brief and issue queue work; tenant/category thresholds still need operator sign-off |
| Avoid healthy-SKU alert noise | Pass after correction | Healthy scenario now produces no active issue |
| Prevent replenishing a loss-making SKU blindly | Pass after correction | Profit-at-risk floors at zero and replenishment is explicitly blocked pending profit review |
| Explain evidence and uncertainty | Pass structurally | Evidence IDs, source, freshness, confidence, missing data and verified-Amazon flags are mandatory |
| Resist prompt injection and prohibited requests | Pass in tests | Refund, cancellation, approval bypass and invented evidence cases are rejected/degraded |
| Separate operator and approver duties | Pass | Self-approval and protected state transitions are blocked |
| Track execution and verify result | Partial | Manual execution and inventory re-sync verification work; generic outcome attribution is incomplete |
| Produce useful Listing workflow | Pass for read-only/manual pilot | Workbench covers snapshot review, exact-change approval, manual execution evidence, before/after metrics and keep/rollback decision; Amazon writes remain disabled |
| Use a real selected model reliably | Fail in current environment | Local configured endpoint returned HTTP 502; provider structured-output smoke remains required |
| Use real Amazon evidence | Not yet verified | Mock truthfulness is enforced, but sandbox/real seller validation is external |
| Prove actual labor-hour savings | Not yet measured | Requires a time-boxed operator pilot with baseline measurements |

## Real efficiency value already present

The system can already reduce repeated operator work in four areas:

1. It combines cost, order, inventory and Listing facts into one SKU-level queue instead of manual spreadsheet joins.
2. It calculates contribution profit, coverage, reorder quantities and business impact deterministically.
3. It suppresses unsafe automation and sends only high-risk actions into approval rather than requiring blanket review of every record.
4. It preserves evidence, ownership, status and verification history, reducing handoff and audit reconstruction time.

## Remaining workflow friction

- The Listing workbench now exists, but category-specific quality weights and operator acceptance still need pilot calibration.
- Recommended actions do not yet create a universal accountable task with assignee, SLA and measured outcome across every domain.
- Advertising and returns/VOC evidence are absent, so some root-cause analysis remains incomplete.
- The Advisor output quality has only been proven with fake/stub gateways and schema/evidence validators, not the intended real provider.
- The dashboard shows priorities but does not yet measure time-to-acknowledge, time-to-resolution or realized profit/cash impact as business KPIs.

## Required company pilot

Run a 2–4 week read-only pilot with 1 owner, 2–5 operators, 1 approver and a limited seller/marketplace scope. Keep Amazon writes disabled.

Measure against the existing manual process:

- daily data preparation minutes;
- number of issues reviewed versus acted on;
- false-positive/dismissal rate;
- Critical/High acknowledgement time;
- time from issue to approved plan;
- percentage of recommendations with sufficient evidence;
- operator edit/rejection rate for Agent drafts;
- repeated issue rate after claimed resolution;
- estimated profit/cash exposure identified;
- realized outcome only where deterministic before/after evidence exists.

### Pilot acceptance thresholds

- at least 30% reduction in daily data preparation time;
- no cross-tenant or prohibited-action incident;
- fewer than 15% dismissed-as-not-useful issues after threshold calibration;
- at least 80% of Critical/High issues acknowledged within the agreed SLA;
- at least 70% of Advisor drafts accepted with minor or no edits;
- 100% of displayed Amazon-verified facts traceable to real Amazon evidence;
- 100% of degraded recommendations visibly state why they are degraded;
- no Amazon write enabled until execution-specific rollback and verification acceptance is signed.

## Final conclusion

The current system is likely to provide real operational help as a **read-only control tower and decision-support workflow**. It is not yet proven as a full production autonomous Agent system. The next evidence needed is not more Mock unit coverage; it is a real-model structured-output smoke, a read-only Amazon pilot and measured operator time/quality outcomes.
