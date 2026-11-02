#!/usr/bin/env bash
#
# Step 12: Cartograph's databases on Neon, from the laptop, one step at a
# time. Each step is its own command and is never retried: a failure stops
# and is reported.
#
#     ./scripts/neon.sh roles       the two roles, their passwords and URLs   (prints nothing)
#     ./scripts/neon.sh databases   warehouse and app, vector, the grants     (output shown)
#     ./scripts/neon.sh revoke-temp no temporary tables in the warehouse for PUBLIC (run once after databases)
#     ./scripts/neon.sh whoami      each new URL: current_user, current_database()
#     ./scripts/neon.sh load        the sf=0.25 warehouse, from the measurement database
#     ./scripts/neon.sh app         migrations to head, then the three schema tables copied
#     ./scripts/neon.sh check       sizes, row counts, the hash, the examples, the refusals
#     ./scripts/neon.sh accounts    the smoke accounts, counted: a dry run (step 14, item 111)
#     ./scripts/neon.sh accounts --yes   deletes them: ONLY after the owner's ruling on the counts
#     ./scripts/neon.sh declared    the five hand-declared relationships measured, read-only, as the reader (item 41)
#
# THE SECRETS (the owner's ruling 1 at the plan stop). .env.deploy is never
# opened, read or printed here: it reaches a container only through
# `docker run --env-file`, and inside it only through the environment.
# `roles` makes the two passwords inside its container, 40 letters and
# digits, and writes them and the two URLs Render is given into the
# mounted .env.deploy itself; every byte the container writes to stdout or
# stderr is discarded, because a failed statement can echo its line. Its
# result is its exit status, and `whoami` afterwards.
#
# THE OWNER'S URL (Neon's own role, database neondb, direct) never leaves
# this laptop. The other databases are reached by replacing /neondb? in it.
#
# DEPLOY_ENV_FILE and DEPLOY_NETWORK exist for the rehearsal against a
# local stand-in; for Neon both stay at their defaults.
set -euo pipefail

cd "$(dirname "$0")/.."

ENV_FILE="${DEPLOY_ENV_FILE:-.env.deploy}"
NETWORK="${DEPLOY_NETWORK:-bridge}"
# psql, pg_dump and pg_restore 17, from an image already on the laptop.
PG_IMAGE=pgvector/pgvector:pg17
APP_ROLE=cartograph_app
READER=cartograph_reader
MEASURE=(docker compose -p cartograph-measure -f docker-compose.measure.yml)

[[ -f "$ENV_FILE" ]] || { echo "neon: $ENV_FILE is missing" >&2; exit 2; }
# Absolute, for the bind mount: Docker makes a directory where a mounted
# path does not exist.
ENV_FILE="$(realpath "$ENV_FILE")"

# A container with the secrets in its environment and nothing else of them.
# The URL helpers run inside it, so no secret is on this command line.
in_pg() {
  docker run --rm -i --network "$NETWORK" --env-file "$ENV_FILE" "$PG_IMAGE" sh -c '
    set -eu
    case "$NEON_OWNER_URL" in */neondb\?*) ;; *) echo "neon: the owner URL does not name /neondb?" >&2; exit 2 ;; esac
    owner_on() { printf %s "$NEON_OWNER_URL" | sed "s#/neondb?#/$1?#"; }
    '"$1"
}

case "${1:-}" in

