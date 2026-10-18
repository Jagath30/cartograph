"""Step 10b: linking. One model call names the tables a question needs,
and from those tables on v1 runs unchanged (the owner's rulings at stop A,
18 October 2026).

Pure (DD-01). The model call itself is the orchestrator's; this module
holds what is sent, what may come back, and what is done with it.

    LINK_RULES         the linking call's system message, the owner's
                       wording, word for word
    build_link_prompt  that, beside the schema exactly as step 10's
                       full-schema prompt renders it (the 24 tables, the
                       107 keys) and the question: the user message IS the
                       full-schema prompt's, byte for byte
    link_reply_schema  the reply's strict shape, its enum of table names
                       built from the snapshot at run time, never by hand
    parse_link_reply   the reply read strictly: names in order, none
                       repeated, every one a table of the snapshot. No
                       tables is the decline, not a fault
    locate_linked      locate, with the anchors named rather than
                       retrieved. Retrieval still scores the question: its
                       table scores order the anchors and its column scores
                       are the wording's evidence for DD-12, so path
                       selection is v1's. No rival is set aside: rivals are
                       how retrieval chooses anchors, and linking chose them

WHAT LINKING DOES AND DOES NOT DECIDE. It names a query's tables, bridges
included: those are what the evaluation's expectations list. What remains
to Cartograph is the choice of keys between them, where DD-12's order
(shortest, the wording, a declared preference, the alphabet) and the
warnings apply.
"""

import json
from dataclasses import dataclass
from typing import Literal

import networkx as nx

from app.core.explainer import explain_tree
from app.core.full_schema import build_full_prompt
from app.core.graph_builder import joined_tables, table_nodes
from app.core.join_tree import ANCHORS_NOT_CONNECTED, DEFAULT_SUBGRAPH_BOUND, build_tree
from app.core.locate import Located
from app.core.path_finder import DEFAULT_MAX_JOINS
from app.core.prompt_builder import Prompt
from app.core.retriever import RawScore, Settings, Term, retrieve
from app.core.snapshot import SchemaSnapshot

LINK_RULES = """\
You name the tables of a data warehouse that a question needs.

You are given the tables as CREATE TABLE statements, then the keys by which they can be joined. The comment above each table and after each column says what it holds.

Rules:
- Name every table a query answering the question would read, including a table needed only to join two others.
- Name no other table.
- Use only the table names given.
- If the question cannot be answered from these tables, do not guess: name none.

Reply with one JSON object and nothing else:
  {"tables": ["<table name>", ...]}
or, when the question cannot be answered from these tables:
  {"tables": []}
Do not explain."""

LINK_REPLY_NAME = "link_reply"

# The linking call's output cap: room for every table named at once, at
# fewer characters a token than any text takes (a test holds it to that).
LINK_MAX_OUTPUT_TOKENS = 400


def build_link_prompt(question: str, snapshot: SchemaSnapshot, graph: nx.DiGraph) -> Prompt:
    full = build_full_prompt(question, snapshot, graph)
    return Prompt(LINK_RULES, full.user, full.tables, False)


def link_reply_schema(snapshot: SchemaSnapshot) -> dict:
    names = sorted(table.name for table in snapshot.tables)
    return {
        "type": "object",
        "properties": {"tables": {"type": "array", "items": {"type": "string", "enum": names}}},
        "required": ["tables"],
        "additionalProperties": False,
    }


@dataclass(frozen=True)
class LinkReply:
    kind: Literal["tables", "none", "malformed"]
    tables: tuple[str, ...] = ()
    # For a malformed reply: what was wrong with it, in fixed words.
    fault: str | None = None


def parse_link_reply(text: str, known: frozenset[str]) -> LinkReply:
    """Read strictly. Nothing is repaired: no code fence stripped, no name
    corrected, no repeat quietly dropped."""
    try:
        reply = json.loads(text)
    except ValueError:
        return LinkReply("malformed", fault="the reply is not JSON")
    if not isinstance(reply, dict) or set(reply) != {"tables"}:
        return LinkReply("malformed", fault="the reply does not have exactly the field tables")
    tables = reply["tables"]
    if not isinstance(tables, list) or not all(isinstance(name, str) for name in tables):
        return LinkReply("malformed", fault="tables is not a list of table names")
    for name in tables:
        if name not in known:
            return LinkReply("malformed", fault=f"it names a table not given: {name}")
    seen: set[str] = set()
    for name in tables:
        if name in seen:
            return LinkReply("malformed", fault=f"it names a table twice: {name}")
        seen.add(name)
    if not tables:
        return LinkReply("none")
    return LinkReply("tables", tuple(tables))


@dataclass(frozen=True)
class LinkedLocated(Located):
    """A located question whose anchors were named by linking, in the
    order linking named them. `retrieval` is still the question's scoring."""

    linked: tuple[str, ...] = ()


def locate_linked(
    question: str,
    linked: tuple[str, ...],
    scores: tuple[RawScore, ...],
    terms: tuple[tuple[Term, tuple[RawScore, ...]], ...],
    graph: nx.DiGraph,
    settings: Settings,
    max_joins: int = DEFAULT_MAX_JOINS,
    subgraph_bound: int = DEFAULT_SUBGRAPH_BOUND,
) -> LinkedLocated:
    """`linked` is never empty: no tables is the decline, decided before
    anything is located."""
    if not linked:
        raise ValueError("no tables were linked; a decline is never located")
    unknown = sorted(set(linked) - set(table_nodes(graph)))
    if unknown:
        raise ValueError(f"linked tables not in the graph: {unknown}")
    retrieval = retrieve(question, scores, terms, settings, partners=joined_tables(graph))
    tree = build_tree(
        graph,
        linked,
        {table: retrieval.score_of(table) for table in linked},
        evidence=retrieval.column_scores,
        margin=settings.margin,
        set_aside=(),
        max_joins=max_joins,
        subgraph_bound=subgraph_bound,
    )
    declined = tree.decline_reason == ANCHORS_NOT_CONNECTED
    return LinkedLocated(
        retrieval, tree, explain_tree(tree, graph), declined, tree.decline_reason if declined else None, linked
    )
