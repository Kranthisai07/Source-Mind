#!/usr/bin/env bash
set -Eeuo pipefail

: "${SECURITY_TEST_OWNER_PASSWORD:?set SECURITY_TEST_OWNER_PASSWORD}"
: "${SECURITY_TEST_RUNTIME_PASSWORD:?set SECURITY_TEST_RUNTIME_PASSWORD}"

init_sql="${SECURITY_TEST_INIT_SQL:-/docker-entrypoint-initdb.d/02-security-test.sql.in}"

psql --set=ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --set=owner_password="$SECURITY_TEST_OWNER_PASSWORD" \
  --set=runtime_password="$SECURITY_TEST_RUNTIME_PASSWORD" \
  --file "$init_sql"
