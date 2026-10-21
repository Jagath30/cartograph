"""One question through the orchestrator with a scripted model and no
database, and the trace document assembled from it: for tests of the
document, the narrative, the store and the API.

The schema and graph are TPC-DS from the committed files. The tree is
built by the real JoinTree from anchors named by the test; every statement
the "model" writes was written by hand.
"""

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from core.tpcds_files import OVERLAY, ddl_snapshot

from app.core.explainer import explain_tree
from app.core.graph_builder import build_graph
from app.core.join_tree import DEFAULT_SUBGRAPH_BOUND, build_tree
from app.core.overlay import apply_overlay, parse_overlay
from app.core.path_finder import DEFAULT_MAX_JOINS
from app.core.trace_document import Context, TraceDocument, assemble, preferences_hash
from app.orchestrator import MAX_RETRIES, Orchestrator
from app.shell.model_client import FakeModel
from app.shell.query_executor import Execution

SNAPSHOT = apply_overlay(ddl_snapshot(), parse_overlay(OVERLAY.read_text()))
GRAPH = build_graph(SNAPSHOT)
CONTEXT = Context(SNAPSHOT, GRAPH, preferences_hash(SNAPSHOT), DEFAULT_SUBGRAPH_BOUND, DEFAULT_MAX_JOINS, "fake-scripted", 0.0)
QUERY_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")
USER_ID = 1
WHEN = datetime(2026, 10, 10, 12, 0, tzinfo=UTC)
SCHEMA_REF = (7, "abc123")


@dataclass
class Located:
    tree: object
    explanation: object
    declined: bool = False
    decline_reason: str | None = None
    retrieval: object = None


def located(*anchors: str, **tree_arguments) -> Located:
    scores = {anchor: 1.0 - position / 10 for position, anchor in enumerate(anchors)}
    tree = build_tree(GRAPH, anchors, scores, **tree_arguments)
    return Located(tree, explain_tree(tree, GRAPH), tree.decline_reason == "anchors_not_connected", tree.decline_reason)


class Runs:
    """Stands where the executor would and keeps what it was handed."""

    def __init__(self, rows: int = 2, failure: str | None = None, truncated: bool = False) -> None:
        self.handed: list = []
        self.rows = rows
        self.failure = failure
        self.truncated = truncated

    def run(self, validated) -> Execution:
        self.handed.append(validated)
        if self.failure:
            return Execution(executed=validated.text, failure=self.failure, sqlstate="22012", message="division by zero")
        return Execution(
            executed=validated.text, columns=("a",), rows=tuple((n,) for n in range(self.rows)),
            truncated=self.truncated, statement_timeout="30s",
        )  # fmt: skip


def reply(sql: str = "", status: str = "sql") -> str:
    return json.dumps({"status": status, "sql": sql})


def orchestrator(script, where: Located, executor=None, retries: int = MAX_RETRIES, **keep) -> Orchestrator:
    return Orchestrator(SNAPSHOT, SCHEMA_REF, lambda asked: where, FakeModel(script), executor or Runs(), retries, **keep)


def trace_of(script, *anchors, executor=None, question="A question?", where: Located | None = None, **tree_arguments):
    where = where or located(*anchors, **tree_arguments)
    return orchestrator(script, where, executor).answer(question)


def document(script, *anchors, **arguments) -> TraceDocument:
    return assemble(trace_of(script, *anchors, **arguments), CONTEXT, QUERY_ID, USER_ID, WHEN)


def linked_trace(link_script, generation_script, question="A question?", choose_script=None, executor=None,
                 schema_ref=SCHEMA_REF, **tree_arguments):
    """Step 10b: a question through the link stage, and the choose stage
    when `choose_script` is given, over scripted models. The tree is the
    real JoinTree's over the tables the linking reply names."""
    from app.core.choosing import build_choose_prompt
    from app.core.linking import LINK_REPLY_NAME, build_link_prompt, link_reply_schema
    from app.orchestrator import Chooser, Linker
    from app.shell.model_client import ReplyShape

    linker = Linker(
        prompt=lambda asked: build_link_prompt(asked, SNAPSHOT, GRAPH),
        shape=ReplyShape(LINK_REPLY_NAME, link_reply_schema(SNAPSHOT)),
        known=frozenset(table.name for table in SNAPSHOT.tables),
        model=FakeModel(link_script),
        locate=lambda asked, named: located(*named, **tree_arguments),
    )
    chooser = None
    if choose_script is not None:
        chooser = Chooser(
            prompt=lambda asked, choices: build_choose_prompt(asked, choices, GRAPH),
            model=FakeModel(choose_script),
            relocate=lambda asked, named, chosen: located(*named, chosen=chosen, **tree_arguments),
        )
    orchestrator = Orchestrator(SNAPSHOT, schema_ref, None, FakeModel(generation_script), executor or Runs(),
                                linker=linker, chooser=chooser)  # fmt: skip
    return orchestrator.answer(question)


def linked_document(*arguments, **keywords) -> TraceDocument:
    return assemble(linked_trace(*arguments, **keywords), CONTEXT, QUERY_ID, USER_ID, WHEN)
