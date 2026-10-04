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
