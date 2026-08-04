# Milestone 1 — SKU Cost and Profit Foundation

## Scope delivered

- Tenant/marketplace/SKU-scoped, effective-dated cost profiles.
- UTF-8 CSV dry-run and commit with extension/MIME, size, row, field, formula-injection, duplicate and idempotency validation.
- Deterministic Decimal profit calculation.
- Contribution profit/margin, break-even price and Break-even ACoS.
- Missing-input degradation, source-based confidence, estimate flag, input manifest and calculation version.
- Persistent profit snapshots, Alembic migration, PostgreSQL RLS registration, API and audit events.

## Non-scope

No Amazon settlement reconciliation, Advertising API, FX provider, inventory forecast, anomaly creation, price execution or frontend profit center is included. Values sent to the calculation API are caller-supplied facts and remain distinguishable from Amazon-verified data.

## CSV workflow

1. Download `docs/templates/sku_cost_profile.csv`.
2. Fill tenant-owned SKU values. Do not include formulas.
3. Dry-run:

```powershell
curl.exe -X POST "http://localhost:8080/api/profitability/cost-profiles/import?dry_run=true" `
  -H "Authorization: Bearer <token>" `
  -H "Idempotency-Key: costs-2026-07-v1" `
  -F "file=@costs.csv;type=text/csv"
```

4. Review `errors`, checksum and valid row count.
5. Commit the exact same bytes with `dry_run=false`. A changed file must use a new idempotency key.

A committed profile is immutable. Corrections create a later effective version; the repository closes the previous active version instead of mutating history.

## Profit calculation API

`POST /api/profitability/snapshots/calculate` requires a matching effective cost profile and a timezone-aware window. It rejects currency mismatch and returns `missing_inputs`, `confidence`, `estimated`, `degraded_reason`, `input_manifest` and `calculation_version`.

The API never treats absent required cost fields as zero. Incomplete profiles produce a degraded snapshot without contribution profit or break-even price.

## Rollback

- Application rollback: deploy the prior image; the additive tables do not affect legacy routes.
- Schema rollback before business data is relied upon: `alembic downgrade 0001`.
- Business correction after use: do not downgrade/delete history; import a corrected later-effective profile and recalculate snapshots.
