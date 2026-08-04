# Production Runbook

## 1. Local Full E2E Stack

The local E2E stack uses real PostgreSQL, Redis and Celery components. A lightweight FastAPI service named `mock-cloud` simulates both OIDC and the Amazon Seller Central/LWA/SP-API endpoints. PostgreSQL encrypted storage is used as the local Secret Manager implementation, so LocalStack is not required.

### Services

| Service | Default URL/Port | Purpose |
| --- | --- | --- |
| OmniSell API | `http://localhost:8080` | Production-mode API using OIDC |
| Mock cloud | `http://localhost:9000` | OIDC, Amazon consent, LWA and SP-API |
| PostgreSQL | `localhost:5432` | Business data, tasks, approvals, audit and encrypted secrets |
| Redis | `localhost:6379` | Celery delivery, rate limiting and OAuth state replay protection |
| ChromaDB | `http://localhost:8001` | Existing RAG backend |
| Mock platform | `http://localhost:8002` | Existing legacy commerce test service |

### Prerequisites

- Docker Desktop or Docker Engine with Compose v2.
- Python 3.12+ on the host for the smoke script.
- Project dependencies installed with `pip install -r requirements.txt`.
- Ports 5432, 6379, 8001, 8002, 8080 and 9000 available, or overridden in `.env.e2e`.

### Start from a clean environment

```powershell
Copy-Item .env.e2e.example .env.e2e
docker compose --env-file .env.e2e -f docker-compose.e2e.yml down -v --remove-orphans
docker compose --env-file .env.e2e -f docker-compose.e2e.yml up -d --build
```

The `migrate` container runs `alembic upgrade head` and creates the `local-demo` tenant before the API starts.

Check readiness:

```powershell
docker compose --env-file .env.e2e -f docker-compose.e2e.yml ps
Invoke-RestMethod http://localhost:9000/health
Invoke-RestMethod http://localhost:8080/api/admin/health
```

### Run the complete automated smoke test

```powershell
python scripts/run_local_e2e.py
```

The smoke test proves all of the following:

1. Obtains an RS256 JWT from the local OIDC token endpoint.
2. Calls the protected OmniSell Amazon authorization API.
3. Follows the mock Seller Central consent redirect into the public OmniSell callback.
4. Exchanges the authorization code through the mock LWA endpoint.
5. Stores the Amazon refresh token as encrypted PostgreSQL ciphertext.
6. Reads mock Amazon orders, FBA inventory and one Listing Item through the real Amazon adapter.
7. Sends a duplicate Amazon notification and verifies Inbox deduplication.

A successful result resembles:

```json
{
  "status": "ok",
  "tenant_id": "local-demo",
  "orders": 2,
  "inventory_sku": "LOCAL-SKU-1001",
  "listing_asin": "B0LOCAL1001",
  "notification_deduplicated": true,
  "refresh_token_encrypted": true
}
```

### Manual OIDC and Amazon authorization

Get a JWT:

```powershell
$token = (Invoke-RestMethod -Method Post `
  -Uri http://localhost:9000/oidc/token `
  -ContentType 'application/x-www-form-urlencoded' `
  -Body 'grant_type=client_credentials&tenant_id=local-demo&subject=local-owner&role=owner&scope=read%20write%20admin').access_token
$headers = @{ Authorization = "Bearer $token" }
```

Create an authorization URL:

```powershell
$authorization = Invoke-RestMethod -Method Post `
  -Uri http://localhost:8080/api/channels/amazon/authorize `
  -Headers $headers `
  -ContentType 'application/json' `
  -Body '{"selling_partner_id":"A1LOCALMOCKSELLER","marketplace_ids":["ATVPDKIKX0DER"]}'
$authorization.authorization_url
```

Open the returned URL in a browser. The mock consent page immediately redirects to the OmniSell callback and returns the new `connection_id`.

### Logs and troubleshooting

```powershell
docker compose --env-file .env.e2e -f docker-compose.e2e.yml logs -f api mock-cloud
docker compose --env-file .env.e2e -f docker-compose.e2e.yml logs -f worker scheduler
docker compose --env-file .env.e2e -f docker-compose.e2e.yml logs -f postgres redis
```

Common failures:

- `401 Invalid or expired access token`: recreate the token; restarting `mock-cloud` generates a new signing key.
- `invalid_oauth_state`: OAuth state is single-use. Start authorization again instead of reloading the callback URL.
- Port conflict: edit `.env.e2e`, then pass it with `--env-file` to every Compose command.
- Stale schema or encrypted secrets: run `docker compose --env-file .env.e2e -f docker-compose.e2e.yml down -v` and restart.
- Ollama unavailable: Amazon/OIDC smoke remains usable. LLM-dependent customer and Agent routes require a local OpenAI-compatible endpoint at the configured `VLLM_BASE_URL`.

### Stop the stack

```powershell
docker compose --env-file .env.e2e -f docker-compose.e2e.yml down
```

Remove all local E2E data:

```powershell
docker compose --env-file .env.e2e -f docker-compose.e2e.yml down -v --remove-orphans
```

## 2. Production Deploy

1. Provision PostgreSQL 16, Redis 7, object storage and a managed secret service.
2. Populate production secrets outside Git. Set `AUTH_MODE=oidc`, `TASK_BACKEND=celery`, and `AUTO_CREATE_SCHEMA=false`.
3. Run `alembic upgrade head` as a one-off migration job.
4. Start API, worker and scheduler containers from the same immutable image.
5. Verify `/api/admin/health`, queue metrics, database connectivity and an Amazon read-only smoke test.

## 3. Rollback

- Roll back application images first; migrations must remain backward-compatible for at least one release.
- Disable Amazon write feature flags before rollback.
- Never downgrade a destructive migration during an incident; restore from a tested backup if required.

## 4. Alerts

- Amazon authorization failures or 401 spikes.
- SP-API 429/5xx rate, sync cursor lag over five minutes, duplicate event rate.
- Queue oldest-task age, dead-letter tasks, task success below 99%.
- API 5xx rate, PostgreSQL saturation, Redis unavailable, model timeout/error rate.

## 5. Recovery

- PostgreSQL: daily full backup plus continuous WAL archiving; quarterly restore exercise.
- Redis: queue delivery only; durable task state remains in PostgreSQL and can be replayed.
- Missed Amazon notifications: resume from stored sync cursor and run compensating polling.
- Compromised refresh token: revoke Amazon authorization, delete the secret reference, and require reauthorization.

Production backup commands default to `.env.production`, `docker-compose.yml` and `docker-compose.production.yml`:

```bash
BACKUP_DIR=/secure/backups POSTGRES_PASSWORD='...' ./scripts/backup_postgres.sh
RESTIC_REPOSITORY='s3:...' RESTIC_PASSWORD='...' POSTGRES_PASSWORD='...' ./scripts/backup_postgres_offsite.sh
RESTIC_REPOSITORY='s3:...' RESTIC_PASSWORD='...' POSTGRES_PASSWORD='...' RESTORE_TARGET=/secure/restore ./scripts/restore_postgres.sh
```

`restore_postgres.sh` restores into the isolated `omnisell_restore` database. Operators must validate row counts, Alembic revision, tenant isolation and application smoke tests before any controlled cutover.

## 6. Security

- Do not place PII, refresh tokens, access tokens or credentials in logs, prompts, vectors or task payloads.
- High-risk approvals must bind to the action hash. Changed parameters invalidate approval.
- Refund/payment reversal remains blocked in code and must be handled by a human outside Agent execution.
- Values in `.env.e2e.example` are local test credentials only and must never be reused in shared or production environments.
