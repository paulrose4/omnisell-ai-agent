# OmniSell Personal Contribution Matrix

The matrix below is the source of truth for "which parts of this repository did
the candidate actually design and own". Every row references the commit, the
modules touched and how the work was verified.

## 1. Production foundation

- **Scope**: tenant-scoped repositories, RLS bootstrap, deterministic business
  modules, audit pipeline, settings/release profiles.
- **Modules owned**:
  `infrastructure/database/**`, `domain/**`, `safety/audit.py`,
  `infrastructure/repositories/audit.py`, `api/release_profiles.py`,
  `api/route_catalog.py`, `application/golden_mock.py`.
- **AI assistance**: none for the deterministic modules. AI-assisted brainstorming
  on initial RLS policies, then hand-written and reviewed.
- **Verification**: `tests/test_production_foundation.py`,
  `tests/test_postgres_rls.py`, `scripts/capture_schema_fingerprint.py`.

## 2. Agent runtime and contracts

- **Scope**: bounded LLM runtime, evidence budget, structured output enforcement,
  typed action arguments, payload compatibility scanner.
- **Modules owned**: `agents/runtime.py`, `agents/contracts.py`,
  `agents/contract_adapters.py`, `agents/validators/evidence.py`,
  `model_gateway/gateway.py`, `model_gateway/factory.py`,
  `application/agent_payload_audit.py`.
- **AI assistance**: AI assisted in sketching initial Pydantic schema; every field
  constraint, max_length and validator was reviewed against the design plan.
- **Verification**: `tests/test_agent_contracts.py`,
  `tests/test_agent_contract_adapters.py`, `tests/test_agent_payload_audit.py`,
  `tests/test_remaining_diff_contracts.py`, `tests/test_model_gateway.py`.

## 3. Approval safety

- **Scope**: separation of `policy_decision` vs `risk_level`, immutable plan
  binding on approval recovery, idempotent manual execution.
- **Modules owned**: `safety/policy_engine.py`,
  `infrastructure/repositories/hitl.py`, `workflows/engine.py`,
  `application/agent_worker.py`.
- **AI assistance**: none for policy engine semantics. AI assistance used to draft
  Pydantic schemas for approval payloads.
- **Verification**: `tests/test_workflow_approval_integrity.py`,
  `tests/test_operational_issue_approval.py`,
  `tests/test_repository_lifecycle_coverage.py`,
  `docs/interview/INCIDENT_02_APPROVAL_EXECUTION_BINDING.md`.

## 4. Frontend console and Golden demo

- **Scope**: Amazon operations control tower UI, sign-in, mock Seller Central
  workbench, focused interview navigation profile.
- **Modules owned**: `frontend/app/**`, `frontend/components/**`,
  `frontend/middleware.ts`, `frontend/playwright.config.ts`,
  `frontend/e2e/golden-stockout.spec.ts`,
  `frontend/scripts/generate-bff-route-inventory.mjs`.
- **AI assistance**: AI helped propose component layouts and selectors. All UI
  behaviour was manually verified against the golden demo and approved.
- **Verification**: `npm run typecheck`, `npm run build`,
  `npm run test:e2e:golden`, screenshots / traces stored under
  `frontend/playwright-report/`.

## 5. Tooling and evidence

- **Scope**: pyproject manifest, Ruff + Pyright + coverage gates, dependency
  locks, schema fingerprint capture, OpenAPI and route inventory, interview
  documentation, ADRs and threat model.
- **Modules owned**: `pyproject.toml`, `.github/workflows/ci.yml`,
  `scripts/check_dependency_locks.py`, `scripts/check_critical_coverage.py`,
  `scripts/capture_schema_fingerprint.py`,
  `scripts/generate_openapi_schema.py`,
  `scripts/generate_route_inventory.py`,
  `docs/decisions/*`, `docs/security/THREAT_MODEL.md`,
  `docs/interview/**`, `docs/evidence/**`.
- **AI assistance**: AI assisted in writing the ADRs and the threat model draft;
  every entry was cross-checked against the corresponding test or code module.
- **Verification**: `ruff check`, `pyright`, `pip check`,
  `scripts/check_dependency_locks.py`,
  `scripts/check_critical_coverage.py`,
  `docs/evidence/migrations/0023_postgresql16_schema_rls_fingerprint.md`.

## 6. What was NOT done by the candidate

- Real Amazon SP-API integration, real OIDC provider wiring, real LLM provider
  benchmarking: explicitly retained as external gates.
- The history commit `b9cb749 Add production safety gates and permission model`
  was authored by `Ava <elovedollssale@gmail.com>` and is preserved unchanged.
