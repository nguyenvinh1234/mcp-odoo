#!/bin/sh
set -eu

case "${RESTORE_RUN_ID:-}" in
  ""|*[!A-Za-z0-9_]*)
    echo "RESTORE_RUN_ID must contain only letters, digits, and underscores" >&2
    exit 2
    ;;
esac

ODOO_DB="ngv_restore_${RESTORE_RUN_ID}"
KEYCLOAK_DB="keycloak_restore_${RESTORE_RUN_ID}"
export PGCONNECT_TIMEOUT=5

wait_for_db() {
  local host="$1" user="$2" password_file="$3"
  export PGPASSWORD="$(cat "$password_file")"
  for _ in $(seq 1 60); do
    if pg_isready -h "$host" -U "$user" -d postgres >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  echo "restore verification database did not become ready" >&2
  return 1
}

restore_once() {
  local host="$1" user="$2" password_file="$3" database="$4" dump="$5"
  export PGPASSWORD="$(cat "$password_file")"
  if ! psql -w -h "$host" -U "$user" -d postgres -Atc \
    "select 1 from pg_database where datname='${database}'" | grep -qx 1; then
    createdb -w -h "$host" -U "$user" "$database"
    pg_restore -w --exit-on-error --no-owner --no-privileges \
      -h "$host" -U "$user" -d "$database" "$dump"
  fi
}

wait_for_db restore-odoo-db odoo /run/secrets/postgres_password
wait_for_db restore-keycloak-db keycloak /run/secrets/keycloak_db_password
restore_once restore-odoo-db odoo /run/secrets/postgres_password \
  "$ODOO_DB" /backups/odoo.dump
restore_once restore-keycloak-db keycloak /run/secrets/keycloak_db_password \
  "$KEYCLOAK_DB" /backups/keycloak.dump

export PGPASSWORD="$(cat /run/secrets/postgres_password)"
ODOO_TABLES="$(psql -w -h restore-odoo-db -U odoo -d "$ODOO_DB" -Atc \
  "select count(*) from pg_tables where schemaname='public'")"
ODOO_REGISTRY="$(psql -w -h restore-odoo-db -U odoo -d "$ODOO_DB" -Atc \
  "select count(*) from ir_module_module where state='installed'")"

export PGPASSWORD="$(cat /run/secrets/keycloak_db_password)"
KEYCLOAK_TABLES="$(psql -w -h restore-keycloak-db -U keycloak -d "$KEYCLOAK_DB" -Atc \
  "select count(*) from pg_tables where schemaname='public'")"
KEYCLOAK_REALMS="$(psql -w -h restore-keycloak-db -U keycloak -d "$KEYCLOAK_DB" -Atc \
  "select count(*) from realm")"

test "$ODOO_TABLES" -gt 0
test "$ODOO_REGISTRY" -gt 0
test "$KEYCLOAK_TABLES" -gt 0
test "$KEYCLOAK_REALMS" -gt 0

printf '{"restore_verify":"PASS","run_id":"%s","odoo_tables":%s,"odoo_installed_modules":%s,"keycloak_tables":%s,"keycloak_realms":%s}\n' \
  "$RESTORE_RUN_ID" "$ODOO_TABLES" "$ODOO_REGISTRY" "$KEYCLOAK_TABLES" "$KEYCLOAK_REALMS"
