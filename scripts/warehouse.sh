#!/usr/bin/env bash
#
# DR-06: rebuild the TPC-DS warehouse from nothing, with one command.
#
#     ./scripts/warehouse.sh          scale factor 1, the local target
#     ./scripts/warehouse.sh 0.01     the thirty-second dry run
#
# The graph is the same at every scale factor -- same tables, same keys,
# same edges. Only the row volume changes, so the small run proves the whole
# mechanism before the large one is paid for.
#
# Needs on the host: docker, the duckdb CLI, python3 (standard library only),
# and curl the first time, to fetch tpcds_ri.sql.
#
# Order matters and is deliberate (DD-16, T-09):
#   0. tpcds_ri.sql fetched if absent, and its sha256 checked either way.
#      Nothing below runs on a file that is missing or is not the pinned one.
#   1. tpcds_ri.sql -> the overlay. The graph is correct from here on.
#   2. generate, export, create, COPY, check row counts.
#   3. primary keys on the referenced tables; a foreign key needs a unique target.
#   4. tpcds_ri.sql itself, applied as real constraints. Optional to the
#      mechanism: a refusal is reported, not fatal.
#
# Destroys and recreates the warehouse tables. Never touches the app database.
set -euo pipefail

cd "$(dirname "$0")/.."

SF="${1:-1}"
if ! [[ "$SF" =~ ^[0-9]+(\.[0-9]+)?$ ]]; then
  echo "warehouse: scale factor must be a number, got '$SF'" >&2
  exit 2
fi

RI=backend/warehouse/tpcds_ri.sql
SCHEMA=backend/warehouse/tpcds_schema.sql
OVERLAY=backend/overlays/tpcds.yaml
DATA="data/tpcds/sf$SF"

for tool in docker duckdb python3; do
  command -v "$tool" >/dev/null 2>&1 || { echo "warehouse: $tool not found on PATH" >&2; exit 1; }
done

step() { printf '\n== %s\n' "$1"; }

# psql inside the postgres container, as the warehouse owner. stdin passes
# through, which is how the CSVs reach COPY without a volume mount.
wh() {
  docker compose exec -T postgres sh -c \
    'PGOPTIONS="-c client_min_messages=warning" exec psql -X -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$WAREHOUSE_DB_NAME" "$@"' sh "$@"
}

# Same, but one failed statement does not stop the ones after it.
wh_lenient() {
  docker compose exec -T postgres sh -c \
    'exec psql -X -q -U "$POSTGRES_USER" -d "$WAREHOUSE_DB_NAME" "$@"' sh "$@"
}

mapfile -t TABLES < <(sed -n 's/^create table \([a-z_]*\) (.*/\1/p' "$SCHEMA")

# The TPC's file is not in the repository. Fetch it when absent; verify it
# always. A failed download or a wrong hash stops the build here, before the
# overlay or the database is touched.
step "Source (tpcds_ri.sql)"
read -r RI_URL RI_SHA256 < <(python3 backend/warehouse/ri.py source)
sha_of() { sha256sum "$1" | cut -d' ' -f1; }
if [[ ! -f "$RI" ]]; then
  command -v curl >/dev/null 2>&1 || { echo "warehouse: $RI is absent and curl is not on PATH to fetch it" >&2; exit 1; }
  fetched="$(mktemp)"
  trap 'rm -f "$fetched"' EXIT
  if ! curl -fsSL --retry 2 -o "$fetched" "$RI_URL"; then
    echo "warehouse: could not download tpcds_ri.sql from $RI_URL" >&2
    echo "warehouse: nothing was changed. The committed overlay still holds the edge list." >&2
    exit 1
  fi
  if [[ "$(sha_of "$fetched")" != "$RI_SHA256" ]]; then
    echo "warehouse: the downloaded tpcds_ri.sql is not the pinned file" >&2
    echo "warehouse:   expected sha256 $RI_SHA256" >&2
    echo "warehouse:   received sha256 $(sha_of "$fetched")" >&2
    echo "warehouse: nothing was changed and the download was discarded." >&2
    exit 1
  fi
  mv "$fetched" "$RI"
  chmod 644 "$RI"
  echo "   fetched from the pinned mirror"
