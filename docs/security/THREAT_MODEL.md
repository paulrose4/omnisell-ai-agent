# OmniSell Threat Model

> Status: reviewed on 2026-08-06 alongside the production hardening series.

## 1. Scope and Assets

| Asset | Why it matters | Owner |
| --- | --- | --- |
| Tenant operational data (orders, listings, profit, inventory) | Legal / contractual records | Tenant admin |
| Approval decisions and audit trail | Compliance evidence | Operations compliance |
| LLM prompt / response stream | May contain PII or customer data | Tenant admin |
| Mock provider state | Used for UAT; must not leak to production | Release engineering |
| Real Amazon SP-API credentials | Cross-tenant impact if misused | Tenant admin |
| Database connection strings and DB role grants | Cross-tenant impact | Platform SRE |

## 2. Trust Boundaries

1. Browser (operator / approver) -> Next.js middleware -> backend API (`api/`).
2. Backend API -> Model gateway (`model_gateway/`).
3. Backend API -> PostgreSQL via a fixed non-owner role `omnisell_app`.
4. Backend API -> Mock provider or Amazon SP-API through a single commerce adapter.
5. Background workers (Celery) -> Redis, Chroma, model gateway, same database.

Each boundary is enforced with at least one of: authentication, authorization,
policy evaluation, schema validation, audit logging and budget enforcement.

## 3. Adversary Model

- **Curious operator**: probes for cross-tenant visibility.
- **Compromised model output**: tries to influence amount, inventory, approval status.
- **Malicious approver**: tries to forge approval requests.
- **Replay attacker**: tries to reuse an old approval for a new action.
- **Insider with DB read access**: tries to bypass RLS via superuser fallbacks.

## 4. Attack Paths and Controls

| Attack path | Control | Test / evidence |
| --- | --- | --- |
| Cross-tenant read | `app.current_tenant_id` session variable + PostgreSQL RLS | `tests/test_postgres_rls.py` (10 cases) |
| Cross-tenant write | RLS policy + RLS force flag + `omnisell_app` non-owner role | `tests/test_postgres_rls.py::test_fixed_postgresql_application_role_is_non_owner_nobypassrls_and_tenant_isolated` |
| Prompt injection escalating action severity | `agents/validators/evidence.py` rejects out-of-policy actions and unsupported claims | `tests/test_bounded_advisor_workflow.py` |
| Approval replay | `workflows/engine.py` rebinds approval to immutable plan hash + re-verifies tenant / connection / policy / state / expiry before execution | `tests/test_workflow_approval_integrity.py` |
| Two-person approval bypass | `safety/policy_engine.py` requires distinct approvers and recomputes risk | `tests/test_operational_issue_approval.py` |
| Authorization tampering | JWT validated by OIDC middleware + scope check + tenant claim | `tests/test_security_middleware_unit.py` |
| Provider failure cascading | `model_gateway.FailoverModelGateway` + typed `GatewayBudget` | `tests/test_model_gateway.py` |
| Schema / payload DoS | `agents/contracts.py` resource budget + `application/agent_payload_audit.py` | `tests/test_agent_payload_audit.py`, `tests/test_remaining_diff_contracts.py` |
| Mock provider leaking to production | `api/release_profiles.py` mounts routes per profile; `mock-cloud` only runs in `golden_demo` | `tests/test_release_profiles.py`, `tests/test_production_quality_gates.py` |
| Audit log tampering | Append-only audit + redaction via `safety/audit.py` | `tests/test_audit_redaction.py` |
| Refund or autonomous cancellation | Explicitly refused in `read_only_pilot` profile | `tests/test_refund_safety.py` |

## 5. Residual Risk

- **Real LLM provider quality** still depends on the upstream vendor and is verified
  only against the synthetic regression fixtures. Real provider evaluation requires
  an external environment and is tracked in `docs/EXTERNAL_STAGING_VERIFICATION_RUNBOOK.md`.
- **Real Amazon SP-API** is not exercised; the pilot is read-only. Promotion requires
  a human sign-off captured in `docs/OPERATOR_PRODUCTION_ACCEPTANCE.md`.
- **Operator adoption rate** is unmeasured; targeted by the planned 2–4 week pilot.

## 6. Review Cadence

- Re-run RLS suite on every schema change.
- Re-run agent payload audit on every `agents/contracts.py` change.
- Threat model reviewed alongside each release profile promotion.
