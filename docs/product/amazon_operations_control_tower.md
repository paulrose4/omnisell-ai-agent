# Amazon Operations Control Tower

## Product position

OmniSell is an Amazon cross-border ecommerce AI operations control tower. Its primary artifact is a verified operational decision record, not a chat transcript.

## Users and decisions

- Owners: profit, cash exposure and priorities.
- Operations leaders: issue severity, accountable owner and execution status.
- Marketplace operators: Listing, price and fulfillment actions.
- Supply-chain teams: stockout, excess and replenishment decisions.
- Advertising teams: spend efficiency and search-term actions.
- Customer-service leaders: issue clusters, escalation and product feedback.
- Finance analysts: cost versions, profit formulas and reconciliation.
- Service providers: strict tenant/store separation and portfolio views.

## Six-layer modular-monolith architecture

1. **Collection** — Amazon APIs, Advertising API, CSV, manual configuration and system estimates. Every fact carries source, effective time, collection time, freshness, confidence, estimate flag, tenant and marketplace.
2. **Deterministic metrics** — Decimal-based profit, inventory, Listing, advertising and return metrics. No LLM calculations.
3. **Rules and anomaly detection** — versioned thresholds, deduplication, severity and business-impact calculations.
4. **AI analysis** — evidence-bound explanations, root-cause hypotheses, summaries and draft content. No direct high-risk execution.
5. **Action workflow** — recommendation, policy, approval, idempotent execution, before/after snapshots, rollback and observation.
6. **Experience and operations** — daily cockpit, work queues, imports, data quality, audit, telemetry and runbooks.

## Trusted data contract

Every calculated output must expose:

- `tenant_id`, `marketplace_id`, optional store/SKU scope.
- calculation window and `calculated_at` in UTC.
- source classification: `amazon_api`, `advertising_api`, `csv_import`, `manual`, `system_estimate`, `mock`.
- source timestamps and freshness state.
- formula/calculation version.
- exact inputs or input references.
- `missing_data`, confidence and degraded reason.
- actual versus estimated flags.

## Primary product objects

- Metric snapshot: deterministic fact for a defined window.
- Operational issue: deduplicated problem with impact and evidence.
- Recommended action: structured candidate with risk and expected outcome.
- Operational task: accountable work item independent of platform execution.
- Action execution: immutable attempt with policy/approval/audit context.
- Action outcome: observed result versus baseline and guardrails.
- Daily operations brief: ranked composition of facts, issues and work.

## Execution boundary

LLMs may explain and propose. Application services validate. Policy decides. Workflow coordinates. Adapters execute. Repositories persist. Observability correlates.

No model may directly choose arbitrary tools, calculate price floors, bypass tenant filters, approve itself or write Amazon data without a policy-bound structured action.

## Initial business slice

Milestone 1 delivers cost profile ingestion and deterministic profit snapshots. Orders and manually supplied/derived financial inputs can then support loss detection. Inventory, issues and daily brief build on that trusted base.

## Deployment progression

1. CSV/manual cost import with dry-run.
2. Read-only profit calculation and data-quality UI/API.
3. Read-only Amazon pilot and reconciliation.
4. Internal task creation automation.
5. Recommendation-only price/Listing/inventory workflows.
6. Tenant-by-tenant approval-gated writes.
7. Outcome measurement and controlled rollback.
