# Verification Summary - 2026-08-10

This file is the dated source of truth for public interview claims. Stable public copy should say **770+ automated tests** and link here for the exact run.

## Automated result

| Gate | Result | Boundary |
| --- | --- | --- |
| Full Python suite | **776 passed, 10 skipped, 0 failed** | Python 3.12.10; the skipped suite requires PostgreSQL and a non-owner application role |
| Final governance suite | **23 passed** | Focused release, evidence, security, and interview-contract checks |
| PostgreSQL RLS | Passed | PostgreSQL 16 schema roundtrip, non-owner application role, tenant policies, and migration fingerprint evidence |
| Four-persona browser E2E | Passed | Owner, operator, approver, and reviewer OIDC personas in the controlled CI profile |
| Database migrations | Passed | Alembic upgrade/downgrade and schema fingerprint gates |
| Supply-chain and secret gates | Passed | SBOM generation, Trivy scanning, and Gitleaks |

## Product surface covered

The verified release surface includes four bounded Advisors:

1. Operations Brief
2. Customer Case
3. Listing Optimization
4. Reconciliation

Advisor output remains advisory. Money, fees, inventory, authorization, policy, approval, and state transitions remain deterministic.

## Evidence interpretation

- The test count is engineering evidence, not a claim of Amazon business impact.
- Golden Mock fixtures are synthetic and must not be described as verified Amazon data.
- PostgreSQL RLS and four-persona E2E results demonstrate the tested control path; they do not replace a named external security assessment.
- The repository contains versioned agent-evaluation fixtures and reports, but fixed-fixture structured success does not prove real-provider quality.

## External gates still open

- Real Amazon write operations remain blocked in the `read_only_pilot` profile.
- Real-provider first-pass schema success, repair rate, P50/P95 latency, token use, and cost require a new controlled 30-50 case run.
- The current real-provider quality conclusion remains **no-go / not_verified**.
- Named operator sign-off and a 2-4 week read-only pilot are required before any production-readiness claim.

## Reproduction pointers

- Local workflow: [`../../LOCAL_DEVELOPMENT.md`](../../LOCAL_DEVELOPMENT.md)
- Demo runbook: [`../../INTERVIEW_DEMO_RUNBOOK.md`](../../INTERVIEW_DEMO_RUNBOOK.md)
- Migration evidence: [`../migrations/`](../migrations/)
- Agent evaluation: [`../agent-evals/EVALUATION_REPORT.md`](../agent-evals/EVALUATION_REPORT.md)
- Security model: [`../../security/THREAT_MODEL.md`](../../security/THREAT_MODEL.md)
