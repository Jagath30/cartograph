"""Everything a command needs before it can put a question through the
whole pipeline: retrieval as `show_retrieval` opens it, the model behind
the spend ledger, the executor, and the orchestrator over them.

Shared by `app.ask` and `app.run_dev`, so that the two cannot run a
question slightly differently. Nothing here decides anything.
"""

from dataclasses import dataclass
from pathlib import Path

from app.config import get_settings
from app.core.graph_builder import build_graph
from app.core.locate import locate
from app.orchestrator import Orchestrator
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


def open_pipeline(name: str, purpose: str, alpha: float = 0.5, cut: float = 0.5, cap: int = 5) -> Pipeline:
    """`name` begins every message; `purpose` is written beside each model
    call in the ledger. Raises ModelKeyMissing when there is no key."""
    settings = get_settings()
    snapshot = SchemaIngestor(settings.warehouse_database_url, settings.warehouse_overlay_path).ingest()
    graph = build_graph(snapshot)
    opened = open_retrieval(snapshot, alpha, cut, cap, name)
    ledger = SpendLedger(LEDGER)
    model = LedgeredModel(make_model(settings), ledger, purpose, CEILING_USD)

    def find(question: str):
        scored = opened.index.score_question(question)
        return locate(question, scored.scores, scored.terms, graph, opened.settings)

    orchestrator = Orchestrator(
        snapshot, (opened.stored.id, opened.stored.hash), find, model, QueryExecutor(settings.warehouse_database_url)
    )
    return Pipeline(orchestrator, opened, ledger, settings.sql_model)