roles)
  # Refuses to run twice: passwords already written are never replaced.
  docker run --rm --network "$NETWORK" --env-file "$ENV_FILE" \
    -v "$ENV_FILE:/deploy/env" "$PG_IMAGE" sh -c '
    set -eu
    [ -z "${APP_ROLE_PASSWORD:-}" ] && [ -z "${READER_PASSWORD:-}" ] || exit 3
    case "$NEON_OWNER_URL" in */neondb\?*) ;; *) exit 2 ;; esac
    make() { tr -dc "A-Za-z0-9" < /dev/urandom | head -c 40; }
    APP_ROLE_PASSWORD=$(make); READER_PASSWORD=$(make)
    [ ${#APP_ROLE_PASSWORD} -eq 40 ] && [ ${#READER_PASSWORD} -eq 40 ] || exit 4
    rest=${NEON_OWNER_URL#*@}; host=${rest%%/*}; query=${rest#*\?}
    # The file first, so a password that reaches Neon is never lost.
    grep -v -E "^(APP_ROLE_PASSWORD|READER_PASSWORD|APP_DATABASE_URL|WAREHOUSE_DATABASE_URL)=" /deploy/env > /tmp/env
    {
      echo "APP_ROLE_PASSWORD=$APP_ROLE_PASSWORD"
      echo "READER_PASSWORD=$READER_PASSWORD"
      echo "APP_DATABASE_URL=postgresql://'"$APP_ROLE"':$APP_ROLE_PASSWORD@$host/app?$query"
      echo "WAREHOUSE_DATABASE_URL=postgresql://'"$READER"':$READER_PASSWORD@$host/warehouse?$query"
    } >> /tmp/env
    [ "$(grep -c "^NEON_OWNER_URL=" /tmp/env)" = 1 ] || exit 5
    # Written in place: the file is a bind mount, which a rename would break.
    cat /tmp/env > /deploy/env
    export APP_ROLE_PASSWORD READER_PASSWORD
    psql -X -q -v ON_ERROR_STOP=1 -d "$NEON_OWNER_URL" <<SQL
\getenv app_pw APP_ROLE_PASSWORD
\getenv reader_pw READER_PASSWORD
create role '"$APP_ROLE"' login password :'"'"'app_pw'"'"';
create role '"$READER"' login password :'"'"'reader_pw'"'"';
-- NFR-11 at the connection, and read-only at the role (NFR-07), as locally.
alter role '"$READER"' set statement_timeout = '"'"'30s'"'"';
alter role '"$READER"' set default_transaction_read_only = on;
SQL
  ' > /dev/null 2>&1
  ;;

databases)
  in_pg '
    psql -X -v ON_ERROR_STOP=1 -d "$NEON_OWNER_URL" <<SQL
select current_user as owner \gset
-- Postgres 16 and later: to make a database owned by a role, its maker
-- must be able to become it.
grant '"$APP_ROLE"' to :"owner";
create database warehouse;
create database app owner '"$APP_ROLE"';
SQL
    psql -X -v ON_ERROR_STOP=1 -d "$(owner_on warehouse)" <<SQL
select current_user as owner \gset
revoke connect on database warehouse from public;
revoke temp on database warehouse from public;
grant connect on database warehouse to '"$READER"';
grant usage on schema public to '"$READER"';
-- No tables yet: the default privileges do the work for what the load makes.
alter default privileges for role :"owner" in schema public grant select on tables to '"$READER"';
SQL
    psql -X -v ON_ERROR_STOP=1 -d "$(owner_on app)" <<SQL
-- pgvector, by the owner: a role made by SQL may not create extensions.
create extension if not exists vector;
revoke connect on database app from public;
grant connect on database app to '"$APP_ROLE"';
SQL
    psql -X -v ON_ERROR_STOP=1 -d "$NEON_OWNER_URL" -c "\l+ (warehouse|app)" -c "\du (cartograph_app|cartograph_reader)"
  '
  ;;

revoke-temp)
  # Added after databases had run on Neon (the owner's ruling, 1 November
  # 2026); databases now does it too.
  in_pg 'psql -X -v ON_ERROR_STOP=1 -d "$(owner_on warehouse)" -c "revoke temp on database warehouse from public" -c "\l warehouse"'
  ;;

whoami)
  in_pg '
    psql -X -At -v ON_ERROR_STOP=1 -d "$APP_DATABASE_URL" -c "select current_user, current_database()"
    psql -X -At -v ON_ERROR_STOP=1 -d "$WAREHOUSE_DATABASE_URL" -c "select current_user, current_database()"
  '
  ;;

