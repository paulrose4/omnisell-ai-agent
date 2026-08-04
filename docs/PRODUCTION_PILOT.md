# Production Pilot Go-Live

## Deployment

1. Provision Ubuntu 24.04 with 8 vCPU, 32 GB RAM and encrypted storage.
2. Configure root-readable `.env.production` and `frontend/.env.production`; never reuse example secrets.
3. Set `AUTH_MODE=oidc`, `TASK_BACKEND=celery`, `AUTO_CREATE_SCHEMA=false`, `AMAZON_WRITES_ENABLED=false`, and `AMAZON_PILOT_READ_ONLY=true`.
4. Run `docker compose -f docker-compose.yml -f docker-compose.production.yml --env-file .env.production up -d --build`.
5. Provision each operator with `python scripts/provision_operator.py --tenant-id ... --oidc-subject ... --email ... --display-name ... --role ...`.
6. Verify Amazon connection health, execute a manual read sync and compare sampled orders/inventory with Seller Central.

## Mandatory gates

- External OIDC enforces MFA.
- No API key or hybrid authentication in production.
- SQL-backed HITL and audit survive API restart.
- Amazon Write Gate returns `blocked_read_only_pilot`.
- Orders, inventory and Listings persist idempotently and expose sync cursor health.
- Encrypted PostgreSQL backup is copied off-host and restored successfully before pilot start.
- Operators use Seller Central for all final platform writes.

## Pilot rollback

Stop Scheduler and Worker first, then API. Operators immediately return to Seller Central. Restore PostgreSQL on a replacement host, start the immutable application image, and resume sync from persisted cursors. Do not run destructive Alembic downgrade during an incident.
## Go-live completion controls

- Cost CSV import and inventory policies are available in the OIDC-protected operations console.
- Failed and dead-letter tasks can only be replayed or manually closed by Owner/Admin; replay creates a new immutable task.
- Amazon Inbox events use leases, retry backoff and deterministic notification-to-sync routing. Unknown types enter notification dead letter and data-quality reporting.
- Daily reconciliation compares 30-day order IDs, FBA quantities and persisted Listing state; differences create `amazon_reconciliation_mismatch` issues.
- Production OIDC requires an accepted `amr` MFA value or the configured `acr` value.
- PostgreSQL RLS is exercised in CI against PostgreSQL 16 with a non-owner application role.
- Offsite backups use Restic encryption and retention scripts in `scripts/backup_postgres_offsite.sh`; restore drills use `scripts/restore_postgres.sh`.