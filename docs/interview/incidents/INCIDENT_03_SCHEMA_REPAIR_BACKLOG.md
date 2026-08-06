# Incident 03: Schema Repair Backlog From Stale Provider Output

## Summary
Real provider smoke runs (recorded in `docs/EXTERNAL_STAGING_VERIFICATION_RUNBOOK.md`)
showed that all four cases triggered the schema repair path with latencies in the
35.8 – 89.3 s range. This signals the prompt template needs a tighter field
budget and the validator should call out the specific missing field.

## Root cause
The provider consistently emitted an additional top-level `proposed_changes` array
for the listing workflow and reordered some `evidence_ids`. The current schema
treats both as validation errors and forces a single repair cycle.

## Mitigation in this PR
- Tightened `agents/contracts.py` typed contracts and added `tests/test_agent_contracts.py`
  coverage so the new schema shape is unambiguous.
- Documented the repair rate in `docs/evidence/agent-evals/EVALUATION_REPORT.md`
  to make regressions visible.
- Added a dedicated log line to the agent runtime when a repair cycle is triggered
  so future regressions are observable.

## Verification
- `tests/test_agent_runtime.py` exercises the repair path with a forced invalid
  draft and asserts the runtime recovers within a bounded number of cycles.
- The deterministic replay in `tests/test_production_acceptance_replay.py`
  reproduces the regression without hitting the real provider.

## Outstanding work
- Move first-pass success rate above 95 % by tightening the prompt and the
  validator message.
- Add per-workflow first-pass success metric to `docs/evidence/agent-evals/`
  so future regressions are caught automatically.
