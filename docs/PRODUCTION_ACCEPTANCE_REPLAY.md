# Production Acceptance Replay

## Purpose

This evaluator answers a narrow question: do the current deterministic OmniSell rules find known Amazon operations scenarios without unsafe provenance or policy behavior?

It does not use synthetic data to claim company production readiness. The report classifies every gate as one of:

- `verified_by_synthetic_replay`
- `requires_expert_real_model_validation`
- `requires_real_amazon_shadow_pilot`

## Run

```powershell
.\.venv-clean\Scripts\python scripts\run_production_acceptance_replay.py `
  --days 90 `
  --end-at 2026-07-20T03:00:00+00:00
```

The command writes:

- `artifacts/production-acceptance/amazon-operations-90d.json`
- `artifacts/production-acceptance/production-acceptance-report.json`
- `artifacts/production-acceptance/production-acceptance-report.md`
- `artifacts/production-acceptance/expert-blind-review-template.json`

## Covered

The replay calls the existing deterministic domain functions for:

- inventory health and issue detection;
- contribution profit and missing-cost degradation;
- ACoS, click-waste and budget diagnostics;
- Listing quality;
- VOC sample-size gating;
- Buy Box, stranded inventory, Account Health and inbound-delay signal contracts;
- permanent policy blocks for refund, payment reversal and autonomous cancellation.

All replay records use synthetic sources and set `verified_amazon_data=false`.

## Not Covered

The following gates cannot pass from generated data:

- real SP-API and Advertising API pagination, throttling, timestamps, token revocation and Seller mappings;
- real model JSON Schema reliability, token/cost reconciliation and Chinese recommendation quality;
- independent expert blind-review scores;
- observed labor savings, adoption rate and SLA outcomes;
- 2-4 week Shadow Mode false-negative and false-positive performance.

The expected current decision is `conditional_go` for an internal read-only pilot and `no_go` for a company production claim.

## Replace Synthetic Evidence

For a company gate, export 30-90 days of sanitized company records into the same record envelope, keep the source truthful, and replace scenario-authored labels with labels signed by inventory, advertising, Listing and customer-service reviewers. Run the replay before connecting any write capability.
