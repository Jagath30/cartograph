#!/usr/bin/env bash
#
# NFR-27: one command, exits pass or fail. This is the green light every
# checkpoint is measured against (SDD section 10).
#
# Stages that do not exist yet print SKIP rather than being absent, so the
# script cannot look complete while checking a fraction of what it claims.
set -euo pipefail

cd "$(dirname "$0")/.."

API="http://127.0.0.1:8000/api/v1"
WEB="http://127.0.0.1:3000"

pass=0
fail=0
skip=0

stage()  { printf '\n== %s\n' "$1"; }
ok()     { printf '   PASS  %s\n' "$1"; pass=$((pass + 1)); }
bad()    { printf '   FAIL  %s\n' "$1"; fail=$((fail + 1)); }
pending(){ printf '   SKIP  %s\n' "$1"; skip=$((skip + 1)); }

# Command must succeed and its output must contain the wanted string.
expect() {
  local name="$1" want="$2"; shift 2
  local got
  if got="$("$@" 2>/dev/null)" && [[ "$got" == *"$want"* ]]; then
    ok "$name"
  else
    bad "$name -- wanted '$want', got '${got:-<nothing>}'"
  fi
}

# Command must FAIL. Used for the isolation checks, where success is the bug.
refuse() {
  local name="$1"; shift
  if "$@" >/dev/null 2>&1; then
    bad "$name -- the operation succeeded and should not have"
  else
    ok "$name"
  fi
}

pg() {
  docker compose exec -T postgres sh -c "$1"
}

# One value from the warehouse, read as the SELECT-only role.
wh_ro() {
  pg "PGPASSWORD=\"\$WAREHOUSE_RO_PASSWORD\" psql -X -At -U \"\$WAREHOUSE_RO_USER\" -d \"\$WAREHOUSE_DB_NAME\" -h 127.0.0.1 -c \"$1\""
}

# Exact match, where expect's substring match would let 124 pass for 24.
equal() {
  local name="$1" want="$2" got="$3"
  if [[ "$got" == "$want" ]]; then
    ok "$name"
  else
    bad "$name -- wanted '$want', got '${got:-<nothing>}'"
  fi
}

# A string already in hand must contain the wanted one. For several checks
# against the output of one command, where expect would run it each time.
contains() {
  local name="$1" want="$2" got="$3"
  if [[ "$got" == *"$want"* ]]; then
    ok "$name"
  else
    bad "$name -- wanted '$want', got '${got:-<nothing>}'"
  fi
}

stage "Environment"
./scripts/bootstrap.sh >/dev/null
if [[ -f .env ]]; then ok ".env present"; else bad ".env missing"; fi

stage "Stack"
docker compose up -d --wait --wait-timeout 180
ok "all services report healthy"

stage "Data services"
expect "postgres accepting connections" "accepting" \
  pg 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
expect "redis responds to ping" "PONG" \
  docker compose exec -T redis redis-cli ping
expect "pgvector installed in the app database" "vector" \
  pg 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Atc "select extname from pg_extension where extname = '"'"'vector'"'"'"'

stage "Warehouse isolation (NFR-07)"
refuse "read-only role cannot create a table" \
  pg 'PGPASSWORD="$WAREHOUSE_RO_PASSWORD" psql -v ON_ERROR_STOP=1 -U "$WAREHOUSE_RO_USER" -d "$WAREHOUSE_DB_NAME" -h 127.0.0.1 -c "create table smoke_should_not_exist (i int)"'
refuse "read-only role cannot reach the app database" \
  pg 'PGPASSWORD="$WAREHOUSE_RO_PASSWORD" psql -U "$WAREHOUSE_RO_USER" -d "$POSTGRES_DB" -h 127.0.0.1 -Atc "select 1"'