load)
  # From the measurement database, which holds the measured sf=0.25 in
  # memory, straight into Neon: one dump, one restore, in one transaction,
  # so a failure leaves nothing. The data crosses the network as COPY text,
  # about the CSV's 258 MB. Owner and grants are Neon's, not the laptop's.
  set -o pipefail
  "${MEASURE[@]}" exec -T postgres sh -c 'exec pg_dump -Fc -U "$POSTGRES_USER" -d "$WAREHOUSE_DB_NAME"' |
    in_pg 'pg_restore --no-owner --no-acl --single-transaction --exit-on-error -d "$(owner_on warehouse)"'
  echo "neon: restore exit 0"
  in_pg '
    psql -X -At -v ON_ERROR_STOP=1 -d "$(owner_on warehouse)" <<SQL
grant select on all tables in schema public to '"$READER"';
analyze;
select count(*) || '"'"' tables'"'"' from pg_tables where schemaname = '"'"'public'"'"';
select pg_size_pretty(pg_database_size(current_database())) || '"'"' ('"'"' || pg_database_size(current_database()) || '"'"' bytes)'"'"';
SQL
  '
  ;;

app)
  # Migrations to head as the app role, from the backend image.
  docker run --rm --network "$NETWORK" --env-file "$ENV_FILE" -e REDIS_URL=redis://unused \
    -v "$(pwd)/backend:/app" -w /app cartograph-backend alembic upgrade head
  # The stored snapshot and its vectors, copied from the local store, so
  # nothing is embedded again: the three schema tables, ids and is_current
  # as they are.
  set -o pipefail
  docker compose exec -T postgres sh -c \
    'exec pg_dump --data-only --no-owner --no-acl -t schema_snapshots -t schema_elements -t schema_edges -U "$POSTGRES_USER" -d "$POSTGRES_DB"' |
    in_pg 'psql -X -q -v ON_ERROR_STOP=1 --single-transaction -d "$APP_DATABASE_URL"'
  # Each id sequence past its highest id.
  in_pg '
    psql -X -At -v ON_ERROR_STOP=1 -d "$APP_DATABASE_URL" <<SQL
select setval(pg_get_serial_sequence('"'"'schema_snapshots'"'"', '"'"'id'"'"'), (select max(id) from schema_snapshots));
select setval(pg_get_serial_sequence('"'"'schema_elements'"'"', '"'"'id'"'"'), (select max(id) from schema_elements));
select setval(pg_get_serial_sequence('"'"'schema_edges'"'"', '"'"'id'"'"'), (select max(id) from schema_edges));
select '"'"'snapshots '"'"' || count(*) || '"'"', current '"'"' || count(*) filter (where is_current) from schema_snapshots;
select '"'"'elements '"'"' || count(*) || '"'"', with a vector '"'"' || count(embedding) from schema_elements;
select '"'"'edges '"'"' || count(*) from schema_edges;
SQL
  '
  ;;

