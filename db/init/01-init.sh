#!/bin/bash
set -euo pipefail

# pgvector belongs to the application store only (DD-17), never to the
# warehouse under query.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
CREATE EXTENSION IF NOT EXISTS vector;
REVOKE CONNECT ON DATABASE "$POSTGRES_DB" FROM PUBLIC;
SQL

# Second database and the SELECT-only role (DD-03, NFR-07).
# statement_timeout on the role satisfies NFR-11 at the connection, not in code.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
CREATE ROLE "$WAREHOUSE_RO_USER" WITH LOGIN PASSWORD '$WAREHOUSE_RO_PASSWORD';
ALTER ROLE "$WAREHOUSE_RO_USER" SET statement_timeout = '30s';
ALTER ROLE "$WAREHOUSE_RO_USER" SET default_transaction_read_only = on;
CREATE DATABASE "$WAREHOUSE_DB_NAME" OWNER "$POSTGRES_USER";
SQL

# Privileges inside the warehouse. No tables exist yet — step 2 loads them —
# so ALTER DEFAULT PRIVILEGES is what actually does the work.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$WAREHOUSE_DB_NAME" <<SQL
GRANT CONNECT ON DATABASE "$WAREHOUSE_DB_NAME" TO "$WAREHOUSE_RO_USER";
REVOKE CONNECT ON DATABASE "$WAREHOUSE_DB_NAME" FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO "$WAREHOUSE_RO_USER";
GRANT SELECT ON ALL TABLES IN SCHEMA public TO "$WAREHOUSE_RO_USER";
ALTER DEFAULT PRIVILEGES FOR ROLE "$POSTGRES_USER" IN SCHEMA public
  GRANT SELECT ON TABLES TO "$WAREHOUSE_RO_USER";
SQL

echo "cartograph init: app=$POSTGRES_DB warehouse=$WAREHOUSE_DB_NAME ro=$WAREHOUSE_RO_USER"
