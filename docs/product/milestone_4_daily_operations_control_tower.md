# Milestone 4 — Daily Operations Control Tower

## Scope delivered

- Persisted, tenant-scoped `DailyOperationsBrief` records.
- Deterministic issue severity and quantified-impact prioritization.
- Currency-separated impact aggregation; no implicit FX conversion.
- SKU health summaries combining profit, inventory, Listing and data-quality issues.
- Basic Listing completeness/status evaluation with explicit freshness degradation.
- Daily brief, dashboard and SKU-health APIs.
- Next.js经营总览、今日优先事项和 SKU 健康度 pages.

## Brief composition

The brief includes:

- Open issue counts by severity.
- Issue counts by business domain.
- Data-quality issue count.
- Revenue, profit and cost impact grouped by ISO currency.
- Issues whose impact currency cannot be allocated safely.
- Ranked priority issue IDs and active recommendation IDs.
- Per-SKU health status, issue types, data-quality flag and last detection time.
- Source manifest declaring that persisted issue/action records were used.

Priority order is deterministic:

```text
severity descending -> quantified impact descending -> latest detection first
```

No LLM is used to calculate severity, impact, currency totals or SKU health.

## Listing basic evaluation

Existing persisted Listing records are checked for:

- Suppressed, inactive, error, invalid or deleted status.
- Missing title.
- Missing price or price currency.
- Missing external platform product identifier.
- Missing source observation timestamp.

Because the current Listing table does not have a first-class synchronization timestamp, absent `platform_extensions.observed_at` results in `listing_freshness_unknown`, confidence `0.40` and recommendation-only handling. The system does not present the assessment time as verified Amazon freshness.

## API

- `POST /api/operations/briefs/generate`
- `GET /api/operations/briefs/latest`
- `GET /api/operations/dashboard`
- `GET /api/operations/sku-health`

Generation supports an IANA timezone and optional Listing scan. Cross-tenant dashboard and brief reads return not found.

## Frontend

- `/dashboard`:经营总览、Critical/High count、数据质量、币种隔离影响、今日优先事项和 top SKU.
- `/sku-health`: tenant-scoped SKU risk table.
- Next.js server routes proxy backend requests and inject credentials server-side.
- The root route now redirects to `/dashboard`.

## Execution boundary

The dashboard is a decision surface, not an execution engine. Recommended actions remain `recommendation_only`; generating or viewing a brief cannot change Amazon, price, inventory, advertising or Listing data.

## Rollback

- Run `alembic downgrade 0004` before production reliance.
- The rollback drops persisted daily briefs only; operational issues/actions remain available.
- Remove the briefing router/service and dashboard navigation to revert application behavior.
- No external side effects require compensation.
