"""QUESTION_COST_BOUND_USD covers the most one question can cost, worked
out from the real prompts on the live schema (NFR-25, step 12). The
prompts are built and measured; no model is called.

The largest first prompt is the largest of: linking's (every table and
every key), and generation's shown every table and every join, which is
more than any tree selects; choosing's holds only the question and the
choices. A question at the API's longest is counted four characters to
the character, should it be in a script that tokenises densely.

Needs the loaded warehouse; skips without it, as in CI.
"""

import os
from pathlib import Path

import psycopg
import pytest

from app.api_models import MAX_QUESTION
from app.config import MODEL_PRICES, Settings
from app.core.budget import QUESTION_COST_BOUND_USD, question_cost_bound
from app.core.choosing import CHOOSE_MAX_OUTPUT_TOKENS, CHOOSE_RULES
from app.core.graph_builder import build_graph
from app.core.linking import LINK_MAX_OUTPUT_TOKENS, build_link_prompt
from app.core.path_finder import MANY_TO_ONE, Join
from app.core.prompt_builder import build_prompt
from app.orchestrator import MAX_RETRIES
from app.shell.schema_ingestor import SchemaIngestor

OVERLAY = Path(__file__).resolve().parents[1] / "overlays" / "tpcds.yaml"


@pytest.fixture(scope="module")
def snapshot():
    dsn = os.environ.get("WAREHOUSE_DATABASE_URL")
    if not dsn:
        pytest.skip("WAREHOUSE_DATABASE_URL is not set")
    try:
        return SchemaIngestor(dsn, OVERLAY).ingest()
    except psycopg.OperationalError:
        pytest.skip("the warehouse is not reachable")


def test_the_constant_covers_the_most_a_question_can_cost(snapshot) -> None:
    graph = build_graph(snapshot)
    question = "x" * MAX_QUESTION
    every_table = tuple(table.name for table in snapshot.tables)
    every_join = tuple(
        Join(key.from_table, key.from_columns, key.to_table, key.to_columns, key.source, key.name, MANY_TO_ONE)
        for key in snapshot.foreign_keys
    )
    prompts = (build_link_prompt(question, snapshot, graph), build_prompt(question, snapshot, every_table, every_join))
    largest = max(len(prompt.system) + len(prompt.user) for prompt in prompts) + 3 * MAX_QUESTION
    assert len(CHOOSE_RULES) < largest

    settings = Settings(app_database_url="unused", warehouse_database_url="unused", redis_url="unused")
    most_out = max(settings.sql_model_max_output_tokens, LINK_MAX_OUTPUT_TOKENS, CHOOSE_MAX_OUTPUT_TOKENS)
    # Linking, choosing and generation, and the retries they share.
    price = MODEL_PRICES[settings.sql_model]
    bound = question_cost_bound(largest, most_out, 3 + MAX_RETRIES, MAX_RETRIES, price.input, price.output)
    print(f"largest first prompt {largest} characters; the most a question can cost ${bound:.6f}")
    assert bound <= QUESTION_COST_BOUND_USD
