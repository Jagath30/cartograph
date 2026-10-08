"""The step 5 report is still the step 5 report (DR-16, SRS threat T-04).

How a result is judged is part of the frozen instrument. Step 6 adds a
second judgement beside the first and extends the runner; neither may move
a line of what step 5 reported. `eval/baseline_step5.txt` is the output of
`python -m app.show_eval` on the live warehouse at the commit tagged
checkpoint-05-eval, identical to the baseline recorded in CHECKPOINTS.md,
and the report is compared with it byte for byte.

Needs the warehouse, so it is skipped in CI, like every test that reads it.
"""

import os
from pathlib import Path

import psycopg
import pytest

from app import show_eval

BASELINE = Path(__file__).resolve().parents[1] / "eval" / "baseline_step5.txt"


@pytest.fixture(scope="module")
def warehouse() -> None:
    dsn = os.environ.get("WAREHOUSE_DATABASE_URL")
    if not dsn:
        pytest.skip("WAREHOUSE_DATABASE_URL is not set")
    try:
        with psycopg.connect(dsn, connect_timeout=3) as connection:
            loaded = connection.execute("select count(*) from pg_tables where schemaname = 'public'").fetchone()[0]
    except psycopg.OperationalError:
        pytest.skip("the warehouse is not reachable")
    if loaded == 0:
        pytest.skip("the warehouse is empty -- run ./scripts/warehouse.sh")


def test_the_step_5_report_is_byte_for_byte_the_baseline(warehouse, capsys) -> None:
    show_eval.main()
    assert capsys.readouterr().out == BASELINE.read_text()
