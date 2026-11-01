"""The deployed databases checked from the laptop, with no model (step 12):

    docker run ... --env-file .env.deploy ... python -m app.check_deploy

  HASH       the live warehouse's snapshot hash, ingested as the reader, and
             the stored current snapshot's: each the same as the local one
             (the brief: if the hash differs, stop).
  GUARD      the executor's own check of the reader: a statement_timeout,
             read-only, not a superuser (NFR-07, NFR-11).
  EXAMPLE    each page example's pinned SQL (tests/test_examples_chosen.py)
             run as the reader: how many rows it returns. A decline has none.

Prints counts, hashes and the role's settings; never a URL.
"""

import sys

import psycopg
from test_examples_chosen import LIVE

from app.config import get_settings
from app.core.sql_reading import schema_of
from app.core.sql_validator import validate
from app.shell.query_executor import QueryExecutor
from app.shell.schema_ingestor import SchemaIngestor
from app.shell.snapshot_store import SnapshotStore, snapshot_hash

# The four on the page (frontend/lib/ask/examples.ts); LIVE also holds the
# retired gender question, which is not checked here.
PAGE = (
    "Where are our catalog customers located?",
    "Which five states spent the most on the web?",
    "How many customers were born in each country?",
    "Which of our suppliers deliver late most often?",
)

# The local stored snapshot's hash, read at step 12 (CHECKPOINTS).
LOCAL = "4e674c8f16283c81f47a62b3976aa19b1e42a81e2d29ad3726a13023e4302547"


def main() -> int:
    settings = get_settings()
    failed = 0

    snapshot = SchemaIngestor(settings.warehouse_database_url, settings.warehouse_overlay_path).ingest()
    live = snapshot_hash(snapshot)
    stored = SnapshotStore(settings.app_database_url).current().hash
    for name, value in (("live warehouse", live), ("stored snapshot", stored)):
        same = value == LOCAL
        failed += not same
        print(f"HASH {'PASS' if same else 'FAIL'}  {name} {value}")

    execution = QueryExecutor(settings.warehouse_database_url).run(validate("SELECT 1 AS one FROM store", schema_of(snapshot)).validated)
    print(f"GUARD {'PASS' if execution.ok else 'FAIL'}  statement_timeout {execution.statement_timeout}; {execution.message or 'read-only, not a superuser'}")
    failed += not execution.ok

    with psycopg.connect(settings.warehouse_database_url) as connection:
        for question in PAGE:
            sql = LIVE[question][3]
            if sql is None:
                print(f"EXAMPLE SKIP  {question}  a decline, no SQL")
                continue
            rows = connection.execute(f"select count(*) from ({sql}) as pinned").fetchone()[0]
            failed += rows == 0
            print(f"EXAMPLE {'PASS' if rows else 'FAIL'}  {question}  {rows} rows")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