# A clean clone starts with an empty warehouse, and loading one takes minutes
# and the duckdb CLI -- so an empty warehouse is a SKIP, and a loaded one is
# held to the overlay generated from tpcds_ri.sql.
stage "Warehouse (DR-01, DR-02)"
wh_tables="$(wh_ro "select count(*) from pg_tables where schemaname = 'public'" 2>/dev/null || true)"
if [[ "$wh_tables" == "0" ]]; then
  pending "warehouse is empty -- ./scripts/warehouse.sh loads it"
else
  overlay_fks="$(grep -c '^  - from:' backend/overlays/tpcds.yaml)"
  equal "24 tables" "24" "$wh_tables"
  equal "17 primary keys in the catalog" "17" \
    "$(wh_ro "select count(*) from pg_constraint where contype = 'p' and connamespace = 'public'::regnamespace" 2>/dev/null || true)"
  equal "catalog declares the overlay's $overlay_fks foreign keys" "$overlay_fks" \
    "$(wh_ro "select count(*) from pg_constraint where contype = 'f' and connamespace = 'public'::regnamespace" 2>/dev/null || true)"
fi

stage "Backend"
expect "liveness" '"status":"ok"' curl -sf "$API/health"
expect "readiness reports ready" '"status":"ready"' curl -sf "$API/ready"

stage "Frontend"
expect "page served and titled" "Cartograph" curl -sf "$WEB"

stage "Tests"
if docker compose exec -T backend pytest -q >/dev/null 2>&1; then
  ok "backend suite"
else
  bad "backend suite"
fi

# The application's own reading of the warehouse: SchemaIngestor as the
# SELECT-only role, then GraphBuilder. "N catalog, 0 overlay" is the check
# that matters -- an ingestor that saw no constraints would still report N
# foreign keys, every one of them supplied by the overlay.
stage "Schema ingestion (FR-02, FR-03, FR-43, DD-08)"
if [[ "$wh_tables" == "0" ]]; then
  pending "warehouse is empty -- nothing to ingest"
else
  ingested="$(docker compose exec -T backend python -m app.show_schema 2>/dev/null || true)"
  contains "snapshot read from the catalog, keys included" \
    "24 tables, 425 columns, 17 primary keys, $overlay_fks foreign keys ($overlay_fks catalog, 0 overlay)" "$ingested"
  contains "graph built with $overlay_fks foreign key edges" \
    "of which $overlay_fks foreign key edges" "$ingested"
  contains "naming overlay applied to the descriptions" \
    " — " "$(grep '^naming' <<<"$ingested" || true)"
fi

# The ambiguity the project exists to surface, on the live graph. A tie must
# warn and must name the route it did not take; a strictly shorter route
# must not warn at all. A warning that always fires, or never, fails here.
stage "Path finding (FR-11 to FR-14, FR-40, DD-21)"
if [[ "$wh_tables" == "0" ]]; then
  pending "warehouse is empty -- no paths to find"
else
  tie="$(docker compose exec -T backend python -m app.show_paths catalog_sales customer_address 2>/dev/null || true)"
  contains "billing against shipping address is reported as an arbitrary choice" \
    "WARNING   arbitrary_choice" "$tie"
  contains "the route not taken is named in the warning" \
    "Equally valid: Each catalog sales row has one customer address, through its ship address" "$tie"
  quiet="$(docker compose exec -T backend python -m app.show_paths store_sales customer_address 2>/dev/null || true)"
  contains "a strictly shorter route is chosen" "the shortest was used" "$quiet"
  if [[ "$quiet" == *"WARNING"* ]]; then
    bad "a strictly shorter route does not warn -- it did"
  else
    ok "a strictly shorter route does not warn"
  fi
fi

stage "Not yet verified"
pending "one known question runs the full pipeline (arrives at step 7)"
pending "generated SQL joins along the reported path (arrives at step 7)"

printf '\n== Result\n'
printf '   %d passed, %d failed, %d skipped\n' "$pass" "$fail" "$skip"

if (( fail > 0 )); then
  printf '   SMOKE FAIL\n'
  exit 1
fi
printf '   SMOKE PASS\n'
