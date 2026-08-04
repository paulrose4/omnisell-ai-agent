# Mock Production Pilot

This environment validates OmniSell's real control plane while replacing external identity and Amazon services with contract-compatible simulators.

## Real components

- PostgreSQL 16 with tenant RLS and a non-owner `omnisell_app` runtime role.
- Redis and Celery workers/scheduler.
- Durable tasks, Agent run state, HITL approvals and SQL audit records.
- Policy evaluation, Inbox deduplication, retry leases and Dead Letter state.
- Deterministic money, inventory and order status rules.
- Amazon write gate with read-only pilot defaults.

## Simulated components

- OIDC discovery, JWKS, signed access tokens and MFA `amr` claims.
- Amazon Seller authorization, LWA token exchange/refresh/revocation and read APIs.
- Amazon order, FBA inventory, Listing and notification payloads.

The FastAPI application uses the same OIDC JWT validation and `AmazonSPAPIAdapter` HTTP contract used by a future real integration. Switching to real services changes endpoints and credentials, not application services or domain rules.

## Start

Install and start Docker Desktop, then run:

```powershell
./scripts/start_mock_pilot.ps1
```

The script creates `.env.e2e` from the local-only example, starts the stack and executes the OAuth, read synchronization, notification deduplication and write-blocking smoke test.

## Database isolation

Migrations run as the PostgreSQL owner. API, Worker and Scheduler use the non-owner `omnisell_app` role. Authenticated requests and background jobs bind `app.current_tenant_id` before tenant-scoped repository access, allowing PostgreSQL RLS to reject cross-tenant reads and writes.

Do not configure API or Worker containers with `MIGRATION_DATABASE_URL` in production.

## Safety invariants

```env
AMAZON_WRITES_ENABLED=false
AMAZON_PILOT_READ_ONLY=true
OIDC_REQUIRE_MFA=true
AUTO_CREATE_SCHEMA=false
TASK_BACKEND=celery
```

Every Amazon SP-API request using a method other than GET, HEAD or OPTIONS passes through `AmazonWriteGate`. HITL approval does not override the read-only pilot gate.

## Stop

```powershell
docker compose --env-file .env.e2e -f docker-compose.e2e.yml down
```

Add `-v` only when you intentionally want to delete the local PostgreSQL, Redis and Chroma volumes.
