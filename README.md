# Cartograph

Natural-language querying of complex data warehouses with schema-graph retrieval and explainable join-path selection.

## Running it locally

Requires Docker Desktop with the WSL2 backend.

    ./scripts/bootstrap.sh
    docker compose up

`bootstrap.sh` writes a `.env` with generated passwords if one does not
already exist, and never overwrites an existing file. The repository
contains no credentials of any kind, in the working tree or in its
history, so this step is what makes `docker compose up` work on a clean
clone.

Once up:

- Frontend: http://localhost:3000
- API: http://127.0.0.1:8000/api/v1/health and /api/v1/ready

## Loading the warehouse

The stack comes up with an empty warehouse. One command builds it:

    ./scripts/warehouse.sh          # TPC-DS at scale factor 1, about five minutes
    ./scripts/warehouse.sh 0.01     # the same mechanism in thirty seconds

It needs the `duckdb` CLI and `python3` on the host. It generates TPC-DS
with DuckDB's `tpcds` extension, loads the 24 tables with `COPY`, adds the
17 primary keys, and applies the 102 foreign keys from the TPC toolkit's
`tpcds_ri.sql` — which is also parsed into `backend/overlays/tpcds.yaml`,
the source the schema graph is built from. Running it again drops and
rebuilds the warehouse tables; it never touches the application database.

`tpcds_ri.sql` belongs to the TPC and is not in this repository. The first
run downloads it with `curl` from a commit-pinned mirror of the toolkit and
checks its sha256; a failed download or a different file stops the build
before anything is changed. The overlay generated from it is committed.

At scale factor 1 the loaded warehouse measures 2.2 GB.

## Reading the schema

Once a warehouse is loaded, this prints what the application makes of it:

    docker compose exec backend python -m app.show_schema

It connects as the SELECT-only role, reads tables, columns and keys from
`pg_catalog`, merges the overlay (catalog first, with the source recorded
on every edge), and builds the schema graph: 24 tables and 425 columns as
nodes, 107 foreign keys as edges. 102 of those the database declares. The
other 5 are relationships the TPC's constraint file omits, including the
two-column keys that tie a return to its sale; they are asserted in the
overlay, marked `source: overlay`, and each carries a note saying what
evidence it rests on.

Column names such as `ss_ext_sales_price` are given readable names from the
overlay's `naming` section. That section is written by hand in
`backend/overlays/tpcds.naming.yaml`, and the hand-declared relationships
in `backend/overlays/tpcds.relationships.yaml`;
`backend/overlays/tpcds.yaml` is generated from them and never edited.
After changing either file:

    python3 backend/warehouse/ri.py write-overlay

## Finding join paths

    docker compose exec backend python -m app.show_paths catalog_sales customer_address

prints every route of up to three joins between two tables, the one
selected, and why. The rule is shortest first; a tie is broken by a
preference declared in the overlay if there is one, and alphabetically if
not. An alphabetical choice is reported as arbitrary, and the warning names
the routes that were not taken. A selected route that joins two "many"
sides through one table says so, and says what it does to the rows.
`--all` lists every alternative; `--max-joins 4` raises the limit.

## Retrieval: from a question to the tables that answer it

    docker compose exec backend python -m app.ingest_schema
    docker compose exec backend python -m app.show_retrieval "How much did each store sell last year?"

The first stores the warehouse's schema in the application database and
embeds it, once; it needs `OPENAI_API_KEY` in `.env`. The second puts one
question through retrieval: what its words matched, which tables became
anchors and which were set aside or cut, the tree that joins them and how
each was attached, every warning, and the close calls. A question that has
not been asked before is embedded, which is a paid call of a few dozen
tokens; the last line says what the run cost.

    docker compose exec backend python -m app.show_eval --step6

runs the sixteen questions of the frozen evaluation set
(`backend/eval/questions.yaml`) and reports where retrieval agrees with
them and where it does not. At the settings in use it holds every expected
table for 11 of 15 questions and brings 34 tables too many; it agrees
strictly with none. `CHECKPOINTS.md` has the whole record.
