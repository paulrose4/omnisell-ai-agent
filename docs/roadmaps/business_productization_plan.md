# Business Productization Roadmap

## Principles

Deliver vertical, reviewable business slices. Each slice includes domain model, migration, repository, application service, API, security, audit, tests, documentation and rollback. Real Amazon evidence is separate from mock evidence.

## Milestone 0 — Audit and design

Outputs: capability audit, product architecture, metric dictionary, business rules, automation matrix, operator decisions and this roadmap. No runtime behavior changes. Exit when documents use consistent formulas, names, risk levels and milestone order.

## Milestone 1 — SKU cost and profit foundation

- Versioned tenant/marketplace/SKU cost profiles.
- Secure CSV dry-run and commit with checksum/idempotency/error report.
- Deterministic profit calculator, break-even price and Break-even ACoS.
- Profit snapshots with input manifest, completeness, confidence and freshness.
- Alembic/RLS/repository/API/audit/tests.
- No pricing execution and no invented fee data.

## Milestone 2 — Inventory health and replenishment

Inventory policy, weighted sales, supply days, stockout/excess classification, MOQ/case-pack recommendation, stale-data downgrade and tests.

## Milestone 3 — Operational issue model

Unified issues, business impact, evidence, deduplication, lifecycle and structured recommendations.

## Milestone 4 — Daily operations cockpit

Business summary, priorities, SKU health, approvals, yesterday outcomes and data quality. Empty states remain explicit.

## Milestone 5 — Profit-constrained pricing

Replace percentage-only policy with profit floor, freshness, inventory, Buy Box and campaign context. Recommendation first; writes disabled by default.

## Milestones 6–9

Listing quality/experiments, customer voice/returns, advertising diagnosis, then operational action/outcome tracking.

## Milestones 10–11

Enterprise frontend completion and production validation: evals, tenant tests, fault injection, load, migration checks, CI gates, runbooks and Go/No-Go review.

## Milestone 12 — Operations learning loop

- Shared `OperationsIntelligenceSource` contract for Mock-first and future Amazon adapters.
- Profit/inventory-constrained Sponsored Products recommendations; all manual-only.
- Normalized VOC taxonomy and accountable team routing.
- Explicit operator touch-time, issue feedback and missed-issue evidence.
- Action execution attempts, observation windows and bounded attribution levels.
- Three primary operator queues: today, waiting and verifying.
- Alembic `0021`, tenant RLS, API/BFF coverage, deterministic Mock drill and production gates.

## Milestone execution gate

For every milestone: inspect status; define scope/non-scope; add failing tests/evals; implement minimally; migrate; test focused/full; compile; validate frontend if changed; inspect tenant/PII boundaries; document commands, rollback, external blockers and remaining risk.
