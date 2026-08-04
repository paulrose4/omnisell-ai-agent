#!/usr/bin/env bash
set -euo pipefail
: "${RESTIC_REPOSITORY:?RESTIC_REPOSITORY is required}"
: "${RESTIC_PASSWORD:?RESTIC_PASSWORD is required}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}"
COMPOSE_ENV_FILE="${COMPOSE_ENV_FILE:-.env.production}"
COMPOSE_BASE_FILE="${COMPOSE_BASE_FILE:-docker-compose.yml}"
COMPOSE_OVERRIDE_FILE="${COMPOSE_OVERRIDE_FILE:-docker-compose.production.yml}"
compose=(docker compose --env-file "$COMPOSE_ENV_FILE" -f "$COMPOSE_BASE_FILE" -f "$COMPOSE_OVERRIDE_FILE")
work="$(mktemp -d)"; trap 'rm -rf "$work"' EXIT
stamp="$(date -u +%Y%m%dT%H%M%SZ)"; dump="$work/omnisell-$stamp.dump"
"${compose[@]}" exec -T -e PGPASSWORD="$POSTGRES_PASSWORD" postgres pg_dump -U omnisell -d omnisell -Fc > "$dump"
test -s "$dump"
sha256sum "$dump" > "$dump.sha256"
chmod 600 "$dump" "$dump.sha256"
restic backup "$dump" "$dump.sha256" --tag omnisell-postgres
restic forget --tag omnisell-postgres --keep-daily 7 --keep-weekly 4 --keep-monthly 3 --prune
restic check --read-data-subset=1/20
