# Current Business Capability Audit

## Executive conclusion

As of 2026-07-17, the current working tree is the correct architectural direction for a company-grade Amazon operations system, but it is **not yet a general-production release**. It is suitable for a controlled, tenant-scoped, read-only internal pilot after the production-foundation prerequisite and bounded-advisor changes are reviewed and committed as an ordered series.

The previous conclusion was stale: deterministic SKU profitability, inventory health, unified issues, a daily cockpit, profit-constrained pricing and Listing experiments now exist in code and tests. The remaining work is real external validation, operator ownership decisions and action-outcome/advertising/returns loops.

## Release profiles

| Profile | Decision | Required boundary |
|---|---|---|
| Local or Golden Mock drill | Go | Synthetic data visibly marked; Amazon writes disabled |
| Internal company read-only pilot | Conditional Go | Prerequisite commit, OIDC/MFA, PostgreSQL/RLS, Redis/Celery, Amazon sandbox read validation, runbooks and named operators |
| Recommendation and approval pilot | Conditional Go | Read-only gates plus real-model staging, budgets/alerts, approver separation and manual-execution verification |
| Approval-gated Amazon writes | No-Go | Per-action executor verification, rollback and generic outcome records are incomplete |
| Autonomous refunds, payment reversals or cancellations | Permanently blocked | These actions must never become approvable |

## Verified implemented capabilities

| Company workflow | Status | Verified implementation and boundary |
|---|---|---|
| Tenant and operator access | Implemented and tested | OIDC/JWT, MFA, Membership/RBAC, tenant-scoped repositories and PostgreSQL RLS tests |
| Amazon connection and read sync | Implemented; externally unverified | LWA/token storage, order/Listing/FBA inventory reads, cursors and reconciliation; real seller evidence remains a staging gate |
| Cost onboarding | Implemented and tested | Tenant-scoped CSV/manual profiles, dry-run/idempotency, version history, source/freshness and data-quality reporting |
| SKU profit | Implemented and tested | Decimal calculations, currency checks, missing-data degradation, snapshots and break-even inputs |
| Inventory planning | Implemented and tested | Versioned policies, weighted demand, days of supply, reorder point and MOQ/case-pack recommendations |
| Daily priority triage | Implemented and tested | Deduplicated issues, impact ranking, recommendations, daily brief and SKU health dashboard |
| Profit-constrained pricing | Implemented and tested | Break-even, margin, freshness and stockout guardrails, action hash and HITL; no automatic Amazon price write |
| Listing quality experiments | Implemented and tested | Snapshot queue, structured issues, exact-change approval, manual execution record, before/after evaluation and keep/rollback decision workbench; Amazon writes remain disabled |
| Customer Case 360 | Implemented for controlled workflow | Intake, deterministic compliance checks, evidence-bound advisor, send validation and audit; real Buyer Messaging is unverified |
| Reconciliation | Implemented for read-only use | Difference records, bounded investigator and task drafts; finance/settlement depth remains limited |
| Bounded advisors | Implemented and tested | Persistent workflow, deterministic routing, schema/evidence validation, repair, idempotency, concurrent resume, budgets, metrics and audit |
| Observability | Instrumented; deployment incomplete | Metrics and alert recommendations exist; production collectors, dashboards, paging and retention need deployment evidence |

## Real company operating flow

1. Owner/admin connects one tenant and marketplace through company SSO.
2. Finance imports and validates costs; unresolved rows become data-quality work, never zero-valued inputs.
3. Sync reads Amazon facts and reconciliation records timestamps and differences.
4. Deterministic services calculate profit and inventory health.
5. The daily brief ranks loss, stockout, excess, Listing and data-quality issues.
6. Operators inspect evidence and create structured recommendations from the issue queue.
7. High-risk or degraded recommendations go to a separate approver and are bound to an action hash.
8. In the pilot, approved work is executed manually in Seller Central; Amazon writes remain disabled.
9. Reconciliation and experiment evaluation verify results and recommend rollback when guardrails worsen.

This must remain the primary product experience. Agent monitoring, raw task queues and mock consoles are engineering or administration surfaces.

## Launch blockers

### P0 — before committing or deploying

- Commit the production-foundation prerequisite before the bounded-advisor commit; the Agent runtime depends on its models, repositories, migrations, security and deployment wiring.
- Exclude the untracked root prompt artifact and all local/generated files.
- Review the seven critical Agent files listed in 'docs/CHANGESET_REVIEW.md' and record reviewer decisions.
- Keep Legacy customer/RAG demo routes unavailable in production; production users must use tenant-scoped Customer Case and Advisor workflows.
- Run the new Listing quality workbench through the company pilot and calibrate category-specific quality weights and conversion/return guardrails.

### P1 — before an internal read-only pilot

- Run migration and non-owner RLS validation in the target PostgreSQL topology.
- Validate one real Amazon sandbox seller in read-only mode, including observed time, freshness, resource IDs and verified-source labeling.
- Run every Advisor against the selected real model for normal output, repair, timeout, rejection, injection and missing evidence.
- Deploy telemetry storage, dashboards, alerts and redaction verification.
- Complete decisions for thresholds, SLAs, roles, freshness, retention and outage fallback.

### P2 — before approval-gated writes

- Add generic operational task, execution attempt and action outcome records across price, Listing and inventory.
- Prove one idempotent executor at a time with before/after snapshots, verification and rollback.
- Add tenant quotas and distributed Redis-backed rate limiting.
- Add advertising and returns/customer-voice inputs before claiming full profit or root-cause coverage.

## Not yet sufficient for production claims

- Advertising diagnosis, TACOS/search-term optimization and ad writes are not implemented.
- Returns/VOC aggregation and product-quality trend detection are not implemented.
- Generic action-outcome attribution is incomplete; Listing experiments only provide a local loop.
- Real SP-API, Buyer Messaging, provider JSON Schema support and alert delivery are not proven by repository tests.
- Portfolio views and mature enterprise reporting are incomplete.

## Low-value or misleading surfaces

- Legacy '/api/customer/*' and global '/api/rag/search' are development demonstrations; production routing excludes them.
- Mock consoles are training/E2E tools only and remain disabled in production.
- Agent/task telemetry is an admin view, not the daily operator destination.
- Mock, imported, estimated or cached values must never be described as verified Amazon facts.

## Current release judgement

**Conditional Go for a controlled company read-only pilot; No-Go for broad production or Amazon writes.**

Preserve the combined production foundation plus bounded-advisor architecture. Do not ship the Agent files alone: without the prerequisite domain, security, migration, tenant and workflow foundation, the Agent change is not independently deployable.
