#!/usr/bin/env bash
set -euo pipefail
: "${RESTIC_REPOSITORY:?RESTIC_REPOSITORY is required}"
: "${RESTIC_PASSWORD:?RESTIC_PASSWORD is required}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}"
: "${RESTORE_TARGET:?RESTORE_TARGET directory is required}"
COMPOSE_ENV_FILE="${COMPOSE_ENV_FILE:-.env.production}"
COMPOSE_BASE_FILE="${COMPOSE_BASE_FILE:-docker-compose.yml}"
COMPOSE_OVERRIDE_FILE="${COMPOSE_OVERRIDE_FILE:-docker-compose.production.yml}"
compose=(docker compose --env-file "$COMPOSE_ENV_FILE" -f "$COMPOSE_BASE_FILE" -f "$COMPOSE_OVERRIDE_FILE")
mkdir -p "$RESTORE_TARGET"; restic restore latest --tag omnisell-postgres --target "$RESTORE_TARGET"
dump="$(find "$RESTORE_TARGET" -name 'omnisell-*.dump' -type f | sort | tail -1)"
test -n "$dump"
checksum="$dump.sha256"; test -f "$checksum"; (cd "$(dirname "$dump")" && sha256sum -c "$(basename "$checksum")")
"${compose[@]}" exec -T -e PGPASSWORD="$POSTGRES_PASSWORD" postgres dropdb -U omnisell --if-exists omnisell_restore
"${compose[@]}" exec -T -e PGPASSWORD="$POSTGRES_PASSWORD" postgres createdb -U omnisell omnisell_restore
cat "$dump" | "${compose[@]}" exec -T -e PGPASSWORD="$POSTGRES_PASSWORD" postgres pg_restore -U omnisell -d omnisell_restore --clean --if-exists
