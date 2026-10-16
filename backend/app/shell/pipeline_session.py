"""Everything a command needs before it can put a question through the
whole pipeline: retrieval as `show_retrieval` opens it, the model behind
the spend ledger, the executor, and the orchestrator over them.

Shared by `app.ask`, `app.run_dev` and the API, so that no two of them
can run a question slightly differently. Nothing here decides anything.

Step 10 opens it in either of its two modes. `retrieval` is the pipeline
the application runs. `full_schema` differs only in what Cartograph
contributes: nothing is retrieved and nothing is selected, and the prompt
holds every table and every key (app.core.full_schema). The model, the
ledger, the retries, the validator, the checker and the executor are the
same objects of the same kinds in both.

Given a store (step 8), the orchestrator is also told how to keep a
trace: where, whose, and the facts its own trace does not hold -- the
graph for readable names, the hash of the preferences in force, the
bounds and the model. Without one it is exactly as it was at step 7.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

import networkx as nx
from app.config import get_settings
from app.core.full_schema import build_full_prompt, full_schema_located
from app.core.graph_builder import build_graph
from app.core.join_tree import DEFAULT_SUBGRAPH_BOUND
from app.core.locate import locate
from app.core.path_finder import DEFAULT_MAX_JOINS
from app.core.prompt_builder import build_prompt
from app.core.snapshot import SchemaSnapshot
from app.core.trace_document import Context, preferences_hash
from app.orchestrator import Keep, Orchestrator
from app.shell.model_client import LEDGER, LedgeredModel, ModelClient, SpendLedger, make_model
from app.shell.query_executor import QueryExecutor
from app.shell.retrieval_session import Opened, open_retrieval
from app.shell.schema_ingestor import SchemaIngestor

# Step 7's development work stops itself here, well inside its $1.
CEILING_USD = 0.50


Mode = Literal["retrieval", "full_schema"]


@dataclass(frozen=True)
class Pipeline:
    orchestrator: Orchestrator
    retrieval: Opened
    ledger: SpendLedger
    model: str
    snapshot: SchemaSnapshot
    graph: nx.DiGraph
    mode: Mode = "retrieval"


def open_pipeline(
    name: str,
    purpose: str,
    alpha: float = 0.5,
    cut: float = 0.5,
    cap: int = 5,
    store=None,
    mode: Mode = "retrieval",
    wrap_model: Callable[[ModelClient], ModelClient] | None = None,
) -> Pipeline:
    """`name` begins every message; `purpose` is written beside each model
    call in the ledger. `store` is a TraceStore, or None to keep nothing.
    `wrap_model` wraps the ledgered model (step 10's transport retry).
    Raises ModelKeyMissing when there is no key."""
    settings = get_settings()
    snapshot = SchemaIngestor(settings.warehouse_database_url, settings.warehouse_overlay_path).ingest()
    graph = build_graph(snapshot)
    opened = open_retrieval(snapshot, alpha, cut, cap, name, purpose)
    ledger = SpendLedger(LEDGER)
    model: ModelClient = LedgeredModel(make_model(settings), ledger, purpose, CEILING_USD)
    if wrap_model is not None:
        model = wrap_model(model)

    def retrieve(question: str):
        scored = opened.index.score_question(question)
        return locate(question, scored.scores, scored.terms, graph, opened.settings)

    everything = full_schema_located(snapshot)

    def whole_schema(question: str):
        # Nothing is retrieved in this mode, and nothing selected.
        return everything

    def build_whole(question, snapshot, tables, joins):
        return build_full_prompt(question, snapshot, graph)

    if mode not in ("retrieval", "full_schema"):
        raise ValueError(f"no such mode: {mode!r}")
    find, build = (retrieve, build_prompt) if mode == "retrieval" else (whole_schema, build_whole)

    keep = None
    if store is not None:
        context = Context(
            snapshot, graph, preferences_hash(snapshot), DEFAULT_SUBGRAPH_BOUND, DEFAULT_MAX_JOINS,
            settings.sql_model, settings.sql_model_temperature,
        )  # fmt: skip
        keep = Keep(store, context, store.local_user_id())
    orchestrator = Orchestrator(
        snapshot, (opened.stored.id, opened.stored.hash), find, model, QueryExecutor(settings.warehouse_database_url),
        keep=keep, build=build,
    )  # fmt: skip
    return Pipeline(orchestrator, opened, ledger, settings.sql_model, snapshot, graph, mode)
