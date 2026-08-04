# High-Fidelity Amazon US UAT

This drill validates OmniSell with deterministic, fully synthetic Amazon-shaped data. It does not prove Amazon production connectivity or permit Amazon writes.

## Safety boundary

- Every generated record uses `source=mock` and `verified_amazon_data=false`.
- Buyer names, addresses, phone numbers, email addresses, and payment data are not generated.
- The load path is Mock SP-API / reports -> adapters -> deterministic diagnostics -> work items and customer cases.
- `amazon_write_performed` must remain `false`.
- Local Compose accelerates the client-side token bucket only for `mock-cloud`; HTTP 429 and retry behavior remain available as explicit scenarios.
- Reset the mock tenant before repeating a scored run so earlier cases do not contaminate results.

## Fixed 30-day UAT profile

| Data | Count |
| --- | ---: |
| SKUs | 100 |
| Orders | 10,000 |
| Inventory snapshots | 3,000 |
| Sponsored Products campaign-day facts | 1,500 |
| Search terms | 3,000 |
| Returns | 500 |
| Buyer messages | 800 |
| Reviews / VOC observations | 300 |
| Customer-service UAT cases | 300 |
| Business contexts | 50 |
| Data-quality / delivery events | 5 |
| Mock action and attribution trials | 4 |
| Ground-truth labels | 100 |

The reference dataset is generated with seed `20260722` and reference time `2026-07-22T04:00:00Z`. Its expected fingerprint is `822132ac7341cf80092277d693af7ccb9e25b0918e29d0107998cb3e25d9a353`.

The five delivery defects are a late report, a missing partition, a restated report, a cross-currency row that must be quarantined, and a duplicate notification. The four action trials cover an observed regression, a quasi-experimental improvement, a randomized causal result, and an inconclusive result with missing metrics. These are expected-answer fixtures: a UAT failure is useful evidence that the corresponding product capability is not ready.

## Generate files

From the repository root:

```powershell
python -m scripts.generate_high_fidelity_mock_store `
  --output-dir outputs/high_fidelity_amazon_us_30d `
  --reference-time 2026-07-22T04:00:00Z
```

The output directory contains CSV data for analysis, JSONL orders shaped for Mock SP-API, `manifest.json`, and `omnisell_high_fidelity_uat.xlsx`. `data_quality_events.csv` and `action_trials.csv` contain machine-readable expected behavior for ingestion and attribution tests. Use the workbook to assign cases and record the original-process and OmniSell active-touch times.

## Load through OmniSell

1. Start the local stack and complete Mock Amazon authorization.
2. Open `/mock-scenarios` as Owner or Admin.
3. Select the Mock Amazon connection.
4. Click `载入30天高拟真数据` once and wait for the sync result.
5. Confirm the response shows 100 SKUs, 10,000 orders, 300 customer cases, `Amazon验证：否，合成数据`, and the expected fingerprint prefix.
6. Inspect `/customer-cases`, `/intelligence`, and the three work queues. Evidence must remain tenant-scoped and traceable to a mock source.
7. Read `/mock/high-fidelity/data-quality-events` and `/mock/high-fidelity/action-trials` from Mock Cloud when exercising report recovery, idempotency, and outcome attribution.

The button calls `POST /api/mock-scenarios/high-fidelity/seed`. The API loads the same generator configuration into Mock Cloud, synchronizes orders/inventory/listings through existing adapters, ingests advertising/VOC facts, and creates customer cases through the normal workflow.

## Customer-service study

Use 3-5 operators and split cases into two matched groups. Group A first uses Seller Central/Excel, then OmniSell on different cases. Group B uses the reverse order. A person must not process the same case twice.

Record these columns in `UAT Cases`:

- operator and completion status;
- legacy and OmniSell active-touch minutes;
- classification and routing correctness;
- whether the suggested reply was adopted;
- reopen status;
- any PII or policy violation;
- operator notes.

The workbook calculates time saved and pass/review status. Compare operator decisions with `Ground Truth`; do not expose ground truth before the case is completed.

## Acceptance gates

- Median active-touch time per case improves by at least 20%.
- Evidence and order lookup time improves by at least 30%.
- Classification and routing accuracy are at least 90%.
- High-risk case recall is at least 95%.
- Useless recommendations remain below 15%.
- PII leaks, unauthorized refunds, off-platform promises, and Amazon writes remain zero.
- At least 80% of operators complete work through the task workflow.

A pass means the synthetic operational workflow is feasible. It does not replace a real Amazon read-only Shadow Pilot, frozen-window reconciliation, or production authorization review.

## Scoring discipline

- Score business detection against `ground_truth.csv`; do not infer expected alerts from page appearance.
- Score report recovery against `data_quality_events.csv`. Late or incomplete data must downgrade freshness instead of producing a false business alert.
- Score action results against `action_trials.csv`. Only the randomized, sufficiently sampled trial permits a causal claim.
- Do not expose expected answers to operators before they complete their assigned cases.
- Reset the tenant and reload the same fingerprint before every comparable run.
