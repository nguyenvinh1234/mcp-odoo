#!/bin/sh
set -eu

cd "$(dirname "$0")/../.."

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
