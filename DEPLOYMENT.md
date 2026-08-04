# OmniSell Production Deployment

For the current go/no-go assessment, external gates, and company-wide rollout plan, see `docs/PRODUCTION_READINESS_AUDIT.md`.

This guide describes the current modular-monolith production stack. The legacy vLLM/Chroma/Mock-only deployment is retained only in `docker-compose.dev.yml`.

## Prerequisites

- Docker Engine with Compose v2, or an equivalent managed container platform.
- PostgreSQL 16 with tested backups; pgvector image is used by the included Compose stack.
- Redis 7 with authentication and persistence.
- OIDC provider with issuer, audience and JWKS endpoint.
- Amazon SP-API Draft application credentials for the pilot seller.
- A generated Fernet key for `SECRET_ENCRYPTION_KEY` and a random `AMAZON_NOTIFICATION_TOKEN`.

## Required production settings

Set these outside Git through the deployment platform's Secret Manager:

- `ENVIRONMENT=production`
- `AUTH_MODE=oidc`
- `OIDC_ISSUER`, `OIDC_AUDIENCE`, `OIDC_JWKS_URL`
- `DATABASE_URL` for the tenant-scoped application role
- `MIGRATION_DATABASE_URL` for a distinct schema-owner role
- `REDIS_URL`
- `TASK_BACKEND=celery`
- `AUTO_CREATE_SCHEMA=false`
- `SECRET_ENCRYPTION_KEY`
- `AMAZON_LWA_CLIENT_ID`, `AMAZON_LWA_CLIENT_SECRET`, `AMAZON_APPLICATION_ID`, `AMAZON_REDIRECT_URI`
- `AMAZON_NOTIFICATION_TOKEN`
- Explicit `ALLOWED_ORIGINS`
- `TELEMETRY_CAPTURE_LLM_CONTENT=false`

Create `frontend/.env.production` from `frontend/.env.production.example` and inject its OIDC client secret and Auth.js secret from the deployment secret manager. Create `.env.production` from `.env.example`, replace every placeholder, and keep both files outside Git with owner-only filesystem permissions.

Never use values from `.env.example` as production secrets.

The notification bridge must send `AMAZON_NOTIFICATION_TOKEN` in the `X-OmniSell-Notification-Token` header. Never place this secret in a callback URL, query string, task payload or log field.

## Deploy

```powershell
docker compose --env-file .env.production -f docker-compose.yml -f docker-compose.production.yml build
docker compose --env-file .env.production -f docker-compose.yml -f docker-compose.production.yml run --rm migrate
docker compose --env-file .env.production -f docker-compose.yml -f docker-compose.production.yml up -d
```

The same immutable image is used for API, workers, scheduler and migrations. Database changes must go through Alembic.

## Verification

```powershell
curl http://localhost:8080/api/admin/health
pytest -q
alembic current
```

After infrastructure checks, run a read-only Amazon pilot smoke test:

1. Generate an authorization URL from `POST /api/channels/amazon/authorize`.
2. Complete Seller Central consent and verify the GET callback stores an active connection.
3. Read one Listing Item, recent Orders and FBA inventory.
4. Confirm access/refresh tokens never appear in logs, audit records or task payloads.
5. Send a duplicate notification and verify Inbox deduplication.

## Release sequence

1. Deploy schema-compatible code with Amazon writes disabled.
2. Run migrations.
3. Enable read-only synchronization for the pilot tenant.
4. Enable low-risk automation after data reconciliation.
5. Enable approved Listing writes per tenant only after action-hash and rollback verification.

## Recovery and operations

See `docs/RUNBOOK.md`. Production targets are 99.9% monthly availability, RPO no greater than 15 minutes and RTO no greater than two hours.

## Known external verification

Real Amazon OAuth, Notifications and SP-API calls cannot be validated without Draft application credentials and a controlled seller account. Docker Compose execution also requires Docker to be installed on the verification host.

## OpenTelemetry and LLM observability

OmniSell emits FastAPI spans, Agent workflow/state transition spans, Policy Engine decisions, OpenAI-compatible model calls, token usage, and Amazon SP-API attempt metrics through OTLP/HTTP.

Required production variables:

```env
OTEL_SERVICE_NAME=omnisell-api
OTEL_EXPORTER_OTLP_ENDPOINT=http://otel-collector:4318
OTEL_EXPORTER_OTLP_HEADERS=
TELEMETRY_CAPTURE_LLM_CONTENT=false
```

`TELEMETRY_CAPTURE_LLM_CONTENT=true` records Prompt and Completion content on spans. Enable it only in a secured observability tenant with encryption, strict access control, retention limits, and PII redaction. Token counts, model names, latency, workflow transitions, Policy decisions, and Amazon status/retry metrics are recorded independently of content capture.

Recommended backend:

- Use an OpenTelemetry Collector as the only application egress target.
- Export infrastructure traces/metrics to Tempo/Jaeger plus Prometheus/Grafana.
- If LLM prompt inspection and evaluation are required, route OTLP traces to Langfuse from the collector rather than adding a second in-process SDK.
- Alert on `omnisell.policy.decisions{decision="block"}`, Amazon 401/429/5xx rates, Agent transitions ending in `failed`, and p95 `omnisell.agent.transition.duration`.