check)
  # Sizes, as the owner.
  in_pg '
    psql -X -At -v ON_ERROR_STOP=1 -d "$NEON_OWNER_URL" -c "
      select datname || '"'"' '"'"' || pg_database_size(datname) || '"'"' bytes ('"'"' || pg_size_pretty(pg_database_size(datname)) || '"'"')'"'"'
      from pg_database where datname in ('"'"'warehouse'"'"', '"'"'app'"'"', '"'"'neondb'"'"') order by datname"
  '
  # Row counts against what DuckDB generated, as the reader.
  in_pg 'psql -X -At -v ON_ERROR_STOP=1 -d "$WAREHOUSE_DATABASE_URL"' < <(
    sed -n 's/^\([a-z_]*\) \([0-9]*\)$/select case when count(*) = \2 then '"'"'match     \1 \2'"'"' else '"'"'MISMATCH  \1 '"'"' || count(*) || '"'"' not \2'"'"' end from \1;/p' \
      data/tpcds/sf0.25/rowcounts.txt)
  # The reader refuses a write, and the timeout cancels a long statement.
  in_pg '
    if psql -X -At -d "$WAREHOUSE_DATABASE_URL" -c "delete from store" > /tmp/out 2>&1; then echo "WRITE ALLOWED"; else echo "write refused: $(grep -o "ERROR: .*" /tmp/out)"; fi
    if psql -X -At -d "$WAREHOUSE_DATABASE_URL" -c "create table smuggled (i int)" > /tmp/out 2>&1; then echo "CREATE ALLOWED"; else echo "create refused: $(grep -o "ERROR: .*" /tmp/out)"; fi
    # Read-only switched off by the reader itself: the missing grant is the second line (NFR-07).
    if psql -X -At -d "$WAREHOUSE_DATABASE_URL" -c "set default_transaction_read_only = off" -c "delete from store" > /tmp/out 2>&1; then echo "WRITE ALLOWED READ-WRITE"; else echo "write refused read-write: $(grep -o "ERROR: .*" /tmp/out)"; fi
    if psql -X -At -d "$WAREHOUSE_DATABASE_URL" -c "create temporary table scratch (i int)" > /tmp/out 2>&1; then echo "TEMP ALLOWED"; else echo "temp refused: $(grep -o "ERROR: .*" /tmp/out)"; fi
    if psql -X -At -d "$WAREHOUSE_DATABASE_URL" -c "set default_transaction_read_only = off" -c "create temporary table scratch (i int)" > /tmp/out 2>&1; then echo "TEMP ALLOWED READ-WRITE"; else echo "temp refused read-write: $(grep -o "ERROR: .*" /tmp/out)"; fi
    psql -X -At -d "$WAREHOUSE_DATABASE_URL" -c "show statement_timeout" -c "show default_transaction_read_only"
    start=$(date +%s)
    if psql -X -At -d "$WAREHOUSE_DATABASE_URL" -c "select pg_sleep(31)" > /tmp/out 2>&1; then echo "SLEEP RAN TO THE END"; else echo "pg_sleep(31) cancelled after $(( $(date +%s) - start ))s: $(grep -o "ERROR: .*" /tmp/out)"; fi
  '
  # The hash of the live warehouse and of the stored snapshot, and the
  # examples' pinned SQL, with no model.
  docker run --rm --network "$NETWORK" --env-file "$ENV_FILE" -e REDIS_URL=redis://unused \
    -e WAREHOUSE_OVERLAY_PATH=overlays/tpcds.yaml -e PYTHONPATH=/app:/app/tests \
    -v "$(pwd)/backend:/app" -w /app cartograph-backend python -m app.check_deploy
  ;;

accounts)
  # As the application's role, which owns the app database. Counts only,
  # unless --yes; app.clean_accounts matches two exact patterns and never
  # the local user.
  case "${2:-}" in ""|--yes) ;; *) echo "usage: $0 accounts [--yes]" >&2; exit 2 ;; esac
  docker run --rm --network "$NETWORK" --env-file "$ENV_FILE" -e REDIS_URL=redis://unused \
    -v "$(pwd)/backend:/app" -w /app cartograph-backend python -m app.clean_accounts ${2:+"$2"}
  ;;

declared)
  # Item 41: scripts/declared.sql, SELECTs only, as the reader, whose
  # transactions are read-only by default. Changes nothing, and no overlay.
  in_pg 'psql -X -At -v ON_ERROR_STOP=1 -d "$WAREHOUSE_DATABASE_URL"' < scripts/declared.sql
  ;;

*)
  echo "usage: $0 roles|databases|revoke-temp|whoami|load|app|check|accounts|declared" >&2
  exit 2
  ;;
esac
