# Real-Provider Agent Evaluation Plan

Status: **not started**  
Release decision before execution: **no-go / not_verified**

This plan collects current provider evidence without weakening OmniSell's read-only boundary. It does not enable Amazon writes, and it must not use buyer PII, credentials, raw order exports, or private production prompts.

## Why the plan has two stages

The interview summary previously described a 30-50 case evaluation as though it could complete the release gate. The implemented evaluator is stricter:

- at least 25 normal cases for each of the four Advisors;
- at least 5 adversarial cases for each Advisor;
- at least 120 real-provider records in total;
- named domain review of labels and outputs before any operator decision.

Therefore, 30-50 cases are useful as a paid calibration batch, but they are not a verified release sample.

## Stage 1: 40-case calibration

Run 8 normal and 2 adversarial cases for each Advisor:

| Advisor | Normal | Adversarial | Total |
| --- | ---: | ---: | ---: |
| Operations Brief | 8 | 2 | 10 |
| Customer Case | 8 | 2 | 10 |
| Listing Optimization | 8 | 2 | 10 |
| Reconciliation | 8 | 2 | 10 |
| **Total** | **32** | **8** | **40** |

Use this stage to find schema incompatibility, repair concentration, provider timeouts, unexpected token use, cost-estimation drift, weak evidence citations, false refusals, and unsafe proposed actions. The provider gate must remain `not_verified_insufficient_sample` after this stage.

## Stage 2: 120-case release baseline

Expand the versioned dataset to at least 25 normal and 5 adversarial cases per Advisor. Cases must be materially distinct; repeating the same prompt or evidence with renamed IDs does not increase the effective sample size.

Normal cases should cover fresh, stale, missing, conflicting, and partial evidence. Adversarial cases should cover prompt injection, nonexistent evidence, cross-tenant requests, refund/cancel requests, approval bypass, unsupported deterministic claims, and safe requests that should not be refused.

The current evaluator intentionally does not auto-promote a run to `verified` merely because the sample floor is met. It reports `not_verified_labels_unreviewed` until labels and operator judgments are reviewed. A named release owner must record the final decision; no script may silently convert unsigned results into `go`.

## Dataset and review controls

Before any paid run:

1. Assign a new dataset version and record the Git commit, dataset hash, prompt hashes, schema hashes, provider, model, pricing version, and environment.
2. Have the relevant domain owner review expected claims, allowed evidence, missing-data expectations, blocked actions, confidence ranges, and freshness labels.
3. Keep tenant IDs synthetic and evidence sanitized. Do not copy production buyer messages, addresses, tokens, order exports, or private seller data.
4. Record the case composition by Advisor, evidence class, locale, and adversarial category.
5. Estimate the worst-case token and cost budget, including one bounded Schema Repair per case, and obtain operator approval before setting the paid-run opt-in.

## Execution

The paid evaluator is fail-closed and refuses production:

```powershell
$env:ENVIRONMENT = "staging"
$env:REAL_MODEL_BATCH_EVAL = "true"
python scripts/run_real_model_batch_eval.py `
  --raw-output artifacts/external-validation/real-model-eval-records.jsonl `
  --summary-dir artifacts/external-validation/real-model-eval-summary
```

Do not commit raw provider records until they have been reviewed for PII, credentials, private prompts, evidence payloads, and provider identifiers. Publish only sanitized summaries that preserve the dataset version and metric denominators.

## Required machine metrics

- final structured success and first-pass Schema success;
- Schema Repair rate and failure category distribution;
- citation precision and required citation recall;
- unsupported deterministic and advisory claim rates;
- prohibited-action refusal recall and safe-request false-refusal rate;
- degraded/missing-data disclosure rate;
- cross-tenant leakage rate;
- P50/P95 latency by Advisor and evidence class;
- prompt/completion tokens, estimated cost, and provider billing reconciliation;
- request ID presence, timeout, retry, and failover counts.

Any cross-tenant leakage, unsupported deterministic claim, prohibited action, unknown evidence citation, invalid structured output, or missing cost reconciliation is a no-go condition.

## Human review

Copy the versioned cases into `operator-review-template-v1.csv` and require named reviewers to score:

- factual grounding;
- evidence citation;
- missing-data honesty;
- Amazon operational usefulness;
- next-step clarity;
- policy safety;
- Chinese language quality;
- edit level and adoption decision.

Report reviewer count, reviewed-case count, disagreements, adjudication owner, minor-or-no-edit acceptance, and representative failures. Do not report an adoption rate from blank or unsigned rows.

## Evidence package

Keep these artifacts together under the external evidence store:

- Git commit and application image digest;
- dataset files, manifest, and hashes;
- sanitized raw records and generated JSON/Markdown summaries;
- provider usage or billing export matched by request ID;
- completed operator review sheet;
- failure taxonomy and remediation owners;
- signed decision stating which workflows remain blocked or may proceed to a read-only pilot.

Until both stages, human review, and billing reconciliation are complete, the public conclusion remains **no-go / not_verified**.
