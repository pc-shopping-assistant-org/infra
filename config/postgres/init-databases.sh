#!/usr/bin/env bash
set -euo pipefail

IFS=',' read -ra databases <<< "$POSTGRES_MULTIPLE_DATABASES"
for database in "${databases[@]}"; do
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
    --set=database="$database" <<'SQL'
SELECT format('CREATE DATABASE %I', :'database')
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = :'database')\gexec
SQL
done
