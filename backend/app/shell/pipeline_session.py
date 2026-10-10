"""Everything a command needs before it can put a question through the
whole pipeline: retrieval as `show_retrieval` opens it, the model behind
the spend ledger, the executor, and the orchestrator over them.

Shared by `app.ask`, `app.run_dev` and the API, so that no two of them
can run a question slightly differently. Nothing here decides anything.

Given a store (step 8), the orchestrator is also told how to keep a
trace: where, whose, and the facts its own trace does not hold -- the
graph for readable names, the hash of the preferences in force, the
bounds and the model. Without one it is exactly as it was at step 7.
"""

from dataclasses import dataclass
from pathlib import Path

from app.config import get_settings
from app.core.graph_builder import build_graph
from app.core.join_tree import DEFAULT_SUBGRAPH_BOUND
from app.core.locate import locate
from app.core.path_finder import DEFAULT_MAX_JOINS
from app.core.trace_document import Context, preferences_hash
from app.orchestrator import Keep, Orchestrator
from app.shell.model_client import LedgeredModel, SpendLedger, make_model
from app.shell.query_executor import QueryExecutor
from app.shell.retrieval_session import Opened, open_retrieval
from app.shell.schema_ingestor import SchemaIngestor

LEDGER = Path(__file__).resolve().parents[2] / "eval" / ".spend" / "model_calls.jsonl"
# Step 7's development work stops itself here, well inside its $1.
CEILING_USD = 0.50


@dataclass(frozen=True)
class Pipeline:
    orchestrator: Orchestrator
    retrieval: Opened
    ledger: SpendLedger
    model: str


def open_pipeline(
    name: str, purpose: str, alpha: float = 0.5, cut: float = 0.5, cap: int = 5, store=None
) -> Pipeline:
    """`name` begins every message; `purpose` is written beside each model
    call in the ledger. `store` is a TraceStore, or None to keep nothing.
    Raises ModelKeyMissing when there is no key."""
    settings = get_settings()
    snapshot = SchemaIngestor(settings.warehouse_database_url, settings.warehouse_overlay_path).ingest()
    graph = build_graph(snapshot)
    opened = open_retrieval(snapshot, alpha, cut, cap, name)
    ledger = SpendLedger(LEDGER)
    model = LedgeredModel(make_model(settings), ledger, purpose, CEILING_USD)

    def find(question: str):
        scored = opened.index.score_question(question)
        return locate(question, scored.scores, scored.terms, graph, opened.settings)

    keep = None
    if store is not None:
        context = Context(
            snapshot, graph, preferences_hash(snapshot), DEFAULT_SUBGRAPH_BOUND, DEFAULT_MAX_JOINS,
            settings.sql_model, settings.sql_model_temperature,
        )  # fmt: skip
        keep = Keep(store, context, store.local_user_id())
    orchestrator = Orchestrator(
        snapshot, (opened.stored.id, opened.stored.hash), find, model, QueryExecutor(settings.warehouse_database_url),
        keep=keep,
    )
    return Pipeline(orchestrator, opened, ledger, settings.sql_model)
