# OmniSell Interview Start Here

OmniSell is an evidence-driven Amazon operations control tower with four bounded AI Advisors. It is not a free-chat agent and it does not allow an LLM to decide money, inventory, permissions, approval, or execution parameters.

## 90-second pitch

> I built OmniSell to answer a production question: how can an operations team use an LLM without giving an uncertain model authority over inventory, money, or approvals? The platform turns Amazon-shaped operational facts into tenant-scoped Issues, evidence-backed Advisor recommendations, immutable approval plans, and audited manual execution. FastAPI, Next.js, PostgreSQL RLS, Redis, and Celery provide the full-stack control plane. The Agent Runtime enforces strict Pydantic schemas plus evidence, context, token, cost, and timeout budgets. I also fixed an approval-integrity flaw where a client could theoretically approve plan A but submit parameters for plan B; execution now rebuilds its command only from the server-side approved snapshot and action hash. The repository maintains 770+ automated tests, but the current public posture remains a read-only pilot: Golden Mock data is synthetic, real Amazon writes are blocked, and real-provider quality is still a no-go gate until a fresh 30-50 case evaluation is completed.

## 5-minute demo

1. Open the control-tower screenshot and point out the **Golden Mock** disclosure before discussing any metric.
2. Follow one stockout-risk Issue from deterministic inventory facts to an Advisor explanation containing evidence, confidence, missing data, and freshness.
3. Show the approval request and explain the tenant, connection, marketplace, canonical plan, policy version, evidence, and action hash binding.
4. Show that the execution API accepts an `approval_request_id` and `Idempotency-Key`, not editable business parameters.
5. Finish with the verification summary and the explicit external gates: real Amazon writes, named OIDC operators, real-provider evaluation, and a 2-4 week read-only pilot.

For the reproducible local workflow, use [`../INTERVIEW_DEMO_RUNBOOK.md`](../INTERVIEW_DEMO_RUNBOOK.md).

## 15-minute architecture path

1. **Product boundary:** [`ONE_PAGE_PROJECT_OVERVIEW.md`](ONE_PAGE_PROJECT_OVERVIEW.md)
2. **Deterministic versus advisory responsibilities:** [`../decisions/0002-deterministic-business-math.md`](../decisions/0002-deterministic-business-math.md)
3. **Bounded Agent Runtime:** [`../AGENT_RUNTIME.md`](../AGENT_RUNTIME.md)
4. **Approval integrity:** [`INCIDENT_02_APPROVAL_EXECUTION_BINDING.md`](INCIDENT_02_APPROVAL_EXECUTION_BINDING.md)
5. **Schema repair and honest no-go decision:** [`INCIDENT_01_SCHEMA_REPAIR_LATENCY.md`](INCIDENT_01_SCHEMA_REPAIR_LATENCY.md)
6. **Tenant and execution threats:** [`../security/THREAT_MODEL.md`](../security/THREAT_MODEL.md)
7. **Dated evidence:** [`../evidence/verification/verification-summary-2026-08-10.md`](../evidence/verification/verification-summary-2026-08-10.md)

## Mandatory disclosure boundaries

- The public screenshot and golden workflow use synthetic **Golden Mock** data. They are not verified Amazon records.
- The current release profile is `read_only_pilot`; real Amazon writes, refunds, payment reversals, autonomous cancellation, and cross-tenant operations remain blocked.
- `776 passed, 10 skipped` is a dated automated verification result, not a production business outcome.
- Fixed-fixture Agent success proves the evaluator and contracts only. Real-provider quality, latency, repair rate, and cost remain `not_verified` and the release decision remains `no-go`.
- The accurate contribution statement is **personally led the architecture refactor and engineering delivery**, not sole authorship of every historical commit.

## Three stories to lead with

- Why deterministic code, rather than an LLM, owns money, inventory, permissions, approval, and state transitions.
- How the approval-to-execution binding prevents "approve A, execute B" and replay risks.
- Why a final parseable model response was still judged a no-go after 100% of the observed real-provider cases required Schema Repair.