fi
if [[ "$(sha_of "$RI")" != "$RI_SHA256" ]]; then
  echo "warehouse: $RI is not the pinned file" >&2
  echo "warehouse:   expected sha256 $RI_SHA256" >&2
  echo "warehouse:   found    sha256 $(sha_of "$RI")" >&2
  echo "warehouse: nothing was changed. Delete the file to fetch it again." >&2
  exit 1
fi
echo "   sha256 verified"

# Rendered to one side and moved into place, so a failure cannot leave a
# truncated overlay where the committed one was.
step "Overlay (primary source of the graph)"
python3 backend/warehouse/ri.py overlay > "$OVERLAY.new"
expected_fks="$(grep -c '^  - from:' "$OVERLAY.new" || true)"
if (( expected_fks == 0 )); then
  rm -f "$OVERLAY.new"
  echo "warehouse: the overlay came out with no relationships; refusing to continue" >&2
  exit 1
fi
mv "$OVERLAY.new" "$OVERLAY"
echo "   $expected_fks relationships written to $OVERLAY"

step "Stack"
./scripts/bootstrap.sh >/dev/null
docker compose up -d --wait postgres
echo "   postgres healthy"

step "Generate (DuckDB tpcds, scale factor $SF)"
rm -rf "$DATA"
mkdir -p "$DATA"
counts_sql=""
for t in "${TABLES[@]}"; do
  counts_sql+="${counts_sql:+ union all }select '$t', count(*) from $t"
done
duckdb "$DATA/gen.duckdb" >/dev/null <<SQL
INSTALL tpcds;
LOAD tpcds;
CALL dsdgen(sf = $SF);
EXPORT DATABASE '$DATA' (FORMAT CSV, HEADER true);
COPY ($counts_sql) TO '$DATA/rowcounts.txt' (FORMAT CSV, HEADER false, DELIMITER ' ');
SQL
rm -f "$DATA/gen.duckdb" "$DATA/gen.duckdb.wal"
echo "   ${#TABLES[@]} tables exported to $DATA ($(du -sh "$DATA" | cut -f1))"

step "Create tables"
for t in "${TABLES[@]}"; do
  echo "drop table if exists $t cascade;"
done | wh
wh < "$SCHEMA"
echo "   ${#TABLES[@]} tables created in the warehouse database"

step "Load (COPY)"
for t in "${TABLES[@]}"; do
  wh -c "copy $t from stdin (format csv, header match)" < "$DATA/$t.csv"
done
wh -c "analyze"

mismatches=0
while read -r t want; do
  got="$(wh -Atc "select count(*) from $t")"
  if [[ "$got" != "$want" ]]; then
    printf '   MISMATCH  %-24s duckdb %s, postgres %s\n' "$t" "$want" "$got"
    mismatches=$((mismatches + 1))
  fi
done < "$DATA/rowcounts.txt"
total_rows="$(awk '{s += $2} END {print s}' "$DATA/rowcounts.txt")"
if (( mismatches > 0 )); then
  echo "warehouse: $mismatches tables do not match what was generated" >&2
  exit 1
fi
echo "   ${#TABLES[@]} tables, $total_rows rows, every count matches DuckDB"

step "Primary keys (derived from the foreign key targets)"
python3 backend/warehouse/ri.py primary-keys | wh
pks="$(wh -Atc "select count(*) from pg_constraint where contype = 'p' and connamespace = 'public'::regnamespace")"
echo "   $pks primary keys"

step "Foreign keys (tpcds_ri.sql applied as real constraints)"
wh_lenient < "$RI" 2> "$DATA/constraints.log" || true
fks="$(wh -Atc "select count(*) from pg_constraint where contype = 'f' and connamespace = 'public'::regnamespace")"
echo "   $fks of $expected_fks applied"
if [[ "$fks" != "$expected_fks" ]]; then
  echo "   refused (T-09) -- the overlay still carries all $expected_fks; details in $DATA/constraints.log:"
  grep -E '^(psql:.*)?ERROR' "$DATA/constraints.log" | sed 's/^/     /' || true
fi

step "Size"
echo "   $(wh -Atc "select pg_size_pretty(pg_database_size(current_database()))")  ($(wh -Atc "select pg_database_size(current_database())") bytes)"

printf '\n== Result\n   WAREHOUSE READY  sf=%s  %s tables  %s rows  %s primary keys  %s/%s foreign keys  %ss\n' \
  "$SF" "${#TABLES[@]}" "$total_rows" "$pks" "$fks" "$expected_fks" "$SECONDS"
