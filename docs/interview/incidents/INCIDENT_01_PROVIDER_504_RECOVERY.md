# Incident 01: LLM Provider 504 with Bounded Failover Recovery

## Summary
While load-testing the `golden_demo` stack with the primary OpenAI-compatible
provider returning HTTP 504, the bounded failover gateway transparently retried
against the configured fallback provider, preserved token / cost budgets and
returned a `ProviderUnavailable` outcome to the workflow. The advisory was still
rendered with stale-but-valid evidence and the operator workflow surfaced a clear
"model failed; advisory based on cached evidence" banner.

## Timeline
- `T+0`: Planner requested 4,500 completion tokens from primary provider.
- `T+~30s`: Primary gateway returned 504.
- `T+~30.1s`: `FailoverModelGateway` invoked fallback provider with the exact
  same structured schema and prompt version.
- `T+~80s`: Fallback succeeded, returned valid structured output.
- `T+~80.1s`: Workflow continued with evidence references preserved, plan hash
  unchanged.

## Detection
- Provider latency histogram alert (>60s p95).
- Workflow status remained `analyzed` rather than `failed`.

## Mitigation
- Failover is bounded by `GatewayBudget.max_tokens_per_task`; the budget is
  recomputed on fallback invocation so the worst case is preserved.
- The recommendation is re-validated by `agents/validators/evidence.py` so the
  fallback provider cannot silently rewrite the plan.

## Follow-ups
- Capture the run-time delta in `docs/evidence/agent-evals/EVALUATION_REPORT.md`
  under the "failover latency" subsection.
- Consider adding a circuit breaker to short-circuit the primary for 60 s after
  three consecutive 5xx responses.

## Verification
- `tests/test_model_gateway.py::test_failover_budget_is_enforced` (contract test).
- Manual smoke via `scripts/run_real_model_staging_smoke.py` (when the local
  ollama + wenrugouai provider pair is reachable).
