# Change-set Review — 2026-07-20

## Decision

The working tree contains the bounded-advisor implementation plus a broader, previously uncommitted production-foundation change set. The changes are isolated on a dedicated branch, but no commit should be created until the owner confirms whether the broader production-foundation files belong in the same review.

## Agent architecture and hardening scope

The following groups are directly related to the bounded-advisor upgrade and its production hardening:

- agents/contracts.py, agents/runtime.py, agents/registry.py, agents/advisors/, agents/prompts/, agents/validators/;
- application/agent_services.py and the four application/*_agent.py services;
- workflows/engine.py and infrastructure/repositories/workflows.py;
- model_gateway/;
- api/routes/agent_run_routes.py plus Agent wiring/configuration in api/main.py and api/config.py;
- AgentRun ORM and Alembic revisions 0015/0016, plus sequential critical-approval revision 0018;
- Agent tests, evals, RLS tests and docs/AGENT_RUNTIME.md;
- runtime dependency correction for httpx SOCKS proxy support;
- Agent metrics in api/task_queue.py and api/routes/admin_routes.py.

## Broader pre-existing production-foundation scope

These changes are required by the current tested application but extend beyond the Agent hardening review and should be confirmed as a separate commit or prerequisite series:

- Amazon/OIDC, sync, profitability, inventory, listing-quality, reconciliation and customer-service modules;
- frontend/, deploy/, Docker Compose overlays and operational scripts;
- the earlier Alembic migration chain 0001–0014 and the Listing experiment execution revision 0017;
- large rewrites of DEPLOYMENT.md, README.md, api/security.py and pricing routes.

## Excluded local artifacts

The following are not candidates for staging:

- virtual environments, node_modules, .next, pytest caches, __pycache__ and local SQLite databases;
- local .env files containing runtime secrets;
- the ignored root `提示词.txt` local prompt backup.

Example environment files remain eligible only because they contain placeholders or non-secret sample values.

## Security scan

A high-confidence scan found no AWS access-key identifiers, private keys, OpenAI-style secret keys, Slack tokens, or long inline client/access/refresh secrets in the repository change set.

## Verified baseline

- Clean Python 3.13 virtual environment install from `requirements.txt`: passed on 2026-07-18 and revalidated on 2026-07-19.
- Clean-environment pip check: passed with no broken requirements.
- Clean-environment full pytest: 421 passed, 4 skipped, with one non-blocking Starlette TestClient deprecation warning.
- Clean-environment compileall: passed.
- Temporary PostgreSQL 16 migration upgrade/downgrade/re-upgrade: passed.
- PostgreSQL non-owner RLS tests, including agent_runs and approval_decisions: 4 passed.
- Frontend typecheck and production build: passed.
- Local Compose is running at revision 0018 for append-only sequential critical approvals. API health reports four ready Advisors, Celery worker and scheduler are running, and Amazon remains in read-only pilot mode.
- The Listing operations workbench was exercised in a real browser from synchronized Mock data through issue review, experiment approval input and lifecycle display. Invalid/stale frontend sessions now reauthenticate instead of returning BFF 500 errors.
- Browser acceptance covered Agent, reconciliation, approval, issue and Listing workbenches at 320, 375, 390, 768, 1024 and desktop widths. No document-level overflow or out-of-bounds primary control was found. The mobile drawer opens, closes with Escape and closes after route navigation.
- Real-model compatibility was verified on 2026-07-18 with sanitized evidence against hxprice `gpt-5.6-terra`: all four normal Advisor schemas passed after bounded repair, and prompt-injection/nonexistent-evidence/forbidden-action cases preserved safety boundaries. Billing reconciliation and operator quality scoring remain open.
- The first 2026-07-19 repeat exposed a smoke-tool budget drift rather than a production-profile failure: the tool used one fixed 6,000/3,000 token context instead of the four reviewed profiles. The smoke now reads the same typed profile source as runtime and records the applied limits in every case.
- The final single-manifest repeat succeeded for all four Advisors with strict Schema, Evidence validation, non-zero cost accounting and provider request IDs. Operations used 11,511 tokens / `$0.0879650`; Customer Case 7,757 / `$0.0465425`; Listing v3 6,115 / `$0.0343375`; Reconciliation v5 10,130 / `$0.0716750`. All remained within their reviewed profiles.
- Current prompt-injection, nonexistent-evidence and refund/cancel/approval-bypass cases also passed without forbidden proposed actions. Provider compatibility is now a Go; sustained reliability, billing reconciliation, operator quality scoring and real Amazon validation remain release gates.
- Tenant Agent call quotas now enforce atomic per-minute and per-day limits in Redis; idempotent replays do not consume quota, while quota exhaustion is persisted and returned as HTTP 429.
- Agent submission now pre-creates a tenant-scoped AgentRun and durable task, returns `202` in tens of milliseconds, and executes through the Celery text queue with a Redis run lock. Concurrent resume requests were verified to return one retry run.
- Approval resume now validates the canonical action hash, approved plan, evidence, expected changes, tenant/run/action/resource identity, approver independence and expiry before execution. Any mismatch is persisted as a non-retryable `ApprovalIntegrityError`.
- Refund, payment-reversal and Agent cancellation intents are rejected before HITL persistence, including intents nested in structured action plans. A legacy prohibited request already in `pending` is atomically changed to `rejected` and audited when approval is attempted; one or two approvers can never override this policy.
- Provider usage is fail-closed when token counts are missing, invalid, negative or inconsistent. SQL and JSONL audit sinks apply the same recursive secret, PII, prompt and evidence redaction policy.
- Real Amazon read-only verification remains an explicit external staging gate.
- Runtime preflight now enforces total evidence and complete-request budgets before the provider call, deterministically records omitted evidence, and degrades rather than hiding reduced coverage.
- Startup and runtime preflight now reserve both cost and cumulative token capacity for one strict-Schema repair. The global task budget is 36,000 tokens; incompatible provider rates, Advisor cost profiles or global token limits fail before a provider call.
- Critical approvals now require two separately authenticated decisions persisted in `approval_decisions`; one operator can no longer type a second identity or resume the workflow after the first decision.
- Complete issue scans use only the latest SKU snapshot, close absent active conditions, cancel stale proposed actions and block contradictory stockout/excess recommendations.
- Agent, reconciliation and approval workbenches now expose generation method, evidence coverage, typed business context and read-only provenance; mobile navigation uses an accessible drawer without primary-navigation horizontal scrolling.
- A real browser reconciliation run completed `running -> succeeded` with 4 explicit differences. Missing Amazon orders and Listings are persisted as `missing_amazon` instead of aborting the batch, reconciliation parent rows are flushed before child differences, and the UI polls the durable task to show success or the sanitized failure reason.
- The 2026-07-20 provider repeat first exposed the former 14,000-token aggregate repair limit after two successful HTTP responses used 20,827 tokens; that local configuration defect is fixed and regression-tested. A subsequent 36,000-token run reached hxprice but received three consecutive HTTP 504 responses and correctly failed as `ModelProviderError` with zero token/cost persistence and no side effects. Historical strict-Schema compatibility remains valid, but current provider reliability is a release gate.

## Production-foundation prerequisite confirmation

Owner decision recorded on 2026-08-04: **Accept prerequisite series**. The intended release includes the tenant-scoped operations product that the bounded advisors read from. The production foundation will be committed first, followed by deterministic operations capabilities, bounded advisors, the frontend console, and deployment/evaluation evidence. Generated outputs, local environments, secrets and root test-result XML files remain excluded.

Confirm this by dependency, not filename ownership alone: **does the intended release include the tenant-scoped operations product that the bounded advisors read from?** If yes, the prerequisite belongs in the same branch and review series, but in an earlier commit.

The confirmation covers:

- domain/application models for tenant, Amazon connection, cost/profit, inventory, issues, pricing, Listing quality, customer cases and reconciliation;
- database models/repositories, RLS and Alembic revisions 0001–0014;
- OIDC/MFA/RBAC, secret storage, Amazon reads, sync/reconciliation and policy/HITL/audit;
- production Compose, migrations, worker/scheduler, frontend auth proxy and operational scripts;
- focused tests and product/deployment documentation.

It does **not** accept every untracked file. Mock-only consoles, local fixtures, root prompt artifacts and generated/local files remain excluded or separately classified.

Record one owner decision:

1. **Accept prerequisite series** — commit production foundation first, then bounded advisors. Recommended for the intended company product.
2. **Agent-only review** — do not commit yet; rebase Agent work onto an already committed production-foundation baseline.
3. **Technical pilot only** — exclude business modules and revise product claims; this does not satisfy the control-tower goal.

## Production migration history gate

Revision 0001 currently creates the live ORM metadata rather than a frozen schema. Before committing the prerequisite series, record whether any persistent environment has already used revisions 0001–0014. If none has, regenerate a frozen baseline. If one has, preserve history and add a corrective migration. See 'docs/PRODUCTION_FOUNDATION_AUDIT.md'.

## Commit recommendation

Use an ordered review series:

1. Production foundation core and frozen migration baseline, with focused tests.
2. Deterministic operations capabilities backed by revisions 0002–0014, with focused tests.
3. Bounded advisor runtime and Agent migrations 0015–0016, with evals/tests.
4. Listing experiment execution lifecycle and revision 0017, then the Listing operations UI.
5. Deployment validation.
6. Optional Mock drill/local E2E scope.

Revision 0017 must remain after 0016 in Git history because its Alembic `down_revision` is 0016, even though the Listing execution lifecycle is reviewed as a business/UI capability rather than Agent runtime infrastructure.

Do not defer all tests to a final commit; every implementation commit should remain reviewable and green.

Do not stage the entire working tree with git add -A until this ownership decision is recorded.

## Upstream safety baseline reconciliation

The 2026-08-04 merge of `origin/main` commit `822d6e0` preserves upstream ancestry but keeps the current fail-closed safety implementation where the two lines diverged.

- `refund_gate.py` and `hitl.py` permanently reject refunds, payment reversals and autonomous cancellations before an approval request can be created. The older `refund_constraint.py` expected refund execution to enter HITL and failed four tests against the current permanent-prohibition policy.
- `tool_acl.py` and `tool_auth.py` require registered, tenant-bound caller context. The older `tool_permissions.py` allowed unregistered tools to pass through, which conflicts with the repository security rules.
- Current read-only Price and Sync Agent boundaries, async-safe tool wrapping and `domain_guardrail.py` remain authoritative.
- The upstream static screenshots, binary test reports, stale 88-test summary and architecture notes tied to the retired modules are excluded from the review tree. Current reproducible tests, evals and runbooks are the evidence source instead.
