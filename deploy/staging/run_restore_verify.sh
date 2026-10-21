#!/bin/sh
set -eu

cd "$(dirname "$0")/../.."
RESTORE_RUN_ID="${RESTORE_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)}"
RESTORE_RUN_ID="$(printf '%s' "$RESTORE_RUN_ID" | tr -cd 'A-Za-z0-9_')"
test -n "$RESTORE_RUN_ID"
export RESTORE_RUN_ID

dc() {
  docker compose \
    -p ngv-odoo-mcp-restore-verify \
    --env-file deploy/staging/.env \
    -f deploy/staging/restore-verify.yml \
    "$@"
}

stop_restore_databases() {
  dc stop restore-odoo-db restore-keycloak-db >/dev/null 2>&1 || true
}

trap stop_restore_databases EXIT
dc up -d restore-odoo-db restore-keycloak-db
dc up --no-deps --abort-on-container-exit \
  --exit-code-from restore-verify restore-verify
