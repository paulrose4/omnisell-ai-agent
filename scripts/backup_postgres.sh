#!/usr/bin/env bash
set -euo pipefail
: "${BACKUP_DIR:?BACKUP_DIR is required}"
: "${POSTGRES_PASSWORD:?POSTGRES_PASSWORD is required}"
COMPOSE_ENV_FILE="${COMPOSE_ENV_FILE:-.env.production}"
COMPOSE_BASE_FILE="${COMPOSE_BASE_FILE:-docker-compose.yml}"
COMPOSE_OVERRIDE_FILE="${COMPOSE_OVERRIDE_FILE:-docker-compose.production.yml}"
compose=(docker compose --env-file "$COMPOSE_ENV_FILE" -f "$COMPOSE_BASE_FILE" -f "$COMPOSE_OVERRIDE_FILE")
mkdir -p "$BACKUP_DIR"
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
file="$BACKUP_DIR/omnisell-$stamp.dump"
partial="$file.partial"
trap 'rm -f "$partial"' EXIT
"${compose[@]}" exec -T -e PGPASSWORD="$POSTGRES_PASSWORD" postgres pg_dump -U omnisell -d omnisell -Fc > "$partial"
test -s "$partial"
mv "$partial" "$file"
sha256sum "$file" > "$file.sha256"
chmod 600 "$file" "$file.sha256"
find "$BACKUP_DIR" -type f -name 'omnisell-*.dump*' -mtime +30 -delete
printf 'backup=%s\n' "$file"
