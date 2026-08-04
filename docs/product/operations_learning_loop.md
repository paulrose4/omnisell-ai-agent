# Operations Learning Loop

## Purpose

The operations learning loop connects normalized evidence to deterministic diagnosis, operator feedback, accountable work, manual execution, outcome observation and explicitly bounded attribution. It is designed to run first with deterministic test data and later with real Amazon/Advertising/Reports adapters without changing domain rules, repositories, APIs or operator workflows.

```text
OperationsIntelligenceSource
  -> OperationsIntelligenceBatch
  -> existing Advertising / VOC repositories
  -> deterministic findings and work items
  -> operator activity and issue feedback
  -> execution preflight authorization and manual confirmation
  -> pre-registered attribution plan and outcome observation
  -> versioned operator calibration decision
```

## Adapter boundary

`commerce.intelligence_ports.OperationsIntelligenceSource` is the source boundary. A source returns one tenant/marketplace-scoped `OperationsIntelligenceBatch` containing advertising snapshots, search terms, returns, review observations, message tags and business contexts.

The ingestion service rejects records whose tenant, marketplace, source or `verified_amazon_data` flag disagrees with the batch. Mock records always use `source=mock` and `verified_amazon_data=false`. A future Amazon adapter must use reviewed API/report provenance, preserve observed and collected timestamps, strip PII before model access and independently reconcile report coverage.

The Mock adapter is `commerce.adapters.mock_operations_intelligence.MockOperationsIntelligenceSource`. It does not share Amazon credentials, endpoints or response parsers.

## Deterministic decisions

Sponsored Products evaluation combines ACoS/ROAS/TACoS/CTR/CVR/CPC and budget pacing with break-even ACoS, contribution profit and inventory status. Low ACoS cannot classify a loss-making SKU as healthy. Critical stockout, out-of-stock or stale inventory blocks growth candidates. All candidates use `automation_eligible=false` and `execution_mode=manual_only`.

VOC inputs normalize to:

- `product_defect`
- `size_or_fit`
- `shipping_damage`
- `packaging_problem`
- `wrong_or_missing_item`
- `instructions_unclear`
- `listing_mismatch`
- `subjective_dissatisfaction`
- `unknown`

The normalized reason routes work to Listing, supply chain, quality, fulfillment, customer service or operations. Insufficient samples remain non-actionable.

## Operator evidence

Operator activity is explicit, not inferred from page presence:

```text
started -> paused -> resumed -> completed
started -> completed
```

Active touch time sums active intervals. Cycle time spans first start to completion. Waiting time is the difference. Events are idempotent and tenant scoped.

Issue feedback records correctness, usefulness and one structured reason. Missed-issue reports provide false-negative evidence. Calibration metrics include precision, recall, Critical/High recall and false-positive dismissal rate. `calibration_eligible=false` until at least 30 real labels exist; the model cannot change thresholds.

## Execution and attribution

Execution attempts require the current work-item action hash and evidence. L1 execution uses a deterministic preflight authorization with freshness, currency, profit and inventory guards. Medium/high risk actions require an independent approver; all paths persist `amazon_write_performed=false` until a separately reviewed Amazon executor exists. Idempotency keys, expiry and exact parameters prevent stale or duplicate actions.

Rule calibration uses a proposal -> owner approval -> seven-day Shadow -> gate completion -> publish sequence. A proposal requires at least 30 real labels, historical replay metrics, Critical/High recall of at least 95% and no positive expected profit risk. The Calibration Agent may explain and propose, but only the Rule Publisher can create a new version after approval. Every version is auditable and rollbackable.

Attribution is registered before execution. The plan stores baseline and observation windows, primary and guardrail metrics, currency, control design and minimum sample. A deterministic evaluator selects randomized control, difference-in-differences or interrupted time-series evidence; an agent cannot upgrade an association to a causal claim.

Outcome attribution levels are:

- `execution_verified`: execution evidence only;
- `observed_association`: before/after movement without a valid control;
- `quasi_experimental`: matched control and sufficient sample;
- `causal_experiment`: randomized experiment and sufficient sample;
- `inconclusive`: missing execution or outcome evidence.

Only `causal_experiment` allows a causal claim. Other improvements are directional. Contribution profit, not revenue, is the financial outcome. Currency remains ISO 4217 and is never merged implicitly.

## Operator API

- `POST /api/learning/issues/{issue_id}/feedback`
- `POST|GET /api/learning/missed-issues`
- `POST|GET /api/learning/activities`
- `POST /api/learning/executions`
- `POST|GET /api/learning/outcomes`
- `POST /api/learning/business-contexts`
- `GET /api/learning/metrics`
- `POST|GET /api/learning/rules`
- `POST|GET /api/learning/calibration/proposals`
- `POST /api/learning/calibration/proposals/{id}/approve`
- `POST /api/learning/rules/{id}/complete-shadow`
- `POST /api/learning/rules/{id}/publish`
- `POST /api/learning/rules/{id}/rollback`
- `POST|GET /api/learning/execution-authorizations`
- `POST /api/learning/execution-authorizations/{id}/approve`
- `POST /api/learning/execution-authorizations/{id}/revoke`
- `POST|GET /api/learning/attribution-plans`

The work-item API supports three primary queues:

- `view=today`: open, assigned and in-progress work;
- `view=waiting`: blocked or awaiting approval;
- `view=verifying`: observing outcomes, failed verification or inconclusive results.

## Mock acceptance drill

1. Start the local stack with `./scripts/start_local.ps1`.
2. Sign in as Operator.
3. Open **广告与 Amazon 异常** and select **载入闭环测试数据**.
4. Confirm advertising, search-term and VOC records show Mock/unverified provenance.
5. Open **运营任务** and use the three queues.
6. Open one task, start/pause/resume/complete explicit timing and submit useful/noise feedback.
7. Record manual execution and outcome through the learning API or approved Mock execution workflow.
8. Confirm observed changes with confounders remain `observed_association`, never causal.
9. Sign in as Viewer and confirm all learning mutations are forbidden.
10. Use another tenant and confirm feedback, activities, executions and outcomes are not visible.

## Production gates

- Real Sponsored Products reports reconcile after the approved attribution freeze window and achieve at least 99% expected account-day coverage.
- VOC mapping covers at least 95% of return events and unknown reasons remain below 5%.
- Daily preparation time improves by at least 30% and median issue handling by at least 20% without reducing Critical/High recall.
- False-positive dismissal remains below 15%; Critical/High recall is at least 95%.
- Every completed action has execution evidence, baseline, observation window and explicit attribution level.
- Amazon and advertising writes remain disabled until separately reviewed executors, rollback and read-back reconciliation pass.

## Persistence and rollback

Alembic `0021` adds tenant-scoped issue feedback, missed issues, activity events, business contexts, action execution attempts and action outcomes. Alembic `0022` adds rule versions, calibration proposals, execution authorizations and attribution plans. Every table has tenant indexes, uniqueness constraints and PostgreSQL RLS.

Rollback with `alembic downgrade 0021` to remove governed calibration records, or `alembic downgrade 0020` to remove the full learning loop, before production reliance. This does not delete existing advertising, VOC, issues, actions or work items. No external compensation is required because the loop does not perform Amazon writes.
