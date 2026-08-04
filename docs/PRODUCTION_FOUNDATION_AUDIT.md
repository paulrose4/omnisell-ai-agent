# Production Foundation Audit — 2026-07-17

## Decision

The current working tree is the intended direction for OmniSell as a real company operations system. The production foundation, business capability layer, bounded advisors, frontend and deployment belong to the same productization program, but they must not be committed as one undifferentiated change set.

The current version is a **Conditional Go for a controlled read-only company pilot**. It is not yet a broad-production or Amazon-write release.

## 1. Production foundation prerequisite

### Accept into the current version

The following are real prerequisites of the Agent runtime and should be committed before it:

- 'domain/' deterministic facts, money, inventory, issue, pricing and workflow models;
- 'infrastructure/database/' SQLAlchemy models, tenant-aware sessions and bootstrap;
- 'infrastructure/repositories/' tenant-scoped persistence, idempotency, audit and recovery records;
- 'safety/' Policy, HITL, audit, permanent refund/cancellation blocks and Amazon write gates;
- 'api/security.py', 'api/middleware.py' and 'api/config.py';
- Alembic schema prerequisites before the Agent revisions;
- PostgreSQL application role, Redis/Celery and telemetry wiring.

### Verified strengths

- Production configuration fails closed for OIDC, MFA, PostgreSQL, a separate migration role, Redis/Celery, encryption keys, nonzero model costs and disabled Mock mode.
- Business repositories consistently include tenant identifiers and tenant predicates; PostgreSQL RLS provides defense in depth.
- Celery jobs explicitly enter 'tenant_context' before repository access.
- Money-domain calculations use Decimal and currency codes; dangerous refund/payment/cancellation actions remain blocked.
- HITL prevents self-approval and requires two independent verifiers for critical requests.

### Fixes applied during this audit

- Request database and tool contexts now release in a FastAPI yield dependency instead of relying on task destruction.
- HITL price-risk thresholds now use exact Decimal arithmetic.
- Process-wide metrics and circuit-breaker administration now require owner/admin access.
- Circuit-breaker endpoints now receive their Request object instead of failing at runtime.
- High/critical RecommendedAction records cannot be accepted or completed through the generic operator PATCH endpoint.

### Blocking migration issue

'Alembic 0001' currently calls the live ORM 'Base.metadata.create_all()' and iterates the current 'TENANT_SCOPED_TABLES'. This makes historical revision 0001 change whenever the ORM changes. That is not an immutable production migration history.

Before the prerequisite commit, choose one of these paths:

1. If no shared or production database has ever used these revisions, regenerate/squash 0001–0014 into an explicit frozen baseline and keep 0015–0016 as Agent revisions.
2. If any persistent environment already uses them, do not rewrite history. Create a new corrective migration and document the existing baseline checksum and supported upgrade path.

Do not approve the prerequisite commit until this ownership/deployment-history decision is recorded.

## 2. Business capability extension

### Belongs to the same productization project

- profitability and cost ingestion;
- inventory health and replenishment recommendations;
- operational issues, priorities and recommended actions;
- profit-constrained pricing;
- Listing quality snapshots and experiments;
- Customer Case 360;
- reconciliation;
- Amazon OAuth, read sync, notifications and data quality.

These capabilities are the deterministic fact and workflow layer consumed by the bounded advisors. Removing them would leave the Agent runtime with no trustworthy evidence source and no realistic operator workflow.

### Separate optional scope

Mock drill routes, Golden Mock data, Mock Seller Central, Mock Buyer Messaging and local OIDC are valuable for training and E2E validation, but they are not production business features. Keep them in a separate optional commit and require 'MOCK_DRILL_ENABLED=false' in production.

### Remaining workflow gaps

- Listing quality and experiment APIs have no production frontend workbench.
- Advertising diagnosis and search-term workflows are absent.
- Returns/VOC aggregation is absent.
- Generic OperationalTask, ActionExecution and ActionOutcome records are not complete across price, inventory and Listing workflows.
- Real Seller Central manual execution evidence and Buyer Messaging integration remain external validation gates.

## 3. Frontend and deployment

### Include in the release, but separate from backend commits

The frontend is required for company adoption because operators need a daily cockpit, issue queue, cost import, inventory policies, approvals, customer cases and reconciliation. Deployment files are required to reproduce the runtime shape.

### Current deployment classification

- 'docker-compose.production.yml' is suitable for a controlled single-host pilot.
- The local PostgreSQL/Redis volumes and 'grafana/otel-lgtm' container are not a high-availability production architecture.
- Broad production requires managed or replicated PostgreSQL, tested point-in-time recovery, Redis HA, external durable telemetry, alert delivery and documented capacity/SLOs.
- Backup scripts create and verify dumps, but restore currently targets a separate validation database and does not implement an automated production cutover.
- Frontend OIDC uses a server-side proxy, but the Auth.js session currently carries the access token. Verify that '/api/auth/session' does not expose it to browser JavaScript before production; otherwise move backend token access to a server-only token retrieval path.

## 4. Explicitly excluded local files

Never stage:

- '.venv', 'node_modules', '.next', '__pycache__', '.pytest_cache';
- local SQLite databases and runtime logs;
- real '.env' files and credentials;
- generated model files and local temporary data;
- the root '提示词.txt' prompt backup.

The root prompt file is a local archive of earlier development prompts, not a product document. It is now explicitly ignored. If any excerpt becomes formal guidance, rewrite it into a named document under 'docs/' rather than committing the backup.

## Recommended commit series

Tests should travel with the code they prove; do not put all tests into a final commit that leaves earlier commits unverifiable.

1. **Production foundation core** — domain, database/session, core repositories, safety, auth/config, frozen migrations and their tests.
2. **Deterministic operations capabilities** — profitability, inventory, issues, pricing, Listing, customer cases, reconciliation, Amazon read sync and their tests.
3. **Bounded advisor runtime** — advisor contracts/runtime/workflows, Agent migrations 0015–0016, model gateway, evals and Agent tests.
4. **Listing execution lifecycle** — revision 0017 and the corresponding operator workflow/UI. Keep this after the Agent runtime commit because revision 0017 declares `down_revision = "0016"`.
5. **Frontend and deployment** — operator UI, BFF auth, Compose/Caddy, backup/restore and deployment validation.
6. **Mock drill and local E2E** — optional training/mock infrastructure, clearly excluded from production.
7. **Operational documentation** — runbooks, acceptance decisions and external staging evidence; documentation may be folded into the relevant commits where it explains behavior.

## Final acceptance statement

- Production foundation originally intended for this version: **Yes, with migration history correction required before commit.**
- Business capabilities part of the same productionization project: **Yes; Mock drill is separate optional scope.**
- Frontend and deployment delivered with the backend: **Yes, as a separate reviewed commit and initially classified as a single-host pilot.**
- Root prompt backup committed: **No.**
