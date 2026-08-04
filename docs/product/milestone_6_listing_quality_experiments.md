# Milestone 6 — Listing Quality and Controlled Experiments

## Scope delivered

- Deterministic `ListingQualitySnapshot` with title, bullets, description, images, search terms and active-status section scores.
- Structured quality issues, evidence and modification recommendations; all outputs are recommendation-only.
- Tenant-scoped `ListingOptimizationExperiment` with before baseline, exact proposed changes, observation window and action hash.
- HITL approval bound to the exact Listing action before an experiment can enter approved observation status.
- Deterministic after-effect evaluation for quality score, conversion rate and return rate.
- Rollback recommendation and operational issue generation when business guardrails regress.
- Alembic migration `0007` and PostgreSQL RLS registration for snapshots and experiments.
- Explicit guardrail Eval dataset at `evals/listing_experiment_guardrails.json`.

## Quality score

The product default score totals 100 points:

- Title: 20.
- Bullet points: 25.
- Description: 15.
- Images: 15.
- Search terms: 10.
- Active Listing status: 15.

Status bands are `critical < 40`, `poor < 60`, `fair < 80`, and `good >= 80`. These are OmniSell product defaults, not Amazon standards, ranking guarantees or policy compliance certification.

## Experiment lifecycle

```text
quality snapshot -> proposed changes -> awaiting approval -> approved/observing
-> observation window ends -> completed | inconclusive | rollback_recommended
```

Approval only authorizes the exact action hash. This milestone intentionally does not call Amazon `update_listing`; approval activates an internal observation record only.

## Outcome guardrails

- Relative conversion-rate decline of 10% or more recommends rollback.
- Return-rate increase of 2 percentage points or more recommends rollback.
- Missing before/after conversion or return metrics produces `inconclusive`.
- Evaluation before the observation window ends is blocked.
- Rollback is a recommendation. Restoring previous content remains a separate approval-bound platform action.

The comparison is observational. It does not normalize traffic source, advertising, price, inventory availability, seasonality or competitor changes, and therefore is not causal proof.

## API

- `POST /api/listing-quality/snapshots/calculate`
- `GET /api/listing-quality/snapshots`
- `POST /api/listing-quality/experiments`
- `GET /api/listing-quality/experiments`
- `POST /api/listing-quality/experiments/{experiment_id}/evaluate`

All reads and writes derive `tenant_id` from the authenticated principal. Snapshot IDs and experiment IDs from another tenant return no data.

## Source and confidence limits

Rich Listing content currently enters through the request/import context. Amazon read synchronization does not yet persist every content field required by this evaluator. Unknown or missing source observation time lowers confidence and keeps automation disabled.

## Rollback

- Run `alembic downgrade 0006` to remove Listing quality snapshots and experiments.
- Revert the Listing quality route, service and repository wiring if application rollback is required.
- No Amazon Listing write is executed, so this milestone creates no external platform compensation requirement.